from datetime import datetime, timedelta, timezone

import local_db
import prune


def ago_z(days):
    """Ookla-style timestamp: 2026-07-05T09:00:03Z"""
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')


def ago_offset(days):
    """Monitor-style timestamp: 2026-07-05T09:00:03.123456+00:00"""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def seed_test(conn, host_id, days_old, synced):
    cur = conn.execute(
        "INSERT INTO test (host_id, timestamp, synced_at) VALUES (?, ?, ?)",
        (host_id, ago_z(days_old), ago_z(0) if synced else None)
    )
    test_id = cur.lastrowid
    conn.execute("INSERT INTO ping (test_id, latency) VALUES (?, 1.0)", (test_id,))
    return test_id


def seed_outage(conn, host_id, days_old, synced, open_outage=False):
    cur = conn.execute(
        "INSERT INTO outage (host_id, start_time, end_time, status, synced_at) VALUES (?, ?, ?, 'outage', ?)",
        (host_id, ago_offset(days_old), None if open_outage else ago_offset(days_old), ago_offset(0) if synced else None)
    )
    return cur.lastrowid


def remaining_ids(conn, table, id_col):
    return {r[0] for r in conn.execute(f"SELECT {id_col} FROM {table}").fetchall()}  # noqa: S608


def test_remote_mode_prunes_synced_after_remote_days(sqlite_env):
    local_db.init_db()
    host_id = local_db.get_or_create_host()
    conn = local_db.get_connection()

    synced_old = seed_test(conn, host_id, 31, synced=True)       # pruned: synced, past 30d
    synced_fresh = seed_test(conn, host_id, 29, synced=True)     # kept: within 30d
    unsynced_old = seed_test(conn, host_id, 200, synced=False)   # kept: unsynced, under ceiling
    unsynced_ancient = seed_test(conn, host_id, 366, synced=False)  # pruned: past ceiling
    conn.commit()

    prune.prune(has_remote=True)

    remaining = remaining_ids(conn, 'test', 'test_id')
    assert remaining == {synced_fresh, unsynced_old}
    assert synced_old not in remaining and unsynced_ancient not in remaining
    # children removed with their parents
    assert remaining_ids(conn, 'ping', 'test_id') == {synced_fresh, unsynced_old}
    conn.close()


def test_offline_host_never_prunes_before_ceiling(sqlite_env):
    # The user's scenario: remote configured, but nothing has synced for 31+
    # days — nothing may be pruned until the 365-day ceiling.
    local_db.init_db()
    host_id = local_db.get_or_create_host()
    conn = local_db.get_connection()

    for days in (31, 100, 364):
        seed_test(conn, host_id, days, synced=False)
    conn.commit()

    prune.prune(has_remote=True)

    assert len(remaining_ids(conn, 'test', 'test_id')) == 3
    conn.close()


def test_local_only_mode_uses_ceiling_only(sqlite_env):
    # No DB_HOST (conftest clears it): even synced-looking rows stay until 365.
    local_db.init_db()
    host_id = local_db.get_or_create_host()
    conn = local_db.get_connection()

    kept = seed_test(conn, host_id, 364, synced=True)
    pruned = seed_test(conn, host_id, 366, synced=False)
    conn.commit()

    prune.prune()

    remaining = remaining_ids(conn, 'test', 'test_id')
    assert remaining == {kept}
    assert pruned not in remaining
    conn.close()


def test_outage_pruning_never_touches_open_outages(sqlite_env):
    local_db.init_db()
    host_id = local_db.get_or_create_host()
    conn = local_db.get_connection()

    still_open = seed_outage(conn, host_id, 400, synced=False, open_outage=True)  # kept: open
    closed_synced = seed_outage(conn, host_id, 31, synced=True)                   # pruned
    closed_unsynced = seed_outage(conn, host_id, 31, synced=False)                # kept: unsynced
    conn.commit()

    prune.prune(has_remote=True)

    remaining = remaining_ids(conn, 'outage', 'outage_id')
    assert remaining == {still_open, closed_unsynced}
    assert closed_synced not in remaining
    conn.close()


def test_retention_settings_are_respected(sqlite_env):
    local_db.init_db()
    host_id = local_db.get_or_create_host()
    conn = local_db.get_connection()
    conn.execute("UPDATE setting SET value = '5' WHERE setting = 'prune_remote_days'")

    pruned = seed_test(conn, host_id, 6, synced=True)
    kept = seed_test(conn, host_id, 4, synced=True)
    conn.commit()

    prune.prune(has_remote=True)

    remaining = remaining_ids(conn, 'test', 'test_id')
    assert remaining == {kept}
    assert pruned not in remaining
    conn.close()


def test_defaults_seeded(sqlite_env):
    local_db.init_db()
    conn = local_db.get_connection()
    settings = dict(conn.execute("SELECT setting, value FROM setting").fetchall())
    conn.close()
    assert settings['prune_local_days'] == '365'
    assert settings['prune_remote_days'] == '30'


def test_invalid_setting_falls_back_to_default(sqlite_env):
    local_db.init_db()
    host_id = local_db.get_or_create_host()
    conn = local_db.get_connection()
    conn.execute("UPDATE setting SET value = 'banana' WHERE setting = 'prune_remote_days'")

    kept = seed_test(conn, host_id, 29, synced=True)    # within default 30d
    pruned = seed_test(conn, host_id, 31, synced=True)  # past default 30d
    conn.commit()

    prune.prune(has_remote=True)

    remaining = remaining_ids(conn, 'test', 'test_id')
    assert remaining == {kept}
    assert pruned not in remaining
    conn.close()


def test_stale_markers_from_previous_server_are_not_pruned(sqlite_env, monkeypatch):
    # Server-migration scenario: rows are marked synced_at from the OLD
    # server, and the new server is configured but has no sync relationship
    # yet (or is unreachable). Pruning must fall back to the ceiling so the
    # full history survives to be replayed to the new server.
    monkeypatch.setenv('DB_HOST', 'db.invalid')
    local_db.init_db()
    host_id = local_db.get_or_create_host()
    conn = local_db.get_connection()

    for days in (31, 100, 364):
        seed_test(conn, host_id, days, synced=True)  # synced to the OLD server
    conn.commit()

    assert prune.remote_sync_established() is False
    prune.prune()  # auto-detect: must choose local-only retention

    assert len(remaining_ids(conn, 'test', 'test_id')) == 3
    conn.close()
