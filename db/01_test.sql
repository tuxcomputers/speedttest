CREATE TABLE IF NOT EXISTS test (
    test_id SERIAL PRIMARY KEY,
    host_id INTEGER NOT NULL,
    timestamp TIMESTAMP(0),
    isp TEXT,
    packet_loss REAL,
    result_id TEXT,
    result_url TEXT,
    CONSTRAINT test_host_id_fkey FOREIGN KEY (host_id) REFERENCES host(host_id) ON DELETE CASCADE
);

-- Enforces sync idempotency at the database level: concurrent syncs racing
-- the application-side duplicate check get a unique violation instead of a
-- duplicate row.
CREATE UNIQUE INDEX IF NOT EXISTS idx_test_host_timestamp ON test(host_id, timestamp);
