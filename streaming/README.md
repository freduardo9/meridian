# streaming/ -- Kafka and Flink jobs (Phases 3–4)

Debezium connector configs (Phase 3) and Flink SQL jobs over the usage-event
stream (Phase 4). The Postgres source already runs with `wal_level=logical`,
so CDC needs no rebuild.
