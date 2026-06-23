CREATE TABLE IF NOT EXISTS connectivity (
    host_id INTEGER PRIMARY KEY,
    last_checked TIMESTAMPTZ NOT NULL,
    is_connected BOOLEAN NOT NULL,
    FOREIGN KEY (host_id) REFERENCES host(host_id)
);
