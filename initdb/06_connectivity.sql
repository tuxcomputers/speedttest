CREATE TABLE IF NOT EXISTS network_status (
    host_id                  INTEGER PRIMARY KEY,
    last_connectivity_check  TIMESTAMPTZ,
    is_connected             BOOLEAN NOT NULL DEFAULT FALSE,
    last_db_write            TIMESTAMPTZ,
    FOREIGN KEY (host_id) REFERENCES host(host_id)
);
