from datetime import datetime, timedelta, timezone

import connectivity_monitor as cm
import local_db


def test_outage_open_and_close(sqlite_env):
    local_db.init_db()
    host_id = local_db.get_or_create_host()

    assert cm.get_open_outage(host_id) is None
    outage_id = cm.open_outage(host_id, cm.now_utc())
    assert cm.get_open_outage(host_id) == outage_id

    cm.close_outage(outage_id)
    assert cm.get_open_outage(host_id) is None

    conn = local_db.get_connection()
    row = conn.execute("SELECT * FROM outage WHERE outage_id = ?", (outage_id,)).fetchone()
    conn.close()
    assert row['status'] == 'outage'
    assert row['end_time'] is not None


def test_unknown_gap_recorded(sqlite_env):
    local_db.init_db()
    host_id = local_db.get_or_create_host()

    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    end = datetime.now(timezone.utc)
    cm.record_unknown_gap(host_id, start, end)

    conn = local_db.get_connection()
    row = conn.execute("SELECT * FROM outage WHERE host_id = ?", (host_id,)).fetchone()
    conn.close()
    assert row['status'] == 'unknown'
    assert row['start_time'] == start.isoformat()
    assert row['end_time'] == end.isoformat()


def test_update_status_upserts(sqlite_env):
    local_db.init_db()
    host_id = local_db.get_or_create_host()

    cm.update_status(host_id, True)
    cm.update_status(host_id, False)

    conn = local_db.get_connection()
    rows = conn.execute("SELECT * FROM network_status").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]['is_connected'] == 0
    assert cm.get_last_connectivity_check(host_id) is not None


def test_get_ping_hosts_reads_settings(sqlite_env):
    local_db.init_db()
    hosts = cm.get_ping_hosts()
    assert hosts == ['8.8.8.8', '1.1.1.1', '9.9.9.9', '208.67.222.222']


def test_dns_query_fails_fast_when_unreachable(sqlite_env):
    # 192.0.2.1 is TEST-NET-1: reserved, never routable, never answers.
    # The check must time out and return False, not raise.
    assert cm.dns_query_ok('192.0.2.1', timeout=0.2) is False


def test_tcp_connect_fails_when_unreachable(sqlite_env):
    assert cm.tcp_connect_ok('192.0.2.1', timeout=0.2) is False
