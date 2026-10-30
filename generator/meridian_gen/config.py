"""Knobs for the Meridian generator.

Everything that shapes the data lives here so the behaviour is readable in one
place. Rates are per month unless stated otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time, timedelta, timezone

UTC = timezone.utc


@dataclass(frozen=True)
class Config:
    seed: int = 42
    n_customers: int = 5_000
    usage_events: int = 10_000_000
    history_start: date = date(2024, 1, 1)   # company's first customer
    as_of: date = date(2026, 9, 30)          # last day of data, inclusive
    usage_months: int = 12                   # usage events only for the trailing N months

    @property
    def start_ts(self) -> datetime:
        return datetime.combine(self.history_start, time(), UTC)

    @property
    def end_ts(self) -> datetime:
        """Exclusive upper bound: midnight after as_of."""
        return datetime.combine(self.as_of + timedelta(days=1), time(), UTC)

    @property
    def usage_start(self) -> date:
        end = self.as_of + timedelta(days=1)
        m = end.month - 1 - self.usage_months
        return date(end.year + m // 12, m % 12 + 1, 1)

    def as_dict(self) -> dict:
        d = asdict(self)
        return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in d.items()}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

# plan_id: (code, name, tier_rank, interval, retired_at)
PLANS = {
    1: ("starter_monthly",   "Starter",           1, "month", None),
    2: ("starter_annual",    "Starter (Annual)",  1, "year",  None),
    3: ("team_monthly",      "Team (Legacy)",     2, "month", date(2025, 1, 1)),
    4: ("growth_monthly",    "Growth",            3, "month", None),
    5: ("growth_annual",     "Growth (Annual)",   3, "year",  None),
    6: ("pro_monthly",       "Pro",               4, "month", None),
    7: ("pro_annual",        "Pro (Annual)",      4, "year",  None),
    8: ("enterprise_annual", "Enterprise",        5, "year",  None),
}

# (plan_id, unit cents per seat per interval, effective_from, effective_to)
# Growth gets a list-price increase on 2026-04-01. Existing monthly customers
# keep their old price (grandfathered); annual customers re-price at renewal.
GROWTH_PRICE_CHANGE = date(2026, 4, 1)
PLAN_PRICES = [
    (1, 1_500,   date(2024, 1, 1), None),
    (2, 15_000,  date(2024, 1, 1), None),
    (3, 2_500,   date(2024, 1, 1), date(2025, 1, 1)),
    (4, 3_500,   date(2024, 1, 1), GROWTH_PRICE_CHANGE),
    (4, 3_900,   GROWTH_PRICE_CHANGE, None),
    (5, 35_000,  date(2024, 1, 1), GROWTH_PRICE_CHANGE),
    (5, 39_000,  GROWTH_PRICE_CHANGE, None),
    (6, 7_500,   date(2024, 1, 1), None),
    (7, 75_000,  date(2024, 1, 1), None),
    (8, 120_000, date(2024, 1, 1), None),   # list only -- every deal is negotiated
]

PLAN_UP = {1: 4, 2: 5, 3: 4, 4: 6, 5: 7, 6: None, 7: 8, 8: None}
PLAN_DOWN = {1: None, 2: None, 3: 1, 4: 1, 5: 2, 6: 4, 7: 5, 8: 7}
TO_ANNUAL = {1: 2, 3: 5, 4: 5, 6: 7}
ENTERPRISE_MIN_SEATS = 20

# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

SEGMENTS = {"smb": 0.60, "mid_market": 0.30, "enterprise": 0.10}
N_TEST_ACCOUNTS = 20          # internal QA accounts mixed into real data
N_GDPR_DELETES = 8            # erased accounts whose history remains

TRIAL_DAYS = 14
P_TRIAL = {"smb": 0.90, "mid_market": 0.60, "enterprise": 0.10}
P_TRIAL_CONVERT = {"smb": 0.30, "mid_market": 0.45, "enterprise": 0.60}
P_CONVERT_EARLY = 0.30        # of converters, convert before the trial ends
P_LATE_CONVERT = 0.04         # of non-converters, buy 1-10 days after expiry (new sub)
P_TRIAL_RETURN = 0.06         # of non-converters, come back months later

# segment -> {plan_id: weight} for the first paid plan (monthly variant)
FIRST_PLAN = {
    "smb":        {1: 0.60, 4: 0.35, 6: 0.05},
    "mid_market": {4: 0.50, 6: 0.50},
    "enterprise": {8: 0.70, 7: 0.30},
}
P_ANNUAL = {"smb": 0.15, "mid_market": 0.35, "enterprise": 1.0}
P_LEGACY_TEAM = 0.40          # before 2025-01-01, share of Growth picks sold as Team

CHANNELS = {
    "smb":        {"paid_search": .35, "paid_social": .15, "organic": .25, "referral": .12,
                   "partner": .05, "events": .03, "outbound": .05},
    "mid_market": {"paid_search": .20, "paid_social": .15, "organic": .15, "referral": .10,
                   "partner": .10, "events": .10, "outbound": .20},
    "enterprise": {"paid_search": .05, "paid_social": .00, "organic": .05, "referral": .10,
                   "partner": .15, "events": .20, "outbound": .45},
}
# The signup form changed on this date; before it, labels were Title Case.
CHANNEL_FORM_CHANGE = date(2025, 2, 1)
OLD_CHANNEL_LABELS = {"paid_search": "Paid Search", "paid_social": "Paid Social",
                      "organic": "Organic", "referral": "Referral"}
P_CHANNEL_NULL = 0.04

# ---------------------------------------------------------------------------
# Subscription behaviour (monthly hazards)
# ---------------------------------------------------------------------------

HAZ_MONTHLY = {
    #               churn  up     down   +seat  -seat  annual payfail
    "smb":        (0.035, 0.020, 0.012, 0.05, 0.03, 0.008, 0.040),
    "mid_market": (0.018, 0.025, 0.012, 0.08, 0.03, 0.010, 0.020),
    "enterprise": (0.010, 0.020, 0.008, 0.06, 0.02, 0.010, 0.010),
}
HAZ_ANNUAL = {
    #               churn  up     +seat  payfail(at renewal)
    "smb":        (0.014, 0.008, 0.03, 0.03),
    "mid_market": (0.010, 0.010, 0.05, 0.02),
    "enterprise": (0.007, 0.006, 0.06, 0.01),
}
P_RENEWAL_TRUE_DOWN = 0.20    # annual: seats cut at renewal
P_RENEWAL_TRUE_UP = 0.20      # annual: seats added at renewal
P_CANCEL_REVERSED = 0.12      # cancellation requested, then withdrawn
P_PAYMENT_RECOVERED = 0.70    # failed payment later succeeds (else involuntary churn)
DUNNING_DAYS = 21

P_RETURN = 0.18               # churned paying customers who come back (1-8 months)
RETURN_MODE = {"new_subscription": 0.60, "new_account": 0.25, "reactivate": 0.15}

# Coupons for new paid subscriptions
P_DISCOUNT = 0.15
COUPONS = {
    "LAUNCH20":    (20.0, 3),     # percent off, months (None = forever)
    "ANNUAL10":    (10.0, None),
    "PARTNER25":   (25.0, 12),
    "INTERNAL100": (100.0, None),  # test accounts only
}

# ---------------------------------------------------------------------------
# Data-quality defects (rates)
# ---------------------------------------------------------------------------

P_SUB_EVENT_DUPLICATE = 0.005  # webhook delivered twice
P_SUB_EVENT_LATE = 0.02        # webhook received hours/days late
P_INVOICE_VOID_REISSUE = 0.004
P_USAGE_DUPLICATE = 0.004
P_USAGE_LATE = 0.015
P_USAGE_CLOCK_SKEW = 0.001
GRACE_DAYS = 30                # read-only access after churn still emits events
