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
    CONSTRAINT server_test_id_fkey FOREIGN KEY (test_id) REFERENCES test(test_id) ON DELETE CASCADE
);
