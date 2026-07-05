"""Integration tests for data_sync against a real PostgreSQL.

Skipped unless TEST_DB_HOST is set (CI provides a postgres:16 service).
Locally:

    docker run --rm -d -p 5432:5432 -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test -e POSTGRES_DB=test postgres:16
    TEST_DB_HOST=localhost TEST_DB_USER=test TEST_DB_PASSWORD=test TEST_DB_NAME=test pytest tests/test_data_sync.py
"""
import glob
import os

import pytest

import local_db

pytestmark = pytest.mark.skipif(
    not os.environ.get('TEST_DB_HOST'),
    reason="TEST_DB_HOST not set — PostgreSQL integration tests skipped"
)

REPO_ROOT = os.path.join(os.path.dirname(__file__), '..')


@pytest.fixture
def pg_env(sqlite_env, monkeypatch):
    monkeypatch.setenv('DB_HOST', os.environ['TEST_DB_HOST'])
    monkeypatch.setenv('DB_PORT', os.environ.get('TEST_DB_PORT', '5432'))
    monkeypatch.setenv('DB_NAME', os.environ.get('TEST_DB_NAME', 'test'))
    monkeypatch.setenv('DB_USER', os.environ.get('TEST_DB_USER', 'test'))
    monkeypatch.setenv('DB_PASSWORD', os.environ.get('TEST_DB_PASSWORD', 'test'))

    import data_sync
    conn = data_sync.get_pg_connection()
    conn.autocommit = True
    cursor = conn.cursor()
    # Clean slate, then apply the schema exactly as docker-entrypoint-initdb.d
    # would, followed by the migration (verifying it is fresh-DB safe).
    cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    for path in sorted(glob.glob(os.path.join(REPO_ROOT, 'db', 'schema', '*.sql'))):
        with open(path) as f:
            cursor.execute(f.read())
    for path in sorted(glob.glob(os.path.join(REPO_ROOT, 'db', 'migrations', '*.sql'))):
        with open(path) as f:
            cursor.execute(f.read())
    yield conn
    conn.close()


def seed_local_test(timestamp='2026-07-02T00:05:00Z'):
    local_db.init_db()
    host_id = local_db.get_or_create_host()
    conn = local_db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO test (host_id, timestamp, isp, packet_loss) VALUES (?, ?, 'TestISP', 0.5)",
        (host_id, timestamp)
    )
    test_id = cursor.lastrowid
    cursor.execute("INSERT INTO ping (test_id, latency, jitter, low, high) VALUES (?, 10.1, 1.2, 9.0, 12.0)", (test_id,))
    cursor.execute(
        "INSERT INTO download (test_id, bandwidth_mbps, bytes, elapsed) VALUES (?, 100.5, 1000000, 15000)",
        (test_id,)
    )
    cursor.execute(
        "INSERT INTO upload (test_id, bandwidth_mbps, bytes, elapsed) VALUES (?, 20.25, 500000, 15000)",
        (test_id,)
    )
    cursor.execute(
        "INSERT INTO server (test_id, remote_server_id, host, port, name) VALUES (?, 1234, 'test.example', 8080, 'Test Server')",
        (test_id,)
    )
    cursor.execute(
        "INSERT INTO outage (host_id, start_time, end_time, status) VALUES (?, '2026-07-01T10:00:00+00:00', '2026-07-01T10:05:00+00:00', 'outage')",
        (host_id,)
    )
    conn.commit()
    conn.close()
    return host_id, test_id


def pg_count(pg_conn, table):
    cursor = pg_conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 — test helper, hardcoded names
    return cursor.fetchone()[0]


def test_sync_pushes_everything(pg_env):
    import data_sync
    seed_local_test()
    data_sync.sync()

    for table in ('host', 'test', 'ping', 'download', 'upload', 'server', 'outage'):
        assert pg_count(pg_env, table) == 1, f"expected 1 row in {table}"

    cursor = pg_env.cursor()
    cursor.execute("SELECT hostname, host_hash, last_db_sync FROM host")
    hostname, host_hash, last_db_sync = cursor.fetchone()
    assert hostname == 'testhost'
    assert host_hash == 'abc123def456abcd'
    assert last_db_sync is not None


