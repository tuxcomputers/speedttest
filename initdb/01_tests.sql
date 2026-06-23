CREATE TABLE IF NOT EXISTS tests (
    test_id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ,
    isp TEXT,
    packet_loss REAL,
    result_id TEXT,
    result_url TEXT
);
