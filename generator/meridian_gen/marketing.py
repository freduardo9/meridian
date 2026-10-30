"""Daily ad-platform spend by campaign.

Spend is sized from the simulation's own new-customer counts so CAC lands in a
plausible range per channel -- but the spend feed knows nothing about customers.
Its channel vocabulary doesn't match the signup form's, it relabels itself
mid-history, one feed has an outage, and some months get restated.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from .config import UTC, Config

# campaign_id: (feed channel label, campaign name, customer channel it drives, share of that channel)
CAMPAIGNS = {
    "gads-brand":      ("google_ads",      "Search - Brand",            "paid_search", 0.25),
    "gads-nonbrand":   ("google_ads",      "Search - Non-brand",        "paid_search", 0.45),
    "gads-competitor": ("google_ads",      "Search - Competitor terms", "paid_search", 0.15),
    "bing-core":       ("bing_ads",        "Bing - Core",               "paid_search", 0.15),
    "li-abm":          ("linkedin_ads",    "LinkedIn - ABM",            "paid_social", 0.60),
    "li-retarget":     ("linkedin_ads",    "LinkedIn - Retargeting",    "paid_social", 0.15),
    "meta-prospect":   ("meta_ads",        "Meta - Prospecting",        "paid_social", 0.25),
    "evt-tradeshows":  ("events",          "Trade shows",               "events",      0.80),
    "evt-webinars":    ("events",          "Webinars",                  "events",      0.20),
    "partner-mdf":     ("partner_program", "Partner MDF",               "partner",     1.00),
    "content-synd":    ("content",         "Content syndication",       "organic",     0.70),
    "seo-tools":       ("content",         "SEO tooling",               "organic",     0.30),
    "ref-rewards":     ("referral_program", "Referral rewards",         "referral",    1.00),
    "sdr-data":        ("sales_tools",     "Outbound data & sequencing", "outbound",   1.00),
}
# Ad-spend per new customer, by the customer channel it drives. Sales salaries
# aren't in this feed, which is exactly why "CAC" needs a definition.
TARGET_CAC = {"paid_search": 1_400, "paid_social": 2_200, "events": 6_500, "partner": 900,
              "organic": 250, "referral": 300, "outbound": 450}
CPC = {"google_ads": 4.5, "bing_ads": 3.2, "linkedin_ads": 9.0, "meta_ads": 1.8}

# The feed relabels itself mid-history.
OLD_LABELS = {"google_ads": ("Google Ads", date(2025, 6, 1)),
              "linkedin_ads": ("LinkedIn", date(2025, 3, 1))}
OUTAGE = ("bing-core", date(2025, 8, 11), date(2025, 8, 20))   # inclusive: no rows at all
N_RESTATED_MONTHS = 3


def generate(sim, cfg: Config) -> list[tuple]:
    r = random.Random(cfg.seed + 11)
    month_key = lambda d: (d.year, d.month)

    new_by = defaultdict(int)  # (channel, (y, m)) -> new paying customers
    for channel, ts in sim.first_paid.values():
        if channel:
            new_by[(channel, month_key(ts.date()))] += 1

    months = []
    d = cfg.history_start.replace(day=1)
    while d <= cfg.as_of:
        months.append(month_key(d))
        d = (d + timedelta(days=32)).replace(day=1)

    # Budget for month m anticipates conversions in m and m+1 (spend leads revenue).
    budget = {}
    for ch, cac in TARGET_CAC.items():
        for i, m in enumerate(months):
            nxt = months[i + 1] if i + 1 < len(months) else m
            n = 0.5 * new_by[(ch, m)] + 0.5 * new_by[(ch, nxt)] + 0.5
            budget[(ch, m)] = n * cac * r.lognormvariate(0, 0.15)

    rows, spend_id = [], 1
    restated = {}
    for (y, mo) in r.sample(months[1:-1], min(N_RESTATED_MONTHS, max(len(months) - 2, 0))):
        last = (date(y, mo, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        restated[(y, mo)] = datetime.combine(last + timedelta(days=r.randint(8, 20)), time(9), UTC)
    day = cfg.history_start
    while day <= cfg.as_of:
        m = month_key(day)
        days_in_month = ((day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day
        wk = 1.15 if day.weekday() < 5 else 0.62
        for cid, (label, cname, ch, share) in CAMPAIGNS.items():
            if cid == OUTAGE[0] and OUTAGE[1] <= day <= OUTAGE[2]:
                continue
            if ch == "events" and r.random() < 0.75:
                continue        # event spend is lumpy: a few big days a month
            amt = budget[(ch, m)] * share / days_in_month * wk * r.lognormvariate(0, 0.25)
            if ch == "events":
                amt *= 4.0
            amt = round(amt, 2)
            if amt <= 0:
                continue
            feed = label
            if label in OLD_LABELS and day < OLD_LABELS[label][1]:
                feed = OLD_LABELS[label][0]
            cpc = CPC.get(label)
            clicks = int(amt / cpc * r.uniform(0.8, 1.2)) if cpc else None
            imps = int(clicks / r.uniform(0.015, 0.05)) if clicks else None
            reported = datetime.combine(day + timedelta(days=1), time(6), UTC) + timedelta(minutes=r.uniform(0, 180))
            rows.append((spend_id, day, feed, cid, cname, amt, imps, clicks, reported))
            spend_id += 1
            if m in restated:
                # the platform re-sends the whole month later with corrected amounts
                rt = restated[m] + timedelta(seconds=r.uniform(0, 900))
                if rt < cfg.end_ts:
                    rows.append((spend_id, day, feed, cid, cname, round(amt * r.uniform(0.92, 1.08), 2),
                                 imps, clicks, rt))
                    spend_id += 1
        day += timedelta(days=1)
    return rows
