# Meridian

A data platform for a fictional B2B SaaS company, built up in phases on a laptop.

Meridian sells a seat-based analytics product on monthly and annual plans. Its
operational systems -- product database, billing, ad-platform spend, product
analytics -- are simulated by a Python generator. Everything downstream of those
systems is the actual project: a dimensional model and metrics layer in dbt, an
Iceberg lakehouse served by Trino, CDC and streaming ingestion, and finally the
whole stack on Kubernetes.

All data is synthetic and reproducible from a seed. Nothing here is drawn from
any real company.

## Phases

| Phase | What it adds | Status |
|---|---|---|
| 0 | Postgres source systems + synthetic data generator | ✅ |
| 1 | dbt: staging / intermediate / marts, SCD Type 2, ARR / CAC / churn metrics | |
| 2 | Iceberg on MinIO + Trino; partitioning, time travel, compaction | |
| 3 | Airbyte and Debezium CDC from the Postgres source | |
| 4 | Redpanda + Flink: streaming usage events into Iceberg | |
| 5 | The stack on k3d with Helm, ArgoCD and operators | |

Each phase gets an Architecture Decision Record in [`docs/adr/`](docs/adr/).

## Layout

```
meridian/
├── generator/          # Python: synthetic customers, subscriptions, invoices, usage, spend
├── infra/
│   ├── compose/        # one compose file per phase
│   └── k8s/            # Phase 5
├── warehouse/          # Iceberg DDL and partition specs (Phase 2)
├── transform/          # dbt project (Phase 1)
├── streaming/          # Kafka / Flink jobs (Phase 4)
├── catalog/            # YAML entity model
├── sql/                # ad-hoc checks against the source
└── docs/               # ADRs, source-data guide, setup notes
```

## Quick start

Requires Docker Desktop (WSL2 backend on Windows) and `make`. On Windows, run
these from a WSL shell -- see [`docs/setup-windows.md`](docs/setup-windows.md).

```bash
make doctor     # checks WSL, Docker, RAM, line endings, git -- fixes nothing, just reports
make seed       # start Postgres, build the generator, seed ~10M rows (~5 min)
make profile    # row counts and a tour of what's in there
make psql       # poke around
```

`make seed-quick` builds a 500-customer world in about fifteen seconds for when
you're iterating. `make seed` and `make seed-quick` both drop and rebuild the
source schemas, so re-running is always safe.

Connect any client to `localhost:5433`, database `meridian`, user and password
`meridian`. (5433, not 5432, so it never fights a local Postgres install.)

<details>
<summary>Without make (PowerShell)</summary>

```powershell
docker compose -f infra/compose/phase0.yml up -d --wait postgres
docker compose -f infra/compose/phase0.yml run --rm --build generator          # full
docker compose -f infra/compose/phase0.yml run --rm --build generator --quick  # small
Get-Content sql/profile.sql | docker compose -f infra/compose/phase0.yml exec -T postgres psql -U meridian -d meridian
docker compose -f infra/compose/phase0.yml down        # keep data
docker compose -f infra/compose/phase0.yml down -v     # delete data
```
</details>

## The source data

The default seed (`--seed 42`) produces:

| Table | Rows | What it is |
|---|---:|---|
| `app.customers` | ~5,000 | accounts, current state |
| `app.customers_audit` | ~7,000 | row-image history of every change to an account |
| `app.users` | ~110,000 | people in each account |
| `app.subscriptions` | ~5,200 | subscriptions, current state |
| `app.subscription_events` | ~15,000 | every trial, conversion, plan / seat change, cancellation |
| `billing.invoices` / `invoice_lines` | ~25,600 / ~27,000 | what was actually billed, prorations included |
| `billing.discounts` | ~400 | coupons applied to subscriptions |
| `marketing.spend` | ~13,600 | daily ad spend by campaign |
| `events.usage_events` | ~10,000,000 | product analytics, trailing 12 months |

Subscription history runs from January 2024 to the as-of date (30 Sep 2026);
usage events cover the trailing twelve months.

The data is deliberately untidy -- mid-period plan changes, downgrades that take
effect later than they're requested, grandfathered prices, trials that never
convert, customers who churn and come back under a new account, a spend feed
that relabels its channels mid-history, duplicate and late-arriving events.
[`docs/source-data-guide.md`](docs/source-data-guide.md) lists every defect and
where to find it.

## How this repo is built

The infrastructure, the generator and the docs are written with Claude Code.
The things this project exists to teach -- every dbt model, every Iceberg DDL
and partition spec, every metric definition -- are written by hand.
