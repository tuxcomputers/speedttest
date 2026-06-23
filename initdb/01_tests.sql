CREATE TABLE IF NOT EXISTS test (
    test_id SERIAL PRIMARY KEY,
    host_id INTEGER NOT NULL,
    timestamp TIMESTAMPTZ,
    isp TEXT,
    packet_loss REAL,
    result_id TEXT,
    result_url TEXT,
    FOREIGN KEY (host_id) REFERENCES host(host_id)
);
