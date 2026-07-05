import os

import data_sync
import local_db
from log_setup import get_logger

log = get_logger('prune')

DEFAULT_LOCAL_DAYS = 365
DEFAULT_REMOTE_DAYS = 30
VACUUM_THRESHOLD = 500  # reclaim file space only after a large prune


def get_retention_days(conn, setting, default):
    row = conn.execute("SELECT value FROM setting WHERE setting = ?", (setting,)).fetchone()
    try:
        days = int(row['value']) if row and row['value'] else default
    except (TypeError, ValueError):
        log.warning(f"Invalid value for {setting} ({row['value']!r}), using default {default}")
        return default
    return max(1, days)


def build_age_filter(column, local_days, remote_days, has_remote):
    """Rows older than the local ceiling are always pruned. When a remote is
    configured, rows that have been synced are pruned at the shorter remote
    retention — unsynced rows are never touched before the ceiling, so a host
    that can't reach the remote keeps everything until it has synced."""
    where = f"(julianday({column}) < julianday('now', ?)"
    params = [f'-{local_days} days']
    if has_remote:
        where += f" OR (synced_at IS NOT NULL AND julianday({column}) < julianday('now', ?))"
        params.append(f'-{remote_days} days')
    where += ")"
    return where, params


def remote_sync_established():
    """The remote retention rule may only be used when this host has an
    established sync relationship with the CURRENT remote (its last_db_sync
    is set there). synced_at markers can be stale — after migrating to a new
    server they still refer to the old one, and trusting them would prune
    history before the first full sync replays it. Any doubt (no config,
    connection problems, host unknown remotely) falls back to local-only
    retention."""
    if not os.environ.get('DB_HOST'):
        return False
    try:
        conn = local_db.get_connection()
        row = conn.execute("SELECT host_hash FROM host LIMIT 1").fetchone()
        conn.close()
        if not row or not row['host_hash']:
            return False
        pg_conn = data_sync.get_pg_connection()
        try:
            cursor = pg_conn.cursor()
            cursor.execute("SELECT last_db_sync FROM host WHERE host_hash = %s", (row['host_hash'],))
            r = cursor.fetchone()
            return bool(r and r[0])
        finally:
            pg_conn.close()
    except Exception as e:
        log.warning(f"Cannot verify sync state on the remote ({e}) — using local-only retention")
        return False


def prune(has_remote=None):
    if has_remote is None:
        has_remote = remote_sync_established()
    conn = local_db.get_connection()
    local_days = get_retention_days(conn, 'prune_local_days', DEFAULT_LOCAL_DAYS)
    remote_days = get_retention_days(conn, 'prune_remote_days', DEFAULT_REMOTE_DAYS)

    test_where, test_params = build_age_filter('timestamp', local_days, remote_days, has_remote)
    # Delete children explicitly — local databases created before the schema
    # gained ON DELETE CASCADE would otherwise fail the FK check.
    for child in ('ping', 'download', 'upload', 'server'):
        conn.execute(
            f"DELETE FROM {child} WHERE test_id IN (SELECT test_id FROM test WHERE {test_where})",  # noqa: S608
            test_params
        )
    tests_deleted = conn.execute(f"DELETE FROM test WHERE {test_where}", test_params).rowcount  # noqa: S608

    # Open outages (end_time IS NULL) are never pruned, however old.
    outage_where, outage_params = build_age_filter('end_time', local_days, remote_days, has_remote)
    outages_deleted = conn.execute(
        f"DELETE FROM outage WHERE end_time IS NOT NULL AND {outage_where}",  # noqa: S608
        outage_params
    ).rowcount

    conn.commit()

    mode = f"synced after {remote_days}d, unsynced ceiling {local_days}d" if has_remote else f"local-only, {local_days}d"
    log.info(f"Pruned {tests_deleted} test(s), {outages_deleted} outage(s) ({mode})")

    if tests_deleted + outages_deleted >= VACUUM_THRESHOLD:
        log.info("Large prune — running VACUUM to reclaim file space")
        conn.execute("VACUUM")
    conn.close()


def main():
    local_db.init_db()
    try:
        prune()
    except Exception as e:
        log.error(f"Prune failed: {e}")


if __name__ == '__main__':
    main()
