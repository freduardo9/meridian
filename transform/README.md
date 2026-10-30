# transform/ -- dbt project (Phase 1)

Empty on purpose. Every model in here is hand-typed.

Planned shape:

```
transform/
├── models/
│   ├── staging/        # one model per source table: rename, cast, nothing else
│   ├── intermediate/   # joins and the hard logic
│   └── marts/          # dim_customer (SCD2), dim_plan, dim_date,
│                       # fct_subscription_events, fct_usage_daily, metrics
├── snapshots/
├── tests/
└── dbt_project.yml
```

Sources are the four Postgres schemas -- `app`, `billing`, `marketing`, `events`.
Read [`docs/source-data-guide.md`](../docs/source-data-guide.md) before the first
staging model.
