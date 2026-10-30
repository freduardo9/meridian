-- =============================================================================
-- Meridian source systems (Phase 0)
--
-- This is the OPERATIONAL side of a fictional B2B SaaS company: the tables its
-- product, billing system and marketing tools would own. It is the *input* to
-- the warehouse, not the warehouse. Nothing here is modeled for analytics on
-- purpose -- that is Phase 1's job, and it's yours to type.
--
-- Four schemas stand in for four source systems:
--   app        the product's own Postgres (accounts, users, subscriptions)
--   billing    the billing provider (invoices, line items, coupons)
--   marketing  the ad-platform / spend export
--   events     the product-analytics event landing table
--
-- The generator drops and recreates all of this on every seed, so a seed is
-- always reproducible from --seed.
-- =============================================================================

DROP SCHEMA IF EXISTS app       CASCADE;
DROP SCHEMA IF EXISTS billing   CASCADE;
DROP SCHEMA IF EXISTS marketing CASCADE;
DROP SCHEMA IF EXISTS events    CASCADE;
DROP SCHEMA IF EXISTS meta      CASCADE;

CREATE SCHEMA app;
CREATE SCHEMA billing;
CREATE SCHEMA marketing;
CREATE SCHEMA events;
CREATE SCHEMA meta;

-- ---------------------------------------------------------------------------
-- app: product database
-- ---------------------------------------------------------------------------

CREATE TABLE app.plans (
    plan_id          integer      PRIMARY KEY,
    plan_code        text         NOT NULL UNIQUE,
    plan_name        text         NOT NULL,
    tier_rank        integer      NOT NULL,           -- 1 = cheapest
    billing_interval text         NOT NULL CHECK (billing_interval IN ('month', 'year')),
    is_active        boolean      NOT NULL,           -- false = retired, no new sales
    created_at       timestamptz  NOT NULL,
    retired_at       timestamptz
);

