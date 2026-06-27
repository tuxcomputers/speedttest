CREATE TABLE IF NOT EXISTS host (
    host_id      SERIAL PRIMARY KEY,
    hostname     TEXT NOT NULL UNIQUE,
    timezone     TEXT NOT NULL DEFAULT 'UTC',
    host_hash    TEXT,
    remote_id    TEXT,
    last_db_sync TIMESTAMP(0)
);
