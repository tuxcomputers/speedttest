import os
import psycopg2
import local_db


def has_db_config():
    return bool(os.environ.get('DB_HOST'))


def get_pg_connection():
    return psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', 5432),
        dbname=os.environ['DB_NAME'],
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD']
    )


def resolve_pg_host(pg_cursor, sqlite_conn, local_host_id, hostname, timezone, host_hash):
    if not host_hash:
        raise ValueError("host_hash is required to resolve remote host")

    pg_cursor.execute("SELECT host_id FROM host WHERE host_hash = %s", (host_hash,))
    row = pg_cursor.fetchone()

    if row:
        pg_host_id = row[0]
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

    cursor = sqlite_conn.cursor()
    cursor.execute("SELECT remote_id FROM host WHERE host_id = ?", (local_host_id,))
    local_remote_id = cursor.fetchone()['remote_id']

    if local_remote_id is None or str(local_remote_id) != str(pg_host_id):
        sqlite_conn.execute(
            "UPDATE host SET remote_id = ? WHERE host_id = ?",
            (str(pg_host_id), local_host_id)
        )
        sqlite_conn.commit()

    return pg_host_id


def sync():
    sqlite_conn = local_db.get_connection()

    try:
        pg_conn = get_pg_connection()
    except Exception as e:
        print(f"Cannot connect to PostgreSQL: {e}")
        sqlite_conn.close()
        return

    try:
        cursor = sqlite_conn.cursor()

        cursor.execute("SELECT host_id, hostname, timezone, host_hash FROM host LIMIT 1")
        row = cursor.fetchone()
        if not row:
            print("No host record in local DB, nothing to sync")
            return

        local_host_id = row['host_id']
        hostname = row['hostname']
        timezone = row['timezone']
        host_hash = row['host_hash']

        pg_cursor = pg_conn.cursor()
        pg_host_id = resolve_pg_host(pg_cursor, sqlite_conn, local_host_id, hostname, timezone, host_hash)
        pg_conn.commit()

        cursor.execute("SELECT * FROM test WHERE synced_at IS NULL")
        tests = cursor.fetchall()

        for t in tests:
            pg_cursor.execute(
                "INSERT INTO test (host_id, timestamp, isp, packet_loss, result_id, result_url) VALUES (%s, %s, %s, %s, %s, %s) RETURNING test_id",
                (pg_host_id, t['timestamp'], t['isp'], t['packet_loss'], t['result_id'], t['result_url'])
            )
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
                c.execute(f"SELECT * FROM {tbl} WHERE test_id = ?", (t['test_id'],))
                r = c.fetchone()
                if r:
                    pg_cursor.execute(
                        f"INSERT INTO {tbl} (test_id, bandwidth_mbps, bytes, elapsed, latency_iqm, latency_low, latency_high, latency_jitter) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
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
            sqlite_conn.execute("UPDATE test SET synced_at = datetime('now') WHERE test_id = ?", (t['test_id'],))
            sqlite_conn.commit()

        cursor.execute("SELECT * FROM outage WHERE synced_at IS NULL AND end_time IS NOT NULL")
        outages = cursor.fetchall()

        for o in outages:
            pg_cursor.execute(
                "INSERT INTO outage (host_id, start_time, end_time, status) VALUES (%s, %s, %s, %s)",
                (pg_host_id, o['start_time'], o['end_time'], o['status'])
            )
            pg_conn.commit()
            sqlite_conn.execute("UPDATE outage SET synced_at = datetime('now') WHERE outage_id = ?", (o['outage_id'],))
            sqlite_conn.commit()

        cursor.execute("SELECT * FROM network_status WHERE host_id = ?", (local_host_id,))
        ns = cursor.fetchone()
        if ns:
            pg_cursor.execute("""
                INSERT INTO network_status (host_id, last_connectivity_check, is_connected, last_db_write)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (host_id) DO UPDATE SET
                    last_connectivity_check = EXCLUDED.last_connectivity_check,
                    is_connected = EXCLUDED.is_connected,
                    last_db_write = NOW()
            """, (pg_host_id, ns['last_connectivity_check'], bool(ns['is_connected'])))
            pg_conn.commit()

        cursor.execute("SELECT setting, value FROM setting WHERE setting LIKE 'ping_host_%'")
        for row in cursor.fetchall():
            pg_cursor.execute(
                "INSERT INTO setting (setting, value) VALUES (%s, %s) ON CONFLICT (setting) DO UPDATE SET value = EXCLUDED.value",
                (row['setting'], row['value'])
            )
        pg_conn.commit()

        sqlite_conn.execute(
            "UPDATE setting SET value = datetime('now') WHERE setting = 'last_db_sync'"
        )
        sqlite_conn.commit()

        print(f"Synced {len(tests)} test(s) and {len(outages)} outage(s)")

    finally:
        sqlite_conn.close()
        pg_conn.close()


local_db.init_db()

if not has_db_config():
    print("No DB configuration, skipping sync")
else:
    try:
        sync()
    except Exception as e:
        print(f"Sync failed: {e}")
