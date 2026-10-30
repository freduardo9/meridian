# Source data guide

What the generator puts in Postgres, how the company behind it works, and every
defect that's in there on purpose. Read this before writing a staging model.

This guide describes the data. It doesn't tell you how to model it -- that's
Phase 1.

---

## The company

Meridian sells a seat-based analytics product to businesses.

- **Customers** are accounts (companies). Each has **users**, roughly one per seat.
- **Segments** shape behaviour but are *not* stored: small businesses (~60% of
  signups, 1–15 seats), mid-market (~30%, ~8–80 seats), enterprise (~10%, 25+
  seats). You can infer segment from `employee_band` and seat counts; the
  generator never writes it down, the way a real app usually doesn't.
- **Self-serve** customers start a 14-day trial; most enterprise deals are
  sales-led and start paid.
- Subscription history begins **2024-01-01**. The snapshot is **as of
  2026-09-30** (everything at or after midnight UTC on 2026-10-01 is "the future"
  and isn't in the data). Usage events cover **2025-10-01 → 2026-09-30**.

### Plans

| plan_id | plan_code | interval | list price (per seat) | notes |
|---|---|---|---|---|
| 1 | `starter_monthly` | month | $15 / month | |
| 2 | `starter_annual` | year | $150 / year | |
| 3 | `team_monthly` | month | $25 / month | **retired 2025-01-01**; existing customers kept it |
| 4 | `growth_monthly` | month | $35 → **$39** from 2026-04-01 | |
| 5 | `growth_annual` | year | $350 → **$390** from 2026-04-01 | |
| 6 | `pro_monthly` | month | $75 / month | |
| 7 | `pro_annual` | year | $750 / year | |
| 8 | `enterprise_annual` | year | $1,200 / year list | every deal is negotiated below list |

`unit_amount_cents` is always **per seat, per billing interval** -- an annual
plan's price is per seat per *year*.

---

## Tables

### `app` -- the product's database

| Table | Grain | Notes |
|---|---|---|
| `plans` | one row per plan | `tier_rank` orders plans cheapest → most expensive |
| `plan_prices` | one row per plan per price period | `effective_to` is exclusive; NULL = current |
| `customers` | one row per account, **current state** | |
| `customers_audit` | one row per INSERT / UPDATE / DELETE | full row image at `changed_at` |
| `users` | one row per user | `deactivated_at` when a seat is given up |
| `subscriptions` | one row per subscription, **current state** | `unit_amount_cents` is the *contracted* price |
| `subscription_events` | one row per change delivered by the billing webhook | `occurred_at` vs `effective_at` vs `received_at` |

Subscription event types: `trial_started`, `trial_converted`, `trial_expired`,
`subscription_created`, `plan_changed`, `seats_changed`,
`cancellation_requested`, `cancellation_reversed`, `canceled`,
`payment_failed`, `payment_recovered`, `renewed`, `reactivated`.

`plan_changed` doesn't say whether it was an upgrade or a downgrade. Neither do
real billing systems.

### `billing` -- the invoicing provider

| Table | Grain | Notes |
|---|---|---|
| `invoices` | one row per invoice | `billing_reason`: `subscription_create`, `subscription_cycle`, `subscription_update` |
| `invoice_lines` | one row per line | `line_type`: `subscription`, `proration`, `discount` (negative) |
| `discounts` | one row per coupon applied | `ends_at` NULL = forever |

Money is in **cents** (integers).

### `marketing` -- ad-platform export

| Table | Grain | Notes |
|---|---|---|
| `spend` | one row per campaign per day **per report** | money is in **dollars** (`numeric`) |

### `events` -- product analytics landing table

| Table | Grain | Notes |
|---|---|---|
| `usage_events` | one row per delivery, **not** per event | no primary key; ~10M rows |

Physical order is arrival order (`received_at`), the way an append-only landing
table fills up.

---

## The defects

Every one of these is deliberate. Most are things you'll meet in a real source.

### Revenue and subscriptions

| # | Defect | Where to see it |
|---|---|---|
| R1 | **Billing periods anchor on the start date**, not the calendar month. A customer who starts on the 17th is billed 17th → 17th; one who starts on the 31st is billed on the 28th/29th/30th in short months. | `subscriptions.current_period_start`, `invoices.period_start` |
| R2 | **Upgrades and seat additions are immediate and prorated**; a separate `subscription_update` invoice credits unused time on the old plan and charges the new one. | `invoice_lines.line_type = 'proration'` |
| R3 | **Downgrades and seat reductions are scheduled** for the end of the period. They're logged when requested, with a later `effective_at`. Some were requested before the as-of date and haven't happened yet. | `subscription_events.effective_at > occurred_at` |
| R4 | **Rarely, a scheduled downgrade is overtaken by a cancellation** -- the downgrade event exists but never took effect. | same subscription: `plan_changed` with a future `effective_at`, then `canceled` |
| R5 | **Cancelling is not ending.** `cancellation_requested` sets `cancel_at_period_end`; access continues until the `canceled` event at period end. Annual customers can announce churn months before it happens. | `subscriptions.canceled_at` vs `ended_at` |
| R6 | **Some cancellations are withdrawn.** | `cancellation_reversed` |
| R7 | **Failed payments.** A cycle invoice fails, the subscription goes `past_due`, and either recovers within two weeks or is cancelled after 21 days of dunning (involuntary churn, `reason = 'payment_failed'`). | `payment_failed`, `payment_recovered`, `invoices.status = 'uncollectible'` |
| R8 | **Grandfathered prices.** Growth went from $35 to $39 on 2026-04-01. Monthly customers who signed before keep $35 until they change plan; annual customers re-price at renewal. List price ≠ contracted price. | `subscriptions.unit_amount_cents` vs `plan_prices` |
| R9 | **A retired plan is still billing.** Team was retired on 2025-01-01. | `plans.is_active = false`, live subscriptions on `plan_id = 3` |
| R10 | **Negotiated enterprise pricing**, uplifted 0–7% at each renewal. | `plan_id = 8` |
| R11 | **Coupons**, some time-boxed, some forever. Contracted price minus discount ≠ contracted price. | `billing.discounts`, `invoice_lines.line_type = 'discount'` |
| R12 | **Monthly → annual switches** at a period boundary change the plan, the interval and the billing anchor at once. | `plan_changed` where the interval differs |
| R13 | **Enterprise invoices are net-30**; some issued before the as-of date are still unpaid. | `billing_reason = 'subscription_create'`, `status = 'open'` |
| R14 | **Voided and reissued invoices.** The void stays in the table. | `invoices.status = 'void'` |
| R15 | **$0 trial invoices** exist for every trial. | `subtotal_cents = 0` |
| R16 | **Trials that never convert** end with `trial_expired`; the subscription is `canceled` with no cancellation request. | `trial_expired` |
| R17 | **Late conversions** buy a few days after the trial expired -- on a *new* subscription. | customer with a `trial_expired` sub followed by a `subscription_created` |
| R18 | **Churn and return**, three ways: a new subscription on the same account; the same subscription reactivated (`reactivated`); or a brand-new account for the same company. | `subscription_events.reason = 'win_back'`, `reactivated`, shared `domain` |

### Customers

| # | Defect | Where to see it |
|---|---|---|
| C1 | **Duplicate accounts.** A returning company sometimes signs up again; the new account has the same `domain` and a differently typed `account_name` (case, punctuation, a trailing space, "(new account)"). | `domain` shared across `customer_id`s |
| C2 | **Attributes change over time.** Employee bands grow, industries get reclassified, companies rename, HQs move. `customers` only shows the latest; `customers_audit` has every version. | `customers_audit` |
| C3 | **No-op updates.** Some audit rows change nothing. | consecutive audit rows with identical values |
| C4 | **Test accounts** in production data: `TEST - …` names on `.meridian-internal.example` domains, on a 100%-off coupon, generating real usage. A few were only flagged `is_test_account` *after* they'd been around a while. | `is_test_account`, `customers_audit` |
| C5 | **Erased accounts.** A handful of trial accounts had their PII removed (NULL name and domain, NULL user emails) and `deleted_at` set. Their subscriptions, invoices and usage are still there. | `customers.deleted_at` |
| C6 | **Acquisition channel labels changed** when the signup form was rebuilt on 2025-02-01 (`Paid Search` → `paid_search`). About 4% are NULL. | `customers.acquisition_channel` |

### Marketing

| # | Defect | Where to see it |
|---|---|---|
| M1 | **The spend feed's channel vocabulary doesn't match the signup form's.** `google_ads` and `bing_ads` are both what customers call `paid_search`. You'll need a mapping. | `spend.channel` vs `customers.acquisition_channel` |
| M2 | **The feed relabels itself**: `Google Ads` → `google_ads` on 2025-06-01, `LinkedIn` → `linkedin_ads` on 2025-03-01. | `spend.channel` |
| M3 | **Restated months.** Three months were re-sent ~2 weeks after month end with corrected amounts. Both versions are in the table; the later `reported_at` wins. | same `(spend_date, campaign_id)` more than once |
| M4 | **A feed outage**: Bing has no rows for 2025-08-11 through 2025-08-20. Missing, not zero. | gaps in `spend_date` for `bing-core` |
| M5 | **Event spend is lumpy** -- a few large days a month, nothing in between. | `channel = 'events'` |
| M6 | **Sales salaries aren't in the feed.** Outbound has only tooling spend. | `campaign_id = 'sdr-data'` |

### Usage events

| # | Defect | Where to see it |
|---|---|---|
| U1 | **At-least-once delivery.** ~0.4% of events arrive twice: same `event_id`, later `received_at`. | `count(*) - count(DISTINCT event_id)` |
| U2 | **Late arrivals.** ~1.5% arrive 1–72 hours after they happened. | `received_at - occurred_at` |
| U3 | **Client clock skew.** ~0.1% have `occurred_at` *after* `received_at`. | `occurred_at > received_at` |
| U4 | **Usage after churn.** Cancelled customers keep read-only access for ~30 days and produce a trickle of events. | events after `subscriptions.ended_at` |
| U5 | **Usage from test accounts.** | join to `customers.is_test_account` |
| U6 | **Engagement fades before cancellation.** Usage declines over the ~45 days before a cancellation request. Not a defect -- a signal. | usage trend vs `cancellation_requested` |
| U7 | **Not-yet-arrived events.** Anything received after the as-of instant isn't in the table, even if it occurred before it. | the last hours of 2026-09-30 |
| U8 | **API and server events have no `app_version`.** | `client IN ('api', 'server')` |

### Cross-system

| # | Defect | Where to see it |
|---|---|---|
| X1 | **Webhook duplicates.** ~0.5% of subscription events are delivered twice with different `event_id`s. | same `(subscription_id, event_type, occurred_at)` |
| X2 | **Units differ by system**: cents in billing, dollars in marketing. | |
| X3 | **Timestamps are `timestamptz`.** The container runs in UTC; your SQL client may not. A date_trunc in Central time puts midnight-UTC events in the previous day. | set `TimeZone` before trusting a daily rollup |

---

## Questions this data forces

These don't have one right answer. That's the point -- answer them in your ADR.

1. A customer upgrades from Growth to Pro on the 20th of a 30-day period. What
   was their MRR for that month? What was billed? Why are those different?
2. A customer requests a downgrade on the 5th, effective on the 30th. What is
   their ARR on the 10th?
3. An annual customer announces in March that they won't renew in November. Are
   they in your ARR in April?
4. Is a `past_due` subscription in ARR? On day 3 of dunning? Day 20?
5. Is ARR measured at list price, contracted price, or contracted price net of
   discounts? What about a forever coupon vs. a three-month one?
6. A customer churns in February and comes back in June under a new account.
   Is that churn plus new ARR, or neither? What does your answer do to NRR?
7. For CAC: which spend counts, which customers count, and in which month?
   A customer who signed up via a Google Ads click in a month whose spend was
   later restated?

---

## Reproducibility

Every run with the same flags produces the same database. The defaults:

```
--seed 42 --customers 5000 --usage-events 10000000
--history-start 2024-01-01 --as-of 2026-09-30 --usage-months 12
```

What produced the current database is recorded in `meta.seed_runs`.
