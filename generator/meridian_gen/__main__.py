"""meridian-gen: seed the Meridian source database.

    python -m meridian_gen                 # full seed: ~5k customers, ~10M usage events
    python -m meridian_gen --quick         # small seed for iterating (~500 customers, 300k events)
    python -m meridian_gen --seed 7        # a different, equally reproducible world

Connection comes from --dsn or $MERIDIAN_DSN. Every run drops and rebuilds the
source schemas, so the same --seed always produces the same database.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date

from . import load, marketing
from .config import Config
from .simulate import Simulator
from .usage import UsageGenerator


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="meridian-gen", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dsn", default=os.environ.get("MERIDIAN_DSN"))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--customers", type=int, default=5_000)
    p.add_argument("--usage-events", type=int, default=10_000_000)
    p.add_argument("--history-start", type=date.fromisoformat, default=date(2024, 1, 1))
    p.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 30))
    p.add_argument("--usage-months", type=int, default=12)
    p.add_argument("--quick", action="store_true", help="500 customers, 300k usage events")
    p.add_argument("--skip-usage", action="store_true", help="load everything except usage events")
    a = p.parse_args(argv)
    if a.quick:
        a.customers, a.usage_events = 500, 300_000
    if not a.dsn:
        p.error("no database: pass --dsn or set MERIDIAN_DSN")
    return a


def log(msg: str, t0: float):
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def main(argv=None) -> int:
    a = parse_args(argv)
    cfg = Config(seed=a.seed, n_customers=a.customers, usage_events=a.usage_events,
                 history_start=a.history_start, as_of=a.as_of, usage_months=a.usage_months)
    t0 = time.time()
    log(f"seed={cfg.seed} customers={cfg.n_customers:,} history={cfg.history_start}..{cfg.as_of} "
        f"usage from {cfg.usage_start}", t0)

    sim = Simulator(cfg).run()
    log(f"simulated {len(sim.customers):,} customers, {len(sim.subs):,} subscriptions, "
        f"{len(sim.invoices):,} invoices", t0)
    spend = marketing.generate(sim, cfg)

    counts = {}
    with load.connect(a.dsn) as conn:
        load.apply_schema(conn)
        for table, rows in [
            ("app.plans", load.plan_rows()),
            ("app.plan_prices", load.plan_price_rows()),
            ("app.customers", sim.customer_rows()),
            ("app.customers_audit", sim.audit),
            ("app.users", sim.users),
            ("app.subscriptions", sim.subscription_rows()),
            ("app.subscription_events", sim.sub_events),
            ("billing.discounts", sim.discounts),
            ("billing.invoices", sim.invoices),
            ("billing.invoice_lines", sim.lines),
            ("marketing.spend", spend),
        ]:
            counts[table] = load.copy_rows(conn, table, rows)
            log(f"{table:<26} {counts[table]:>10,}", t0)
        conn.commit()

        if not a.skip_usage:
            gen = UsageGenerator(sim, cfg)
            total = 0
            for df in gen.months():
                total += load.copy_usage(conn, df)
                conn.commit()
                log(f"events.usage_events        {total:>10,}  (through {df['received_at'].max():%Y-%m})", t0)
            counts["events.usage_events"] = total

        log("analyzing", t0)
        conn.execute("ANALYZE")
        load.finish(conn, cfg, counts, {"skip_usage": a.skip_usage})
        conn.commit()

    log("done", t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
