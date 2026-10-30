"""Customer and subscription lifecycle simulation.

Walks each customer forward through time -- signup, trial, conversion, billing
periods, mid-period changes, cancellation, and sometimes a return -- and records
what the source systems would have written along the way.

Nothing here computes MRR or ARR. The data only records what happened: plans,
seats, prices, dates, invoices. Deriving revenue metrics from that is the
Phase 1 exercise.
"""

from __future__ import annotations

import calendar
import itertools
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import config as C
from .config import UTC, Config
from .names import NameFactory

DAY = timedelta(days=1)
HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)
SECOND = timedelta(seconds=1)

INDUSTRIES = ["software", "fintech", "healthcare", "retail", "manufacturing", "logistics",
              "education", "media", "professional_services", "nonprofit"]
BANDS = ["1-10", "11-50", "51-200", "201-1000", "1001-5000", "5000+"]
SEGMENT_BANDS = {"smb": (0, 1), "mid_market": (2, 3), "enterprise": (4, 5)}
COUNTRIES = [  # (code, region, weight)
    ("US", "NA", .55), ("CA", "NA", .07), ("GB", "EMEA", .09), ("DE", "EMEA", .06),
    ("FR", "EMEA", .04), ("NL", "EMEA", .03), ("AU", "APAC", .05), ("JP", "APAC", .03),
    ("IN", "APAC", .04), ("BR", "LATAM", .03), ("MX", "LATAM", .01),
]
CANCEL_REASONS = ["too_expensive", "missing_features", "switched_competitor",
                  "no_longer_needed", "company_closed", None]


def add_months(dt: datetime, k: int) -> datetime:
    """Calendar-month arithmetic, clamping the day (Jan 31 + 1 month = Feb 28/29)."""
    m = dt.month - 1 + k
    y = dt.year + m // 12
    m = m % 12 + 1
    return dt.replace(year=y, month=m, day=min(dt.day, calendar.monthrange(y, m)[1]))


def interval_months(plan_id: int) -> int:
    return 12 if C.PLANS[plan_id][3] == "year" else 1


def tier(plan_id: int) -> int:
    return C.PLANS[plan_id][2]


def list_price(plan_id: int, t: datetime) -> int:
    d = t.date()
    for pid, amount, start, end in C.PLAN_PRICES:
        if pid == plan_id and start <= d and (end is None or d < end):
            return amount
    # retired plan: last known price
    return [p for p in C.PLAN_PRICES if p[0] == plan_id][-1][1]


@dataclass
class Sub:
    id: int
    customer_id: int
    plan_id: int
    seats: int
    unit: int
    status: str
    created_at: datetime
    updated_at: datetime
    negotiated: float = 1.0
    trial_start: datetime | None = None
    trial_end: datetime | None = None
    anchor: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    cancel_at_period_end: bool = False
    canceled_at: datetime | None = None
    ended_at: datetime | None = None
    cancel_reason: str | None = None
    # usage-segment bookkeeping
    seg_t: datetime | None = None
    seg_kind: str | None = None
    seg_seats: int = 0
    seg_tier: int = 0


