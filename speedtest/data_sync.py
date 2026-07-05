import fcntl
import os

import psycopg2
from psycopg2 import errors as pg_errors

import local_db
from log_setup import get_logger

log = get_logger('data_sync')

# Settings owned by the central database: pulled from PostgreSQL and
# overwritten locally on every sync. Local values are only pushed up when the
# remote doesn't have the key yet (first server bootstrap).
MANAGED_SETTINGS_WHERE = (
    "setting LIKE 'ping_host_%' "
    "OR setting IN ('speedtest_interval_min', 'prune_local_days', 'prune_remote_days')"
)


def has_db_config():
    return bool(os.environ.get('DB_HOST'))


def get_pg_connection():
    return psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', 5432),
        dbname=os.environ['DB_NAME'],
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        sslmode=os.environ.get('DB_SSLMODE', 'prefer'),
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
        options='-c statement_timeout=60000',
    )


def acquire_sync_lock():
    """Take an exclusive lock so overlapping sync processes (a slow sync
    outliving the next 5-minute trigger) can't both push the same unsynced
    rows. Returns the open lock file, or None if another sync holds it."""
    lock_path = os.path.join(os.path.dirname(local_db.get_sqlite_path()) or '.', 'sync.lock')
    lock_file = open(lock_path, 'w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    return lock_file


def resolve_pg_host(pg_cursor, sqlite_conn, local_host_id, hostname, timezone, host_hash):
    if not host_hash:
        raise ValueError("host_hash is required to resolve remote host")

    # host_hash is the identity key; hostname is just a label and is allowed
    # to collide between hosts.
    pg_cursor.execute("SELECT host_id, last_db_sync FROM host WHERE host_hash = %s", (host_hash,))
    row = pg_cursor.fetchone()

    if row:
        pg_host_id, last_db_sync = row[0], row[1]
        pg_cursor.execute(
            "UPDATE host SET hostname = %s, timezone = %s WHERE host_id = %s",
            (hostname, timezone, pg_host_id)
        )
    else:
        pg_cursor.execute(
            "INSERT INTO host (hostname, timezone, host_hash) VALUES (%s, %s, %s) RETURNING host_id",
            (hostname, timezone, host_hash)
        )
        pg_host_id = pg_cursor.fetchone()[0]
        last_db_sync = None

    cursor = sqlite_conn.cursor()
    cursor.execute("SELECT remote_id FROM host WHERE host_id = ?", (local_host_id,))
    local_remote_id = cursor.fetchone()['remote_id']

    if local_remote_id is None or str(local_remote_id) != str(pg_host_id):
        sqlite_conn.execute(
            "UPDATE host SET remote_id = ? WHERE host_id = ?",
            (str(pg_host_id), local_host_id)
        )
        sqlite_conn.commit()

    return pg_host_id, last_db_sync


def mark_synced(sqlite_conn, table, id_column, row_id):
    sqlite_conn.execute(
        f"UPDATE {table} SET synced_at = datetime('now') WHERE {id_column} = ?",  # noqa: S608 — table/column are hardcoded
        (row_id,)
    )
    sqlite_conn.commit()


def sync_settings(sqlite_conn, cursor, pg_conn, pg_cursor):
    pg_cursor.execute(f"SELECT setting, value FROM setting WHERE {MANAGED_SETTINGS_WHERE} ORDER BY setting")
    remote = {row[0]: row[1] for row in pg_cursor.fetchall()}

    cursor.execute(f"SELECT setting, value FROM setting WHERE {MANAGED_SETTINGS_WHERE} ORDER BY setting")
    local = {row['setting']: row['value'] for row in cursor.fetchall()}

    # Remote is the source of truth — pull changed values down.
    changed = {k: v for k, v in remote.items() if local.get(k) != v}
    for setting, value in changed.items():
        sqlite_conn.execute(
            "INSERT INTO setting (setting, value) VALUES (?, ?) ON CONFLICT (setting) DO UPDATE SET value = excluded.value",
            (setting, value)
        )
    if changed:
        sqlite_conn.commit()
        log.info(f"Updated {len(changed)} setting(s) from remote DB: {', '.join(sorted(changed))}")

    # Keys the remote doesn't know yet: push local defaults up.
    missing = {k: v for k, v in local.items() if k not in remote}
    for setting, value in missing.items():
        pg_cursor.execute(
            "INSERT INTO setting (setting, value) VALUES (%s, %s) ON CONFLICT (setting) DO NOTHING",
            (setting, value)
        )
    if missing:
        pg_conn.commit()


def sync():
    sqlite_conn = local_db.get_connection()

    try:
        pg_conn = get_pg_connection()
    except Exception as e:
        log.warning(f"Cannot connect to PostgreSQL: {e}")
        sqlite_conn.close()
        return

    try:
        cursor = sqlite_conn.cursor()

        cursor.execute("SELECT host_id, hostname, timezone, host_hash FROM host LIMIT 1")
        row = cursor.fetchone()
        if not row:
            log.info("No host record in local DB, nothing to sync")
            return

        local_host_id = row['host_id']
        hostname = row['hostname']
        timezone = row['timezone']
        host_hash = row['host_hash']

        pg_cursor = pg_conn.cursor()
        pg_host_id, last_db_sync = resolve_pg_host(pg_cursor, sqlite_conn, local_host_id, hostname, timezone, host_hash)
        pg_conn.commit()

        # No last_db_sync on the remote means this host has never synced to this server —
        # clear local synced_at markers so everything is sent from the beginning.
        if last_db_sync is None:
            sqlite_conn.execute("UPDATE test SET synced_at = NULL")
            sqlite_conn.execute("UPDATE outage SET synced_at = NULL")
            sqlite_conn.commit()
            log.info("First sync to this server — sending all records")

        cursor.execute("SELECT * FROM test WHERE synced_at IS NULL")
        tests = cursor.fetchall()

        skipped_tests = 0
        for t in tests:
            pg_cursor.execute(
                "SELECT test_id FROM test WHERE host_id = %s AND timestamp = %s",
                (pg_host_id, t['timestamp'])
            )
            if pg_cursor.fetchone():
                mark_synced(sqlite_conn, 'test', 'test_id', t['test_id'])
                skipped_tests += 1
                continue

            try:
                pg_cursor.execute(
                    "INSERT INTO test (host_id, timestamp, isp, packet_loss, result_id, result_url) VALUES (%s, %s, %s, %s, %s, %s) RETURNING test_id",
                    (pg_host_id, t['timestamp'], t['isp'], t['packet_loss'], t['result_id'], t['result_url'])
                )
            except pg_errors.UniqueViolation:
                # Backstop for a concurrent sync racing the SELECT above —
                # the unique index on (host_id, timestamp) rejected the copy.
                pg_conn.rollback()
                mark_synced(sqlite_conn, 'test', 'test_id', t['test_id'])
                skipped_tests += 1
                continue
            pg_test_id = pg_cursor.fetchone()[0]

            c = sqlite_conn.cursor()

            c.execute("SELECT * FROM ping WHERE test_id = ?", (t['test_id'],))
            p = c.fetchone()
            if p:
                pg_cursor.execute(
                    "INSERT INTO ping (test_id, latency, jitter, low, high) VALUES (%s, %s, %s, %s, %s)",
                    (pg_test_id, p['latency'], p['jitter'], p['low'], p['high'])
                )

            for tbl in ('download', 'upload'):
                c.execute(f"SELECT * FROM {tbl} WHERE test_id = ?", (t['test_id'],))  # noqa: S608
                r = c.fetchone()
                if r:
                    pg_cursor.execute(
                        f"INSERT INTO {tbl} (test_id, bandwidth_mbps, bytes, elapsed, latency_iqm, latency_low, latency_high, latency_jitter) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",  # noqa: S608
                        (pg_test_id, r['bandwidth_mbps'], r['bytes'], r['elapsed'], r['latency_iqm'], r['latency_low'], r['latency_high'], r['latency_jitter'])
                    )

            c.execute("SELECT * FROM server WHERE test_id = ?", (t['test_id'],))
            s = c.fetchone()
            if s:
                pg_cursor.execute(
                    "INSERT INTO server (test_id, remote_server_id, host, port, name, location, country, ip) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (pg_test_id, s['remote_server_id'], s['host'], s['port'], s['name'], s['location'], s['country'], s['ip'])
                )

            pg_conn.commit()
            mark_synced(sqlite_conn, 'test', 'test_id', t['test_id'])

        cursor.execute("SELECT * FROM outage WHERE synced_at IS NULL AND end_time IS NOT NULL")
        outages = cursor.fetchall()

        skipped_outages = 0
        for o in outages:
            pg_cursor.execute(
                "SELECT outage_id FROM outage WHERE host_id = %s AND start_time = %s AND end_time = %s",
                (pg_host_id, o['start_time'], o['end_time'])
            )
            if pg_cursor.fetchone():
                mark_synced(sqlite_conn, 'outage', 'outage_id', o['outage_id'])
                skipped_outages += 1
                continue

            try:
                pg_cursor.execute(
                    "INSERT INTO outage (host_id, start_time, end_time, status) VALUES (%s, %s, %s, %s)",
                    (pg_host_id, o['start_time'], o['end_time'], o['status'])
                )
            except pg_errors.UniqueViolation:
                pg_conn.rollback()
                mark_synced(sqlite_conn, 'outage', 'outage_id', o['outage_id'])
                skipped_outages += 1
                continue
            pg_conn.commit()
            mark_synced(sqlite_conn, 'outage', 'outage_id', o['outage_id'])

        cursor.execute("SELECT * FROM network_status WHERE host_id = ?", (local_host_id,))
        ns = cursor.fetchone()
        if ns:
            pg_cursor.execute("""
                INSERT INTO network_status (host_id, last_connectivity_check, is_connected, last_db_write)
                VALUES (%s, %s, %s, NOW() AT TIME ZONE 'UTC')
                ON CONFLICT (host_id) DO UPDATE SET
                    last_connectivity_check = EXCLUDED.last_connectivity_check,
                    is_connected = EXCLUDED.is_connected,
                    last_db_write = NOW() AT TIME ZONE 'UTC'
            """, (pg_host_id, ns['last_connectivity_check'], bool(ns['is_connected'])))
            pg_conn.commit()

        sync_settings(sqlite_conn, cursor, pg_conn, pg_cursor)

        pg_cursor.execute(
            "UPDATE host SET last_db_sync = NOW() AT TIME ZONE 'UTC' WHERE host_id = %s",
            (pg_host_id,)
        )
        pg_conn.commit()

        new_tests = len(tests) - skipped_tests
        new_outages = len(outages) - skipped_outages
        log.info(f"Synced {new_tests} test(s), {new_outages} outage(s) — skipped {skipped_tests} duplicate test(s), {skipped_outages} duplicate outage(s)")

    finally:
        sqlite_conn.close()
        pg_conn.close()


def main():
    local_db.init_db()

    if not has_db_config():
        log.info("No DB configuration, skipping sync")
        return

    lock = acquire_sync_lock()
    if lock is None:
        log.info("Another sync is already running — skipping")
        return

    try:
        sync()
    except Exception as e:
        log.error(f"Sync failed: {e}")
    finally:
        lock.close()


if __name__ == '__main__':
    main()
