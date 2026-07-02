CREATE TABLE IF NOT EXISTS ping (
    ping_id SERIAL PRIMARY KEY,
    test_id INTEGER NOT NULL,
    latency REAL,
    jitter REAL,
    low REAL,
    high REAL,
    CONSTRAINT ping_test_id_fkey FOREIGN KEY (test_id) REFERENCES test(test_id) ON DELETE CASCADE
);