class Simulator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.r = random.Random(cfg.seed)
        self.names = NameFactory(random.Random(cfg.seed + 1))
        self.START = cfg.start_ts
        self.END = cfg.end_ts

        self._cid = itertools.count(1)
        self._uid = itertools.count(100_001)
        self._sid = itertools.count(10_001)
        self._eid = itertools.count(1)
        self._iid = itertools.count(1)
        self._lid = itertools.count(1)
        self._did = itertools.count(1)
        self._aid = itertools.count(1)

        self.customers: dict[int, dict] = {}
        self.audit: list[tuple] = []
        self.users: list[list] = []
        self.cust_users: dict[int, list[int]] = defaultdict(list)   # cid -> indexes into users
        self.subs: list[Sub] = []
        self.sub_events: list[tuple] = []
        self.discounts: list[tuple] = []
        self.sub_discounts: dict[int, list[tuple]] = defaultdict(list)
        self.invoices: list[list] = []
        self.lines: list[tuple] = []

        # consumed by the usage and marketing generators
        self.segments: dict[int, list[tuple]] = defaultdict(list)    # cid -> (start, end, seats, kind, tier)
        self.churn_signals: dict[int, list[datetime]] = defaultdict(list)
        self.first_paid: dict[int, tuple[str | None, datetime]] = {}  # cid -> (channel, ts)

    # ------------------------------------------------------------------ helpers

    def u(self, a: float, b: float) -> float:
        return self.r.uniform(a, b)

    def pick(self, weights: dict):
        keys = list(weights)
        return self.r.choices(keys, weights=[weights[k] for k in keys])[0]

    def jitter(self, t: datetime, max_hours: float) -> datetime:
        return t + timedelta(seconds=self.r.uniform(0, max_hours * 3600))

    # ---------------------------------------------------------------- top level

    def run(self) -> "Simulator":
        n = self.cfg.n_customers
        span = (self.END - self.START).total_seconds()
        # Signup density grows linearly over time (pdf proportional to 1 + 2x).
        signups = sorted(
            self.START + timedelta(seconds=span * 0.995 * ((-1 + math.sqrt(1 + 8 * v)) / 2))
            for v in (self.r.random() for _ in range(n))
        )
        test_idx = set(self.r.sample(range(n), min(C.N_TEST_ACCOUNTS, n)))
        for i, t in enumerate(signups):
            cust = self._new_customer(t, is_test=i in test_idx)
            self._lifecycle(cust, t)

        self._close_segments()
        self._attribute_history()
        self._gdpr_deletes()
        return self

    # ---------------------------------------------------------------- customers

    def _new_customer(self, t: datetime, is_test: bool = False, clone_of: dict | None = None) -> dict:
        cid = next(self._cid)
        if clone_of:
            seg = clone_of["segment"]
            name, domain = self.names.variant(clone_of["account_name"]), clone_of["domain"]
            industry, country, region = clone_of["industry"], clone_of["country_code"], clone_of["region"]
            band = clone_of["employee_band"]
        else:
            seg = "enterprise" if is_test else self.pick(C.SEGMENTS)
            name, domain = self.names.company()
            if is_test:
                name, domain = f"TEST - {name}", domain.replace(".example", ".meridian-internal.example")
            industry = self.r.choice(INDUSTRIES)
            code, region, _ = self.r.choices(COUNTRIES, weights=[c[2] for c in COUNTRIES])[0]
            country = code
            lo, hi = SEGMENT_BANDS[seg]
            band = BANDS[self.r.randint(lo, hi)]

        channel = None if is_test else self.pick(C.CHANNELS[seg])
        label = channel
        if channel and t.date() < C.CHANNEL_FORM_CHANGE:
            label = C.OLD_CHANNEL_LABELS.get(channel, channel)
        if not is_test and self.r.random() < C.P_CHANNEL_NULL:
            label = None

        cust = dict(
            customer_id=cid, account_name=name, domain=domain, industry=industry,
            employee_band=band, country_code=country, region=region,
            acquisition_channel=label, is_test_account=is_test,
            signup_at=t, created_at=t, updated_at=t, deleted_at=None,
            segment=seg, channel_norm=channel,
            # a handful of QA accounts are only flagged as test accounts later
            _flag_test_later=is_test and self.r.random() < 0.25,
        )
        if cust["_flag_test_later"]:
            cust["is_test_account"] = False
        self.customers[cid] = cust
        self._audit("INSERT", t, cust)
        self._ensure_users(cid, 1, t)
        return cust

    def _audit(self, op: str, t: datetime, c: dict):
        self.audit.append((next(self._aid), op, t, c["customer_id"], c["account_name"], c["domain"],
                           c["industry"], c["employee_band"], c["country_code"], c["region"],
                           c["acquisition_channel"], c["is_test_account"]))

    def _attribute_history(self):
        """Slowly changing customer attributes, written to the audit log."""
        for c in list(self.customers.values()):
            start = c["created_at"]
            years = max((self.END - start).days / 365.0, 0.01)
            changes = []
            if self.r.random() < 0.12 * years:
                changes.append("band")
            if self.r.random() < 0.06 * years:   # sometimes it grows twice
                changes.append("band")
            if self.r.random() < 0.03:
                changes.append("industry")
            if self.r.random() < 0.02:
                changes.append("rename")
            if self.r.random() < 0.01:
                changes.append("country")
            for _ in range(int(self.r.random() < 0.10 * years) + int(self.r.random() < 0.03 * years)):
                changes.append("noop")
            if c["_flag_test_later"]:
                changes.append("flag_test")
            times = sorted(start + timedelta(seconds=self.u(3600, (self.END - start).total_seconds() - 60))
                           for _ in changes)
            self.r.shuffle(changes)
            for kind, t in zip(changes, times):
                if kind == "band":
                    i = BANDS.index(c["employee_band"])
                    if i == len(BANDS) - 1:
                        continue
                    c["employee_band"] = BANDS[i + 1]
                elif kind == "industry":
                    c["industry"] = self.r.choice([x for x in INDUSTRIES if x != c["industry"]])
                elif kind == "rename":
                    c["account_name"] = self.names.company()[0]
                elif kind == "country":
                    code, region, _ = self.r.choices(COUNTRIES, weights=[x[2] for x in COUNTRIES])[0]
                    c["country_code"], c["region"] = code, region
                elif kind == "flag_test":
                    c["is_test_account"] = True
                self._audit("UPDATE", t, c)
                c["updated_at"] = max(c["updated_at"], t)

    def _gdpr_deletes(self):
        """Erase PII for a few trial-only accounts. Their history stays behind."""
        cutoff = self.END - 120 * DAY
        candidates = [c for c in self.customers.values()
                      if c["customer_id"] not in self.first_paid and not c["is_test_account"]
                      and c["created_at"] < cutoff]
        tests = [c for c in self.customers.values() if c["is_test_account"]]
        for c in self.r.sample(candidates, min(C.N_GDPR_DELETES, len(candidates))):
            t = c["created_at"] + timedelta(days=self.u(30, (cutoff - c["created_at"]).days + 30))
            c["account_name"] = c["domain"] = None
            c["deleted_at"] = c["updated_at"] = t
            self._audit("DELETE", t, c)
            for ui in self.cust_users[c["customer_id"]]:
                self.users[ui][2] = self.users[ui][3] = None
                self.users[ui][6] = self.users[ui][6] or t
        for c in self.r.sample(tests, min(3, len(tests))):
            t = self.END - timedelta(days=self.u(5, 60))
            c["deleted_at"] = c["updated_at"] = t
            self._audit("DELETE", t, c)

    def _ensure_users(self, cid: int, seats: int, t: datetime):
        """Keep roughly one active user per seat: invite on growth, deactivate on shrink."""
        idx = self.cust_users[cid]
        active = [i for i in idx if self.users[i][6] is None]
        domain = self.customers[cid]["domain"]
        if len(active) < seats:
            for n in range(seats - len(active)):
                first, last = self.names.person()
                uid = next(self._uid)
                created = t if not idx else t + timedelta(seconds=self.u(60, 6 * 86400))
                role = "owner" if not idx else self.r.choices(
                    ["admin", "member", "viewer"], weights=[.1, .7, .2])[0]
                self.users.append([uid, cid, f"{first.lower()}.{last.lower()}{uid % 1000}@{domain}",
                                   f"{first} {last}", role, created, None])
                idx.append(len(self.users) - 1)
        elif len(active) > seats + 2:
            removable = [i for i in active if self.users[i][4] != "owner"]
            self.r.shuffle(removable)
            for i in removable[: len(active) - seats]:
                self.users[i][6] = t + timedelta(seconds=self.u(0, 3 * 86400))

    # ------------------------------------------------------------ subscriptions

    def _lifecycle(self, cust: dict, t: datetime):
        seg = cust["segment"]
        if cust["is_test_account"] or cust["_flag_test_later"]:
            self._direct(cust, t + timedelta(minutes=self.u(1, 30)), test=True)
        elif self.r.random() < C.P_TRIAL[seg]:
            self._trial(cust, t + timedelta(seconds=self.u(5, 600)))
        else:
            self._direct(cust, t + timedelta(hours=self.u(1, 72)))

    def _seats_for(self, seg: str) -> int:
        if seg == "smb":
            n = 1
            while self.r.random() > 0.35 and n < 15:
                n += 1
            return n
        if seg == "mid_market":
            return int(min(max(self.r.lognormvariate(math.log(20), 0.5), 8), 80))
        return int(min(max(self.r.lognormvariate(math.log(80), 0.7), 25), 600))

    def _first_plan(self, seg: str, t: datetime) -> int:
        plan = self.pick(C.FIRST_PLAN[seg])
        if plan == 4 and t.date() < C.PLANS[3][4] and self.r.random() < C.P_LEGACY_TEAM:
            plan = 3
        return plan

    def _unit_for(self, sub: Sub, plan_id: int, t: datetime) -> int:
        if plan_id == 8:
            return int(round(list_price(8, t) * sub.negotiated))
        return list_price(plan_id, t)

    def _new_sub(self, cust: dict, plan: int, seats: int, t: datetime, status: str) -> Sub:
        sub = Sub(id=next(self._sid), customer_id=cust["customer_id"], plan_id=plan, seats=seats,
                  unit=0, status=status, created_at=t, updated_at=t,
                  negotiated=self.u(0.60, 0.95))
        sub.unit = self._unit_for(sub, plan, t)
        self.subs.append(sub)
        return sub

    def _maybe_discount(self, cust: dict, sub: Sub, t: datetime, test: bool = False):
        if test:
            code = "INTERNAL100"
        elif sub.plan_id == 8 or self.r.random() >= C.P_DISCOUNT:
            return
        elif cust["channel_norm"] == "partner":
            code = "PARTNER25"
        elif interval_months(sub.plan_id) == 12:
            code = "ANNUAL10"
        else:
            code = "LAUNCH20"
        pct, months = C.COUPONS[code]
        ends = add_months(t, months) if months else None
        self.discounts.append((next(self._did), sub.id, sub.customer_id, code, pct, t, ends))
        self.sub_discounts[sub.id].append((pct, t, ends))

    def _discount_pct(self, sub: Sub, t: datetime) -> float:
        for pct, s, e in self.sub_discounts.get(sub.id, []):
            if s <= t and (e is None or t < e):
                return pct
        return 0.0

    def _record_first_paid(self, cust: dict, t: datetime):
        cid = cust["customer_id"]
        if cid not in self.first_paid and not cust["is_test_account"] and not cust["_flag_test_later"]:
            self.first_paid[cid] = (cust["channel_norm"], t)

    def _trial(self, cust: dict, t: datetime):
        seg = cust["segment"]
        plan = self._first_plan(seg, t)
        if interval_months(plan) == 12:          # trials run on a monthly plan
            plan = 6
        seats = self._seats_for(seg)
        sub = self._new_sub(cust, plan, seats, t, "trialing")
        sub.trial_start, sub.trial_end = t, t + C.TRIAL_DAYS * DAY
        sub.period_start, sub.period_end = sub.trial_start, sub.trial_end
        self._ensure_users(cust["customer_id"], seats, t)
        self._event(sub, "trial_started", t, new_plan=plan, new_seats=seats, new_unit=sub.unit)
        self._invoice(sub, "subscription_create", t, sub.trial_end, t,
                      [("subscription", f"Trial period for {C.PLANS[plan][1]}", plan, seats, 0, 0,
                        t, sub.trial_end)])
        self._mark(sub, t, "trial")

        converts = self.r.random() < C.P_TRIAL_CONVERT[seg]
        if converts:
            conv = sub.trial_end if self.r.random() > C.P_CONVERT_EARLY else t + timedelta(days=self.u(2, 13))
            if conv >= self.END:
                return
            new_plan = plan
            if seg == "enterprise":
                new_plan = self.pick(C.FIRST_PLAN["enterprise"])
            elif self.r.random() < C.P_ANNUAL[seg] and plan in C.TO_ANNUAL:
                new_plan = C.TO_ANNUAL[plan]
            new_seats = seats if self.r.random() < 0.7 else max(1, int(round(seats * self.u(0.6, 1.6))))
            if new_plan == 8:
                new_seats = max(new_seats, C.ENTERPRISE_MIN_SEATS)
            prev_unit = sub.unit
            sub.plan_id, sub.seats = new_plan, new_seats
            sub.unit = self._unit_for(sub, new_plan, conv)
            sub.trial_end = conv
            self._event(sub, "trial_converted", conv, prev_plan=plan, new_plan=new_plan,
                        prev_seats=seats, new_seats=new_seats, prev_unit=prev_unit, new_unit=sub.unit)
            self._ensure_users(cust["customer_id"], new_seats, conv)
            self._maybe_discount(cust, sub, conv)
            self._record_first_paid(cust, conv)
            self._run_paid(cust, sub, conv, "subscription_create")
            return

        if sub.trial_end >= self.END:
            return
        te = sub.trial_end
        self._event(sub, "trial_expired", te)
        sub.status, sub.ended_at = "canceled", te
        self._mark(sub, te, None)
        if self.r.random() < C.P_LATE_CONVERT:
            self._direct(cust, te + timedelta(days=self.u(1, 10)), reason="late_trial_conversion")
        elif self.r.random() < C.P_TRIAL_RETURN:
            self._direct(cust, te + timedelta(days=self.u(60, 300)), reason="returning_trial")

    def _direct(self, cust: dict, t: datetime, reason: str | None = None, test: bool = False):
        if t >= self.END:
            return
        seg = cust["segment"]
        if test:
            plan, seats = 8, 50
        else:
            plan = self._first_plan(seg, t)
            if interval_months(plan) == 1 and plan in C.TO_ANNUAL and self.r.random() < C.P_ANNUAL[seg]:
                plan = C.TO_ANNUAL[plan]
            seats = self._seats_for(seg)
            if plan == 8:
                seats = max(seats, C.ENTERPRISE_MIN_SEATS)
        sub = self._new_sub(cust, plan, seats, t, "active")
        if test:
            sub.negotiated = 1.0
            sub.unit = list_price(8, t)
        self._ensure_users(cust["customer_id"], seats, t)
        self._event(sub, "subscription_created", t, new_plan=plan, new_seats=seats,
                    new_unit=sub.unit, reason=reason)
        self._maybe_discount(cust, sub, t, test=test)
        self._record_first_paid(cust, t)
        self._run_paid(cust, sub, t, "subscription_create", test=test)

    # --------------------------------------------------------------- paid loop

    def _run_paid(self, cust: dict, sub: Sub, t0: datetime, first_reason: str, test: bool = False):
        seg = cust["segment"]
        sub.status = "active"
        sub.anchor = t0
        self._mark(sub, t0, "paid")
        k = 0
        reason = first_reason
        while True:
            n = interval_months(sub.plan_id)
            ps = add_months(sub.anchor, k * n)
            pe = add_months(sub.anchor, (k + 1) * n)
            if ps >= self.END:
                return
            sub.period_start, sub.period_end = ps, pe
            annual = n == 12

            inv = self._invoice(sub, reason, ps, pe, ps, [self._sub_line(sub, ps, pe)],
                                net_terms=annual and seg == "enterprise")
            reason = "subscription_cycle"

            # -- payment failure on renewal/cycle invoices
            pf = (C.HAZ_ANNUAL[seg][3] if annual else C.HAZ_MONTHLY[seg][6])
            if not test and k > 0 and inv is not None and self.r.random() < pf:
                ft = ps + timedelta(hours=self.u(1, 6))
                self._event(sub, "payment_failed", ft)
                sub.status = "past_due"
                inv[4], inv[10] = "open", None
                self._mark(sub, ft, "past_due")
                if self.r.random() < C.P_PAYMENT_RECOVERED:
                    rt = ps + timedelta(days=self.u(1, 14))
                    if rt >= self.END:
                        return
                    self._event(sub, "payment_recovered", rt)
                    inv[4], inv[10] = "paid", rt
                    sub.status = "active"
                    self._mark(sub, rt, "paid")
                else:
                    ct = ps + C.DUNNING_DAYS * DAY
                    if ct >= self.END:
                        return
                    inv[4] = "uncollectible"
                    sub.canceled_at = ct
                    self._event(sub, "canceled", ct, reason="payment_failed")
                    self._end(cust, sub, ct, test)
                    return

            # -- things that happen inside the period
            pending: dict = {}
            slices = [(add_months(ps, j), add_months(ps, j + 1)) for j in range(n)]
            for s, e in slices:
                if s >= self.END:
                    break
                for et, kind in self._draw_period_events(seg, annual, s, e, test):
                    if et >= self.END or et >= pe:
                        continue
                    self._apply_event(cust, sub, kind, et, ps, pe, pending)

            if pe >= self.END:
                return

            # -- period boundary
            if sub.cancel_at_period_end:
                self._event(sub, "canceled", pe, reason=sub.cancel_reason)
                self._end(cust, sub, pe, test)
                return

            changed = False
            if "plan" in pending:
                sub.plan_id = pending["plan"]
                sub.unit = self._unit_for(sub, sub.plan_id, pe)
                changed = True
            if "seats" in pending:
                sub.seats = pending["seats"]
                changed = True

            if not annual and not test and sub.plan_id in C.TO_ANNUAL and self.r.random() < C.HAZ_MONTHLY[seg][5]:
                prev_plan, prev_unit = sub.plan_id, sub.unit
                sub.plan_id = C.TO_ANNUAL[prev_plan]
                sub.unit = self._unit_for(sub, sub.plan_id, pe)
                self._event(sub, "plan_changed", pe - timedelta(days=self.u(1, 10)), effective=pe,
                            prev_plan=prev_plan, new_plan=sub.plan_id, prev_seats=sub.seats,
                            new_seats=sub.seats, prev_unit=prev_unit, new_unit=sub.unit)
                sub.anchor, k, reason = pe, 0, "subscription_update"
                self._mark(sub, pe, "paid")
                self._ensure_users(sub.customer_id, sub.seats, pe)
                continue

            if annual and not test:
                prev_seats, prev_unit = sub.seats, sub.unit
                x = self.r.random()
                if x < C.P_RENEWAL_TRUE_DOWN:
                    sub.seats = max(1, int(round(sub.seats * self.u(0.70, 0.90))))
                elif x < C.P_RENEWAL_TRUE_DOWN + C.P_RENEWAL_TRUE_UP:
                    sub.seats = max(sub.seats + 1, int(round(sub.seats * self.u(1.10, 1.50))))
                if sub.seats != prev_seats:
                    self._event(sub, "seats_changed", pe - timedelta(days=self.u(5, 30)), effective=pe,
                                prev_seats=prev_seats, new_seats=sub.seats)
                if sub.plan_id == 8:
                    sub.unit = int(round(sub.unit * self.u(1.00, 1.07)))
                else:
                    sub.unit = list_price(sub.plan_id, pe)
                self._event(sub, "renewed", pe, prev_plan=sub.plan_id, new_plan=sub.plan_id,
                            prev_seats=prev_seats, new_seats=sub.seats, prev_unit=prev_unit, new_unit=sub.unit)
                changed = True

            if changed:
                self._mark(sub, pe, "paid")
                self._ensure_users(sub.customer_id, sub.seats, pe)
            k += 1

    def _draw_period_events(self, seg, annual, s, e, test):
        if test:
            return []
        span = (e - s).total_seconds()
        out = []
        if annual:
            churn, up, add, _ = C.HAZ_ANNUAL[seg]
            rates = [("churn", churn), ("up", up), ("seat_add", add)]
        else:
            churn, up, down, add, rem, _, _ = C.HAZ_MONTHLY[seg]
            rates = [("churn", churn), ("up", up), ("down", down), ("seat_add", add), ("seat_remove", rem)]
        for kind, p in rates:
            if self.r.random() < p:
                out.append((s + timedelta(seconds=self.u(3600, span - 3600)), kind))
        return sorted(out)

    def _apply_event(self, cust, sub: Sub, kind: str, t: datetime, ps, pe, pending: dict):
        seg = cust["segment"]
        frac = (pe - t).total_seconds() / (pe - ps).total_seconds()

        if kind == "churn":
            if sub.cancel_at_period_end:
                return
            sub.cancel_at_period_end, sub.canceled_at = True, t
            sub.cancel_reason = self.r.choice(CANCEL_REASONS)
            self._event(sub, "cancellation_requested", t, effective=pe, reason=sub.cancel_reason)
            self.churn_signals[sub.customer_id].append(t)
            if self.r.random() < C.P_CANCEL_REVERSED:
                rt = t + timedelta(seconds=self.u(3600, max((pe - t).total_seconds() - 3600, 3601)))
                if rt < pe and rt < self.END:
                    sub.cancel_at_period_end, sub.canceled_at = False, None
                    self._event(sub, "cancellation_reversed", rt)
            return

        if sub.cancel_at_period_end:
            return

        if kind == "up":
            new = C.PLAN_UP.get(sub.plan_id)
            if new is None or (new == 8 and sub.seats < C.ENTERPRISE_MIN_SEATS):
                return
            old_plan, old_unit = sub.plan_id, sub.unit
            new_unit = self._unit_for(sub, new, t)
            self._invoice(sub, "subscription_update", t, pe, t, [
                ("proration", f"Unused time on {C.PLANS[old_plan][1]}", old_plan, sub.seats, old_unit,
                 -int(round(sub.seats * old_unit * frac)), t, pe),
                ("proration", f"Remaining time on {C.PLANS[new][1]}", new, sub.seats, new_unit,
                 int(round(sub.seats * new_unit * frac)), t, pe),
            ])
            sub.plan_id, sub.unit = new, new_unit
            pending.pop("plan", None)
            self._event(sub, "plan_changed", t, prev_plan=old_plan, new_plan=new, prev_seats=sub.seats,
                        new_seats=sub.seats, prev_unit=old_unit, new_unit=new_unit)
            self._mark(sub, t, sub.seg_kind or "paid")

        elif kind == "down":
            new = C.PLAN_DOWN.get(sub.plan_id)
            if new is None or "plan" in pending:
                return
            pending["plan"] = new
            self._event(sub, "plan_changed", t, effective=pe, prev_plan=sub.plan_id, new_plan=new,
                        prev_seats=sub.seats, new_seats=pending.get("seats", sub.seats),
                        prev_unit=sub.unit, new_unit=self._unit_for(sub, new, pe))

        elif kind == "seat_add":
            hi = {"smb": 3, "mid_market": 10, "enterprise": 50}[seg]
            delta = self.r.randint(1, hi)
            old = sub.seats
            sub.seats += delta
            self._invoice(sub, "subscription_update", t, pe, t, [
                ("proration", f"{delta} additional seat(s) on {C.PLANS[sub.plan_id][1]}", sub.plan_id,
                 delta, sub.unit, int(round(delta * sub.unit * frac)), t, pe),
            ])
            self._event(sub, "seats_changed", t, prev_seats=old, new_seats=sub.seats)
            self._ensure_users(sub.customer_id, sub.seats, t)
            self._mark(sub, t, sub.seg_kind or "paid")

        elif kind == "seat_remove":
            base = pending.get("seats", sub.seats)
            new_seats = max(1, base - self.r.randint(1, max(1, base // 4)))
            if new_seats == base:
                return
            pending["seats"] = new_seats
            self._event(sub, "seats_changed", t, effective=pe, prev_seats=sub.seats, new_seats=new_seats)

    def _end(self, cust: dict, sub: Sub, t: datetime, test: bool):
        sub.status, sub.ended_at = "canceled", t
        seats = sub.seats
        self._mark(sub, t, None)
        # read-only access after churn still produces a trickle of usage
        self.segments[sub.customer_id].append((t, t + C.GRACE_DAYS * DAY, max(1, seats // 4), "grace", tier(sub.plan_id)))
        if test or self.r.random() >= C.P_RETURN:
            return
        rt = t + timedelta(days=self.u(30, 240))
        if rt >= self.END:
            return
        mode = self.pick(C.RETURN_MODE)
        if mode == "new_subscription":
            self._direct(cust, rt, reason="win_back")
        elif mode == "new_account":
            clone = self._new_customer(rt, clone_of=cust)
            self._direct(clone, rt + timedelta(minutes=self.u(5, 90)))
        else:
            sub.cancel_at_period_end, sub.canceled_at, sub.ended_at = False, None, None
            sub.seats = max(1, int(round(sub.seats * self.u(0.5, 1.0))))
            self._event(sub, "reactivated", rt, new_plan=sub.plan_id, new_seats=sub.seats, new_unit=sub.unit)
            self._ensure_users(sub.customer_id, sub.seats, rt)
            self._run_paid(cust, sub, rt, "subscription_update")

    # -------------------------------------------------------- record writers

    def _mark(self, sub: Sub, t: datetime, kind: str | None):
        """Close the current usage segment at t and open a new one with the sub's current state."""
        if sub.seg_kind is not None and sub.seg_t is not None and t > sub.seg_t:
            self.segments[sub.customer_id].append((sub.seg_t, t, sub.seg_seats, sub.seg_kind, sub.seg_tier))
        sub.seg_t, sub.seg_kind, sub.seg_seats, sub.seg_tier = t, kind, sub.seats, tier(sub.plan_id)

    def _close_segments(self):
        for sub in self.subs:
            self._mark(sub, self.END, None)

    def _event(self, sub: Sub, etype: str, t: datetime, effective: datetime | None = None,
               prev_plan=None, new_plan=None, prev_seats=None, new_seats=None,
               prev_unit=None, new_unit=None, reason=None):
        if t >= self.END:
            return
        sub.updated_at = max(sub.updated_at, t)
        received = t + timedelta(seconds=self.u(0.2, 5))
        if self.r.random() < C.P_SUB_EVENT_LATE:
            received = t + timedelta(hours=self.u(1, 72))
        if received >= self.END:
            return  # not delivered yet as of the snapshot
        row = (sub.id, sub.customer_id, etype, t, effective or t, prev_plan, new_plan, prev_seats,
               new_seats, prev_unit, new_unit, reason)
        self.sub_events.append((next(self._eid),) + row + (received,))
        if self.r.random() < C.P_SUB_EVENT_DUPLICATE:
            again = received + timedelta(seconds=self.u(1, 600))
            if again < self.END:
                self.sub_events.append((next(self._eid),) + row + (again,))

    def _sub_line(self, sub: Sub, ps, pe):
        return ("subscription", f"{sub.seats} x {C.PLANS[sub.plan_id][1]}", sub.plan_id, sub.seats,
                sub.unit, sub.seats * sub.unit, ps, pe)

    def _invoice(self, sub: Sub, reason, ps, pe, issued, lines, net_terms: bool = False):
        if issued >= self.END:
            return None
        subtotal = sum(l[5] for l in lines)
        pct = self._discount_pct(sub, issued)
        disc = -int(round(subtotal * pct / 100.0)) if subtotal > 0 and pct else 0
        due = issued + (30 * DAY if net_terms else timedelta(0))
        if subtotal <= 0:
            paid = issued
        elif net_terms:
            paid = issued + timedelta(days=self.u(5, 45))
        else:
            paid = issued + timedelta(seconds=self.u(30, 7200))
        status = "paid" if paid < self.END else "open"

        def emit(issued_at, status, paid_at, voided_at):
            iid = next(self._iid)
            inv = [iid, f"MER-{issued_at:%Y%m}-{iid:06d}", sub.customer_id, sub.id, status, reason,
                   ps, pe, issued_at, due, paid_at if status == "paid" else None, voided_at,
                   subtotal, disc, subtotal + disc, "USD"]
            self.invoices.append(inv)
            for l in lines:
                self.lines.append((next(self._lid), iid) + l)
            if disc:
                self.lines.append((next(self._lid), iid, "discount", f"{pct:g}% off", None, None, None,
                                   disc, ps, pe))
            return inv

        if reason == "subscription_cycle" and subtotal > 0 and self.r.random() < C.P_INVOICE_VOID_REISSUE:
            vt = issued + timedelta(hours=self.u(2, 48))
            if vt < self.END:
                emit(issued, "void", None, vt)
                return emit(vt, status if paid + (vt - issued) < self.END else "open", paid + (vt - issued), None)
        return emit(issued, status, paid, None)

    # ------------------------------------------------------------------- output

    def subscription_rows(self):
        for s in self.subs:
            yield (s.id, s.customer_id, s.plan_id, s.status, s.seats, s.unit, s.trial_start, s.trial_end,
                   s.period_start, s.period_end, s.cancel_at_period_end, s.canceled_at, s.ended_at,
                   s.created_at, s.updated_at)

    def customer_rows(self):
        for c in self.customers.values():
            yield (c["customer_id"], c["account_name"], c["domain"], c["industry"], c["employee_band"],
                   c["country_code"], c["region"], c["acquisition_channel"], c["is_test_account"],
                   c["signup_at"], c["created_at"], c["updated_at"], c["deleted_at"])