-- List prices. One row per price period. unit_amount_cents is per seat, per
-- billing interval (so an annual plan's price is per seat per YEAR).
CREATE TABLE app.plan_prices (
    plan_price_id     integer     PRIMARY KEY,
    plan_id           integer     NOT NULL REFERENCES app.plans,
    unit_amount_cents integer     NOT NULL,
    currency          text        NOT NULL DEFAULT 'USD',
    effective_from    date        NOT NULL,
    effective_to      date                              -- exclusive; NULL = current
);

-- Current state only, the way an application table usually is.
-- History lives in app.customers_audit.
CREATE TABLE app.customers (
    customer_id          bigint      PRIMARY KEY,
    account_name         text,
    domain               text,
    industry             text,
    employee_band        text,
    country_code         text,
    region               text,
    acquisition_channel  text,
    is_test_account      boolean     NOT NULL DEFAULT false,
    signup_at            timestamptz NOT NULL,
    created_at           timestamptz NOT NULL,
    updated_at           timestamptz NOT NULL,
    deleted_at           timestamptz
);

-- Row-image audit log, as written by an application trigger: one full copy of
-- the row per INSERT / UPDATE / DELETE.
CREATE TABLE app.customers_audit (
    audit_id             bigint      PRIMARY KEY,
    operation            text        NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    changed_at           timestamptz NOT NULL,
    customer_id          bigint      NOT NULL,
    account_name         text,
    domain               text,
    industry             text,
    employee_band        text,
    country_code         text,
    region               text,
    acquisition_channel  text,
    is_test_account      boolean
);

CREATE TABLE app.users (
    user_id         bigint      PRIMARY KEY,
    customer_id     bigint      NOT NULL REFERENCES app.customers,
    email           text,
    full_name       text,
    role            text        NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    created_at      timestamptz NOT NULL,
    deactivated_at  timestamptz
);

-- Current state of each subscription as of the seed's as-of date.
CREATE TABLE app.subscriptions (
    subscription_id       bigint      PRIMARY KEY,
    customer_id           bigint      NOT NULL REFERENCES app.customers,
    plan_id               integer     NOT NULL REFERENCES app.plans,
    status                text        NOT NULL CHECK (status IN ('trialing', 'active', 'past_due', 'canceled')),
    seats                 integer     NOT NULL,
    unit_amount_cents     integer     NOT NULL,   -- contracted price per seat per interval
    trial_start           timestamptz,
    trial_end             timestamptz,
    current_period_start  timestamptz,
    current_period_end    timestamptz,
    cancel_at_period_end  boolean     NOT NULL DEFAULT false,
    canceled_at           timestamptz,             -- when the cancel was *requested*
    ended_at              timestamptz,             -- when access actually stopped
    created_at            timestamptz NOT NULL,
    updated_at            timestamptz NOT NULL
);

-- Append-only change log for subscriptions, as emitted by the billing
-- webhook consumer. occurred_at = when it happened; effective_at = when it
-- takes effect. They are not always the same.
CREATE TABLE app.subscription_events (
    event_id                 bigint      PRIMARY KEY,
    subscription_id          bigint      NOT NULL,
    customer_id              bigint      NOT NULL,
    event_type               text        NOT NULL,
    occurred_at              timestamptz NOT NULL,
    effective_at             timestamptz NOT NULL,
    prev_plan_id             integer,
    new_plan_id              integer,
    prev_seats               integer,
    new_seats                integer,
    prev_unit_amount_cents   integer,
    new_unit_amount_cents    integer,
    reason                   text,
    received_at              timestamptz NOT NULL
);

-- ---------------------------------------------------------------------------
-- billing: invoicing provider
-- ---------------------------------------------------------------------------

CREATE TABLE billing.discounts (
    discount_id      bigint      PRIMARY KEY,
    subscription_id  bigint      NOT NULL,
    customer_id      bigint      NOT NULL,
    coupon_code      text        NOT NULL,
    percent_off      numeric(5,2) NOT NULL,
    starts_at        timestamptz NOT NULL,
    ends_at          timestamptz                    -- NULL = forever
);

CREATE TABLE billing.invoices (
    invoice_id      bigint      PRIMARY KEY,
    invoice_number  text        NOT NULL UNIQUE,
    customer_id     bigint      NOT NULL,
    subscription_id bigint      NOT NULL,
    status          text        NOT NULL CHECK (status IN ('open', 'paid', 'void', 'uncollectible')),
    billing_reason  text        NOT NULL,
    period_start    timestamptz NOT NULL,
    period_end      timestamptz NOT NULL,
    issued_at       timestamptz NOT NULL,
    due_at          timestamptz NOT NULL,
    paid_at         timestamptz,
    voided_at       timestamptz,
    subtotal_cents  bigint      NOT NULL,
    discount_cents  bigint      NOT NULL,
    total_cents     bigint      NOT NULL,
    currency        text        NOT NULL DEFAULT 'USD'
);

CREATE TABLE billing.invoice_lines (
    invoice_line_id    bigint      PRIMARY KEY,
    invoice_id         bigint      NOT NULL REFERENCES billing.invoices,
    line_type          text        NOT NULL CHECK (line_type IN ('subscription', 'proration', 'discount')),
    description        text        NOT NULL,
    plan_id            integer,
    seats              integer,
    unit_amount_cents  integer,
    amount_cents       bigint      NOT NULL,
    period_start       timestamptz NOT NULL,
    period_end         timestamptz NOT NULL
);

-- ---------------------------------------------------------------------------
-- marketing: daily spend export from the ad platforms
-- ---------------------------------------------------------------------------

-- Note the unit: dollars, not cents. Different system, different convention.
CREATE TABLE marketing.spend (
    spend_id       bigint        PRIMARY KEY,
    spend_date     date          NOT NULL,
    channel        text          NOT NULL,
    campaign_id    text          NOT NULL,
    campaign_name  text          NOT NULL,
    amount_usd     numeric(12,2) NOT NULL,
    impressions    bigint,
    clicks         bigint,
    reported_at    timestamptz   NOT NULL
);

-- ---------------------------------------------------------------------------
-- events: product analytics landing table
-- ---------------------------------------------------------------------------

-- Raw landing table. Deliberately no primary key: the collector delivers
-- at-least-once, so event_id is NOT unique here.
CREATE TABLE events.usage_events (
    event_id     uuid        NOT NULL,
    customer_id  bigint      NOT NULL,
    user_id      bigint      NOT NULL,
    event_name   text        NOT NULL,
    occurred_at  timestamptz NOT NULL,   -- client clock
    received_at  timestamptz NOT NULL,   -- collector clock
    client       text        NOT NULL,
    app_version  text
);

-- ---------------------------------------------------------------------------
-- meta: what produced this database
-- ---------------------------------------------------------------------------

CREATE TABLE meta.seed_runs (
    seeded_at       timestamptz NOT NULL DEFAULT now(),
    seed            integer     NOT NULL,
    history_start   date        NOT NULL,
    as_of           date        NOT NULL,
    usage_start     date        NOT NULL,
    generator_args  jsonb       NOT NULL,
    row_counts      jsonb
);
