-- Phase 0 smoke test: is the database populated, and is the mess actually there?
-- Run with `make profile`. Plain GROUP BYs only -- the interesting SQL is yours.

\pset footer off
\echo '== seed run'
SELECT seed, history_start, as_of, usage_start, seeded_at FROM meta.seed_runs;

\echo '== row counts'
SELECT 'app.customers' AS tbl, count(*) FROM app.customers
UNION ALL SELECT 'app.customers_audit', count(*) FROM app.customers_audit
UNION ALL SELECT 'app.users', count(*) FROM app.users
UNION ALL SELECT 'app.subscriptions', count(*) FROM app.subscriptions
UNION ALL SELECT 'app.subscription_events', count(*) FROM app.subscription_events
UNION ALL SELECT 'billing.invoices', count(*) FROM billing.invoices
UNION ALL SELECT 'billing.invoice_lines', count(*) FROM billing.invoice_lines
UNION ALL SELECT 'billing.discounts', count(*) FROM billing.discounts
UNION ALL SELECT 'marketing.spend', count(*) FROM marketing.spend
UNION ALL SELECT 'events.usage_events', count(*) FROM events.usage_events;

\echo '== subscriptions by status (as of the seed date)'
SELECT status, count(*) FROM app.subscriptions GROUP BY 1 ORDER BY 2 DESC;

\echo '== subscription event types'
SELECT event_type, count(*) FROM app.subscription_events GROUP BY 1 ORDER BY 2 DESC;

\echo '== changes that take effect later than they happen'
SELECT event_type, count(*) AS n
FROM app.subscription_events
WHERE effective_at > occurred_at
GROUP BY 1 ORDER BY 2 DESC;

\echo '== acquisition_channel as the signup form recorded it'
SELECT coalesce(acquisition_channel, '<null>') AS acquisition_channel, count(*)
FROM app.customers GROUP BY 1 ORDER BY 2 DESC;

\echo '== domains that belong to more than one customer_id'
SELECT count(*) AS shared_domains
FROM (SELECT domain FROM app.customers WHERE domain IS NOT NULL GROUP BY domain HAVING count(*) > 1) d;

\echo '== customers with more than one subscription'
SELECT count(*) AS customers
FROM (SELECT customer_id FROM app.subscriptions GROUP BY 1 HAVING count(*) > 1) s;

\echo '== test accounts and deletions'
SELECT is_test_account, (deleted_at IS NOT NULL) AS deleted, count(*)
FROM app.customers GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== audit operations'
SELECT operation, count(*) FROM app.customers_audit GROUP BY 1;

\echo '== invoices by status and reason'
SELECT billing_reason, status, count(*), sum(total_cents) / 100 AS total_usd
FROM billing.invoices GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== plans in use (contracted price vs today''s list price)'
SELECT p.plan_code, s.status, count(*) AS subs,
       min(s.unit_amount_cents) AS min_unit, max(s.unit_amount_cents) AS max_unit
FROM app.subscriptions s JOIN app.plans p USING (plan_id)
WHERE s.status <> 'canceled'
GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== spend feed channel labels'
SELECT channel, min(spend_date), max(spend_date), count(*) FROM marketing.spend GROUP BY 1 ORDER BY 1;

\echo '== spend rows reported more than once for the same day and campaign'
SELECT count(*) AS restated_keys
FROM (SELECT spend_date, campaign_id FROM marketing.spend GROUP BY 1, 2 HAVING count(*) > 1) x;

\echo '== usage: duplicates, late arrivals, clock skew'
SELECT count(*) - count(DISTINCT event_id)                                    AS duplicate_rows,
       count(*) FILTER (WHERE received_at - occurred_at > interval '1 hour')  AS late_over_1h,
       count(*) FILTER (WHERE occurred_at > received_at)                      AS occurred_after_received
FROM events.usage_events;
