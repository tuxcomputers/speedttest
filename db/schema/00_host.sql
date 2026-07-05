CREATE TABLE IF NOT EXISTS host (
    host_id      SERIAL PRIMARY KEY,
    hostname     TEXT NOT NULL,
    timezone     TEXT NOT NULL DEFAULT 'UTC',
    host_hash    TEXT,
    remote_id    TEXT,
    last_db_sync TIMESTAMP(0)
);

-- host_hash is the host identity key (hostname is just a label and may
-- collide between machines, e.g. two default-named Raspberry Pis).
CREATE UNIQUE INDEX IF NOT EXISTS idx_host_host_hash ON host(host_hash);
