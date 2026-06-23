CREATE TABLE IF NOT EXISTS ping (
    ping_id SERIAL PRIMARY KEY,
    test_id INTEGER NOT NULL,
    latency REAL,
    jitter REAL,
    low REAL,
    high REAL,
    FOREIGN KEY (test_id) REFERENCES tests(test_id)
);
