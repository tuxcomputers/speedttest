CREATE TABLE IF NOT EXISTS upload (
    upload_id SERIAL PRIMARY KEY,
    test_id INTEGER NOT NULL,
    bandwidth_mbps REAL,
    bytes BIGINT,
    elapsed INTEGER,
    latency_iqm REAL,
    latency_low REAL,
    latency_high REAL,
    latency_jitter REAL,
    CONSTRAINT upload_test_id_fkey FOREIGN KEY (test_id) REFERENCES test(test_id) ON DELETE CASCADE
);
