-- Brings databases initialised before the migration system existed up to the
-- current schema. Idempotent: safe to run on a freshly initialised database.
-- Applied by db/migrate.sh inside a single transaction.

-- 1. Foreign keys gain ON DELETE CASCADE so pruning a host or test doesn't
--    orphan child rows.
ALTER TABLE test           DROP CONSTRAINT IF EXISTS test_host_id_fkey;
ALTER TABLE test           ADD  CONSTRAINT test_host_id_fkey           FOREIGN KEY (host_id) REFERENCES host(host_id) ON DELETE CASCADE;
ALTER TABLE ping           DROP CONSTRAINT IF EXISTS ping_test_id_fkey;
ALTER TABLE ping           ADD  CONSTRAINT ping_test_id_fkey           FOREIGN KEY (test_id) REFERENCES test(test_id) ON DELETE CASCADE;
ALTER TABLE download       DROP CONSTRAINT IF EXISTS download_test_id_fkey;
ALTER TABLE download       ADD  CONSTRAINT download_test_id_fkey       FOREIGN KEY (test_id) REFERENCES test(test_id) ON DELETE CASCADE;
ALTER TABLE upload         DROP CONSTRAINT IF EXISTS upload_test_id_fkey;
ALTER TABLE upload         ADD  CONSTRAINT upload_test_id_fkey         FOREIGN KEY (test_id) REFERENCES test(test_id) ON DELETE CASCADE;
ALTER TABLE server         DROP CONSTRAINT IF EXISTS server_test_id_fkey;
ALTER TABLE server         ADD  CONSTRAINT server_test_id_fkey         FOREIGN KEY (test_id) REFERENCES test(test_id) ON DELETE CASCADE;
ALTER TABLE outage         DROP CONSTRAINT IF EXISTS outage_host_id_fkey;
ALTER TABLE outage         ADD  CONSTRAINT outage_host_id_fkey         FOREIGN KEY (host_id) REFERENCES host(host_id) ON DELETE CASCADE;
ALTER TABLE network_status DROP CONSTRAINT IF EXISTS network_status_host_id_fkey;
ALTER TABLE network_status ADD  CONSTRAINT network_status_host_id_fkey FOREIGN KEY (host_id) REFERENCES host(host_id) ON DELETE CASCADE;

-- 2. Remove duplicates left behind by the old racy check-then-insert sync
--    (keep the lowest id; children of duplicate tests are removed by the
--    cascade added above).
DELETE FROM test t
USING test t2
WHERE t.host_id = t2.host_id
  AND t.timestamp = t2.timestamp
  AND t.test_id > t2.test_id;

DELETE FROM outage o
USING outage o2
WHERE o.host_id = o2.host_id
  AND o.start_time = o2.start_time
  AND o.end_time = o2.end_time
  AND o.outage_id > o2.outage_id;

-- 3. Unique indexes: make sync idempotency a database guarantee.
CREATE UNIQUE INDEX IF NOT EXISTS idx_test_host_timestamp ON test(host_id, timestamp);
CREATE UNIQUE INDEX IF NOT EXISTS idx_outage_host_window  ON outage(host_id, start_time, end_time);

-- 4. host_hash is the identity key; hostname is just a label. If this index
--    fails, two host rows share a host_hash (an artifact of the old unstable
--    in-container fallback). Inspect with:
--        SELECT host_hash, array_agg(host_id) FROM host GROUP BY host_hash HAVING COUNT(*) > 1;
--    then merge/delete the spare rows and re-run the migration.
CREATE UNIQUE INDEX IF NOT EXISTS idx_host_host_hash ON host(host_hash);
ALTER TABLE host DROP CONSTRAINT IF EXISTS host_hostname_key;

-- 5. Seed the remotely-managed speed test interval.
INSERT INTO setting (setting, value) VALUES ('speedtest_interval_min', '5')
ON CONFLICT (setting) DO NOTHING;
