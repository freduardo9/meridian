"""Product usage events, generated month by month so the table lands in arrival order.

Volume is driven by who is actually paying: each customer-day draws active users
from its current seat count, scaled by the customer's engagement, the day of the
week, and its state (trial, paid, past-due, post-churn grace). Customers who are
about to cancel go quiet over the preceding weeks.

The target volume is hit by calibrating one global rate after a first pass, so
--usage-events is honoured regardless of how the lifecycle simulation played out.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Iterator

import numpy as np
import pandas as pd

from . import config as C
from .config import UTC

US_PER_DAY = 86_400_000_000
KIND_MULT = {"trial": 1.3, "paid": 1.0, "past_due": 0.8, "grace": 0.08}
WEEKDAY_MULT = np.array([1.0, 1.0, 1.0, 1.0, 0.95, 0.25, 0.20])  # Mon..Sun

EVENTS = ["login", "dashboard_viewed", "filter_applied", "report_created", "report_exported",
          "integration_synced", "api_call", "invite_sent", "settings_updated"]
BASE_P = np.array([.12, .36, .20, .08, .05, .07, .08, .015, .015])


def _event_probs(tier: int) -> np.ndarray:
    p = BASE_P.copy()
    if tier >= 4:
        p[6] *= 3.0     # Pro and Enterprise lean on the API
        p[5] *= 1.5
    return p / p.sum()


TIER_P = {t: _event_probs(t) for t in range(1, 6)}


def _us(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000)


class UsageGenerator:
    def __init__(self, sim, cfg):
        self.sim, self.cfg = sim, cfg
        self.rng = np.random.default_rng(cfg.seed + 7)
        self.win_start = datetime.combine(cfg.usage_start, time(), UTC)
        self.win_end = cfg.end_ts
        self.n_days = (self.win_end - self.win_start).days
        self.day0 = _us(self.win_start)
        self.noon = self.day0 + np.arange(self.n_days, dtype=np.int64) * US_PER_DAY + US_PER_DAY // 2
        self.weekday = np.array([(self.win_start + timedelta(days=d)).weekday() for d in range(self.n_days)])
        self.customers = self._profiles()
        self.rate = self._calibrate()

    # ------------------------------------------------------------------ pass 1

    def _profiles(self) -> list[dict]:
        users_by_cust: dict[int, list] = {}
        for u in self.sim.users:
            users_by_cust.setdefault(u[1], []).append(u)

        out = []
        for cid, segs in self.sim.segments.items():
            seats = np.zeros(self.n_days, dtype=np.int64)
            mult = np.zeros(self.n_days)
            tiers = np.ones(self.n_days, dtype=np.int64)
            hit = False
            for start, end, n, kind, tr in segs:
                if end <= self.win_start or start >= self.win_end:
                    continue
                m = (self.noon >= _us(start)) & (self.noon < _us(end))
                if m.any():
                    hit = True
                    seats[m] = np.maximum(seats[m], n)
                    mult[m] = KIND_MULT[kind]
                    tiers[m] = tr
            if not hit:
                continue
            for s in self.sim.churn_signals.get(cid, []):
                su = _us(s)
                m = (self.noon < su) & (self.noon >= su - 45 * US_PER_DAY)
                mult[m] *= 0.4 + 0.6 * (su - self.noon[m]) / (45 * US_PER_DAY)

            users = sorted(users_by_cust.get(cid, []), key=lambda u: u[5])
            if not users:
                continue
            created = np.array([_us(u[5]) for u in users], dtype=np.int64)
            deact = np.array([_us(u[6]) if u[6] else np.iinfo(np.int64).max for u in users], dtype=np.int64)
            eng = self.rng.beta(5, 3)
            p = np.clip(eng * 0.9 * WEEKDAY_MULT[self.weekday] * mult, 0, 1)
            p[seats == 0] = 0
            out.append(dict(
                cid=cid, seats=seats, p=p, tiers=tiers, lam=float(self.rng.lognormal(0.0, 0.5)),
                user_ids=np.array([u[0] for u in users], dtype=np.int64), created=created, deact=deact,
                eligible=np.searchsorted(created, self.noon, side="right"),
            ))
        return out

    def _calibrate(self) -> float:
        expected = sum(float((c["seats"] * c["p"]).sum()) * c["lam"] for c in self.customers)
        if expected == 0:
            return 0.0
        return self.cfg.usage_events / (expected * (1 + C.P_USAGE_DUPLICATE))

    # ------------------------------------------------------------------ pass 2

    def months(self) -> Iterator[pd.DataFrame]:
        d = 0
        while d < self.n_days:
            start_dt = self.win_start + timedelta(days=d)
            nxt = (start_dt.replace(day=1) + timedelta(days=32)).replace(day=1)
            d_end = min(d + (nxt - start_dt).days, self.n_days)
            df = self._month(d, d_end, month_index=(start_dt.year - self.win_start.year) * 12
                             + start_dt.month - self.win_start.month)
            if df is not None and len(df):
                yield df
            d = d_end

    def _month(self, d0: int, d1: int, month_index: int) -> pd.DataFrame | None:
        rng = self.rng
        parts = []
        for c in self.customers:
            seats = c["seats"][d0:d1]
            if not seats.any():
                continue
            k = rng.binomial(seats, c["p"][d0:d1])
            k = np.minimum(k, c["eligible"][d0:d1])
            n = rng.poisson(k * c["lam"] * self.rate)
            total = int(n.sum())
            if total == 0:
                continue
            day = np.repeat(np.arange(d0, d1), n)
            elig = c["eligible"][day]
            idx = rng.integers(0, np.maximum(elig, 1))
            secs = np.clip(rng.normal(15.5 * 3600, 3.5 * 3600, total), 0, 86_399.999)
            occurred = self.day0 + day.astype(np.int64) * US_PER_DAY + (secs * 1e6).astype(np.int64)
            # an event can't come from a deactivated user, or before the user existed
            gone = c["deact"][idx] <= occurred
            idx[gone] = 0                                   # fall back to the account owner
            occurred = np.maximum(occurred, c["created"][idx] + 60_000_000)
            tiers = c["tiers"][day]
            names = np.empty(total, dtype=np.int64)
            for t in np.unique(tiers):
                m = tiers == t
                names[m] = rng.choice(len(EVENTS), size=int(m.sum()), p=TIER_P[int(t)])
            parts.append((np.full(total, c["cid"], dtype=np.int64), c["user_ids"][idx], names, occurred))

        if not parts:
            return None
        cust = np.concatenate([p[0] for p in parts])
        user = np.concatenate([p[1] for p in parts])
        name = np.concatenate([p[2] for p in parts])
        occurred = np.concatenate([p[3] for p in parts])
        n = len(cust)

        # collector latency, late arrivals, client clock skew
        received = occurred + (rng.exponential(1.5, n) * 1e6).astype(np.int64) + 50_000
        late = rng.random(n) < C.P_USAGE_LATE
        received[late] += (rng.uniform(1, 72, late.sum()) * 3600e6).astype(np.int64)
        skew = rng.random(n) < C.P_USAGE_CLOCK_SKEW
        occurred[skew] += (rng.uniform(5, 90, skew.sum()) * 60e6).astype(np.int64)

        event_ids = np.array(_uuids(rng, n), dtype=object)
        ev = np.array(EVENTS, dtype=object)[name]
        client = np.where(ev == "api_call", "api",
                          np.where(ev == "integration_synced", "server",
                                   np.where(rng.random(n) < 0.15, "mobile", "web"))).astype(object)
        minor = month_index + 3 - (rng.random(n) < 0.12).astype(int)
        patch = rng.integers(0, 4, n)
        version = np.array([f"4.{a}.{b}" for a, b in zip(minor, patch)], dtype=object)
        version[(client == "api") | (client == "server")] = None

        # at-least-once delivery: some events land twice, identical except received_at
        dup = np.flatnonzero(rng.random(n) < C.P_USAGE_DUPLICATE)
        if len(dup):
            cols = [cust, user, ev, occurred, event_ids, client, version]
            cust, user, ev, occurred, event_ids, client, version = (np.concatenate([a, a[dup]]) for a in cols)
            received = np.concatenate([received, received[dup] + (rng.uniform(1, 6 * 3600, len(dup)) * 1e6).astype(np.int64)])

        keep = received < _us(self.win_end)       # not yet arrived as of the snapshot
        order = np.argsort(received[keep], kind="stable")
        sel = np.flatnonzero(keep)[order]

        return pd.DataFrame({
            "event_id": event_ids[sel],
            "customer_id": cust[sel],
            "user_id": user[sel],
            "event_name": ev[sel],
            "occurred_at": pd.to_datetime(occurred[sel], unit="us", utc=True),
            "received_at": pd.to_datetime(received[sel], unit="us", utc=True),
            "client": client[sel],
            "app_version": version[sel],
        })


def _uuids(rng: np.random.Generator, n: int) -> list[str]:
    b = np.frombuffer(rng.bytes(16 * n), dtype=np.uint8).reshape(n, 16).copy()
    b[:, 6] = (b[:, 6] & 0x0F) | 0x40   # version 4
    b[:, 8] = (b[:, 8] & 0x3F) | 0x80   # RFC 4122 variant
    h = b.tobytes().hex()
    return [f"{h[i:i+8]}-{h[i+8:i+12]}-{h[i+12:i+16]}-{h[i+16:i+20]}-{h[i+20:i+32]}"
            for i in range(0, 32 * n, 32)]
