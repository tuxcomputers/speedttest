# Database conventions

- All auto-increment primary key columns must be named `<table_name>_id` (e.g. the primary key of the `test` table is `test_id`, the primary key of `outage` is `outage_id`).
- Table names must be singular, not plural (e.g. `host` not `hosts`, `test` not `tests`, `outage` not `outages`).
