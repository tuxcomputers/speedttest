CREATE TABLE IF NOT EXISTS server (
    server_id SERIAL PRIMARY KEY,
    test_id INTEGER NOT NULL,
    remote_server_id INTEGER,
    host TEXT,
    port INTEGER,
    name TEXT,
    location TEXT,
    country TEXT,
    ip TEXT,
    FOREIGN KEY (test_id) REFERENCES tests(test_id)
);