def test_sync_is_idempotent(pg_env):
    import data_sync
    seed_local_test()
    data_sync.sync()
    data_sync.sync()
    assert pg_count(pg_env, 'test') == 1
    assert pg_count(pg_env, 'outage') == 1


def test_resync_after_cleared_markers_does_not_duplicate(pg_env):
    # Simulates the "first sync to this server" path re-sending old rows.
    import data_sync
    seed_local_test()
    data_sync.sync()

    conn = local_db.get_connection()
    conn.execute("UPDATE test SET synced_at = NULL")
    conn.execute("UPDATE outage SET synced_at = NULL")
    conn.commit()
    conn.close()

    data_sync.sync()
    assert pg_count(pg_env, 'test') == 1
    assert pg_count(pg_env, 'ping') == 1
    assert pg_count(pg_env, 'outage') == 1


def test_unique_index_blocks_duplicate_tests(pg_env):
    import psycopg2
    cursor = pg_env.cursor()
    cursor.execute("INSERT INTO host (hostname, host_hash) VALUES ('h', 'hash1') RETURNING host_id")
    host_id = cursor.fetchone()[0]
    cursor.execute("INSERT INTO test (host_id, timestamp) VALUES (%s, '2026-07-02 00:05:00')", (host_id,))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cursor.execute("INSERT INTO test (host_id, timestamp) VALUES (%s, '2026-07-02 00:05:00')", (host_id,))


def test_duplicate_hostnames_allowed_for_different_hosts(pg_env):
    # Two Pis with the same default hostname must both be able to register.
    cursor = pg_env.cursor()
    cursor.execute("INSERT INTO host (hostname, host_hash) VALUES ('raspberrypi', 'hash1')")
    cursor.execute("INSERT INTO host (hostname, host_hash) VALUES ('raspberrypi', 'hash2')")
    assert pg_count(pg_env, 'host') == 2


def test_settings_pull_from_remote(pg_env):
    import data_sync
    seed_local_test()

    cursor = pg_env.cursor()
    cursor.execute("UPDATE setting SET value = '10' WHERE setting = 'speedtest_interval_min'")
    cursor.execute("UPDATE setting SET value = '4.2.2.4' WHERE setting = 'ping_host_1'")

    data_sync.sync()

    conn = local_db.get_connection()
    local = dict(conn.execute("SELECT setting, value FROM setting").fetchall())
    conn.close()
    assert local['speedtest_interval_min'] == '10'
    assert local['ping_host_1'] == '4.2.2.4'


def test_prune_remote_rules_only_after_first_sync(pg_env):
    # Before the first sync to this server the host has no last_db_sync there,
    # so prune must not trust synced_at markers (they may be from an old
    # server). After a successful sync it may.
    import data_sync
    import prune

    seed_local_test()
    assert prune.remote_sync_established() is False
    data_sync.sync()
    assert prune.remote_sync_established() is True


def test_network_status_last_db_write_is_utc(pg_env):
    import data_sync
    from datetime import datetime, timezone

    seed_local_test()
    conn = local_db.get_connection()
    conn.execute(
        "INSERT INTO network_status (host_id, last_connectivity_check, is_connected) VALUES (1, '2026-07-02T00:00:00+00:00', 1)"
    )
    conn.commit()
    conn.close()

    # Force the server to a non-UTC timezone (applies to the fresh connection
    # sync() opens) so a bare NOW() would produce a visibly wrong value.
    dbname = os.environ.get('TEST_DB_NAME', 'test')
    cursor = pg_env.cursor()
    cursor.execute(f'ALTER DATABASE "{dbname}" SET timezone = \'Australia/Brisbane\'')
    try:
        data_sync.sync()
    finally:
        cursor.execute(f'ALTER DATABASE "{dbname}" RESET timezone')

    cursor.execute("SELECT last_db_write, last_db_sync FROM network_status JOIN host USING (host_id)")
    last_db_write, last_db_sync = cursor.fetchone()
    # Stored values must be UTC regardless of the server's timezone setting
    # (Brisbane is UTC+10, so a bare NOW() would be ~36000s off).
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for name, value in (('last_db_write', last_db_write), ('last_db_sync', last_db_sync)):
        delta = abs((now - value).total_seconds())
        assert delta < 120, f"{name} {value} is not UTC (off by {delta:.0f}s)"
