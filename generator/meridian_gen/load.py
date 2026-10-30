"""Write the simulated world into Postgres with COPY."""

from __future__ import annotations

import io
import json
import time
from importlib import resources

import psycopg

from . import config as C
from .config import Config

TABLES = {
    "app.plans": ["plan_id", "plan_code", "plan_name", "tier_rank", "billing_interval", "is_active",
                  "created_at", "retired_at"],
    "app.plan_prices": ["plan_price_id", "plan_id", "unit_amount_cents", "currency", "effective_from",
                        "effective_to"],
    "app.customers": ["customer_id", "account_name", "domain", "industry", "employee_band", "country_code",
                      "region", "acquisition_channel", "is_test_account", "signup_at", "created_at",
                      "updated_at", "deleted_at"],
    "app.customers_audit": ["audit_id", "operation", "changed_at", "customer_id", "account_name", "domain",
                            "industry", "employee_band", "country_code", "region", "acquisition_channel",
                            "is_test_account"],
    "app.users": ["user_id", "customer_id", "email", "full_name", "role", "created_at", "deactivated_at"],
    "app.subscriptions": ["subscription_id", "customer_id", "plan_id", "status", "seats", "unit_amount_cents",
                          "trial_start", "trial_end", "current_period_start", "current_period_end",
                          "cancel_at_period_end", "canceled_at", "ended_at", "created_at", "updated_at"],
    "app.subscription_events": ["event_id", "subscription_id", "customer_id", "event_type", "occurred_at",
                                "effective_at", "prev_plan_id", "new_plan_id", "prev_seats", "new_seats",
                                "prev_unit_amount_cents", "new_unit_amount_cents", "reason", "received_at"],
    "billing.discounts": ["discount_id", "subscription_id", "customer_id", "coupon_code", "percent_off",
                          "starts_at", "ends_at"],
    "billing.invoices": ["invoice_id", "invoice_number", "customer_id", "subscription_id", "status",
                         "billing_reason", "period_start", "period_end", "issued_at", "due_at", "paid_at",
                         "voided_at", "subtotal_cents", "discount_cents", "total_cents", "currency"],
    "billing.invoice_lines": ["invoice_line_id", "invoice_id", "line_type", "description", "plan_id", "seats",
                              "unit_amount_cents", "amount_cents", "period_start", "period_end"],
    "marketing.spend": ["spend_id", "spend_date", "channel", "campaign_id", "campaign_name", "amount_usd",
                        "impressions", "clicks", "reported_at"],
}
USAGE_COLS = ["event_id", "customer_id", "user_id", "event_name", "occurred_at", "received_at", "client",
              "app_version"]


def connect(dsn: str, retries: int = 30) -> psycopg.Connection:
    for attempt in range(retries):
        try:
            return psycopg.connect(dsn)
        except psycopg.OperationalError:
            if attempt == retries - 1:
                raise
            time.sleep(1)


def apply_schema(conn: psycopg.Connection):
    sql = resources.files("meridian_gen").joinpath("schema.sql").read_text()
    conn.execute(sql)


def copy_rows(conn: psycopg.Connection, table: str, rows) -> int:
    cols = TABLES[table]
    n = 0
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
            for row in rows:
                cp.write_row(row)
                n += 1
    return n


def copy_usage(conn: psycopg.Connection, df) -> int:
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, date_format="%Y-%m-%d %H:%M:%S.%f+00")
    buf.seek(0)
    with conn.cursor() as cur:
        with cur.copy(f"COPY events.usage_events ({', '.join(USAGE_COLS)}) FROM STDIN WITH (FORMAT csv)") as cp:
            while chunk := buf.read(1 << 20):
                cp.write(chunk)
    return len(df)


def plan_rows():
    from datetime import datetime, time as dtime
    for pid, (code, name, tier, interval, retired) in C.PLANS.items():
        created = datetime.combine(C.PLAN_PRICES[0][2], dtime(), C.UTC)
        retired_ts = datetime.combine(retired, dtime(), C.UTC) if retired else None
        yield (pid, code, name, tier, interval, retired is None, created, retired_ts)


def plan_price_rows():
    for i, (pid, amount, start, end) in enumerate(C.PLAN_PRICES, start=1):
        yield (i, pid, amount, "USD", start, end)


def finish(conn: psycopg.Connection, cfg: Config, counts: dict, extra_args: dict):
    conn.execute(
        "INSERT INTO meta.seed_runs (seed, history_start, as_of, usage_start, generator_args, row_counts) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (cfg.seed, cfg.history_start, cfg.as_of, cfg.usage_start,
         json.dumps({**cfg.as_dict(), **extra_args}), json.dumps(counts)),
    )
