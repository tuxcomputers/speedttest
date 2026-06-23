CREATE TABLE IF NOT EXISTS outage (
    outage_id SERIAL PRIMARY KEY,
    host_id INTEGER NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    status TEXT NOT NULL,
    FOREIGN KEY (host_id) REFERENCES host(host_id)
);
