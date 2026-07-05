CREATE TABLE IF NOT EXISTS outage (
    outage_id SERIAL PRIMARY KEY,
    host_id INTEGER NOT NULL,
    start_time TIMESTAMP(0) NOT NULL,
    end_time TIMESTAMP(0),
    status TEXT NOT NULL,
    CONSTRAINT outage_host_id_fkey FOREIGN KEY (host_id) REFERENCES host(host_id) ON DELETE CASCADE
);

-- Backstop against concurrent syncs double-inserting the same closed outage.
-- Open outages (end_time IS NULL) are never synced, and NULLs compare
-- distinct in a unique index, so they are unaffected.
CREATE UNIQUE INDEX IF NOT EXISTS idx_outage_host_window ON outage(host_id, start_time, end_time);
