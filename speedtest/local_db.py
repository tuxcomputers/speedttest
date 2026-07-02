import os
import socket
import sqlite3

from tzlocal import get_localzone

from log_setup import get_logger

log = get_logger('local_db')

# Bump when the local schema changes; init_db() applies everything below the
# stored version idempotently, then fast-paths on subsequent runs.
SCHEMA_VERSION = 1

BASE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS host (
        host_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        hostname  TEXT NOT NULL,
        timezone  TEXT NOT NULL DEFAULT 'UTC',
        host_hash TEXT,
        remote_id TEXT
    );

    CREATE TABLE IF NOT EXISTS test (
        test_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        host_id      INTEGER NOT NULL,
        timestamp    TEXT,
        isp          TEXT,
        packet_loss  REAL,
        result_id    TEXT,
        result_url   TEXT,
        synced_at    TEXT,
        FOREIGN KEY (host_id) REFERENCES host(host_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ping (
        ping_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        test_id   INTEGER NOT NULL,
        latency   REAL,
        jitter    REAL,
        low       REAL,
        high      REAL,
        FOREIGN KEY (test_id) REFERENCES test(test_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS download (
        download_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        test_id        INTEGER NOT NULL,
        bandwidth_mbps REAL,
        bytes          INTEGER,
        elapsed        INTEGER,
        latency_iqm    REAL,
        latency_low    REAL,
        latency_high   REAL,
        latency_jitter REAL,
        FOREIGN KEY (test_id) REFERENCES test(test_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS upload (
        upload_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        test_id        INTEGER NOT NULL,
        bandwidth_mbps REAL,
        bytes          INTEGER,
        elapsed        INTEGER,
        latency_iqm    REAL,
        latency_low    REAL,
        latency_high   REAL,
        latency_jitter REAL,
        FOREIGN KEY (test_id) REFERENCES test(test_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS server (
        server_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        test_id          INTEGER NOT NULL,
        remote_server_id INTEGER,
        host             TEXT,
        port             INTEGER,
        name             TEXT,
        location         TEXT,
        country          TEXT,
        ip               TEXT,
        FOREIGN KEY (test_id) REFERENCES test(test_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS outage (
        outage_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        host_id    INTEGER NOT NULL,
        start_time TEXT,
        end_time   TEXT,
        status     TEXT,
        synced_at  TEXT,
        FOREIGN KEY (host_id) REFERENCES host(host_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS network_status (
        host_id                  INTEGER PRIMARY KEY,
        last_connectivity_check  TEXT,
        is_connected             INTEGER,
        FOREIGN KEY (host_id) REFERENCES host(host_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS setting (
        setting  TEXT PRIMARY KEY,
        value    TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_test_unsynced ON test(synced_at) WHERE synced_at IS NULL;
    CREATE INDEX IF NOT EXISTS idx_outage_unsynced ON outage(synced_at) WHERE synced_at IS NULL;
"""


def get_sqlite_path():
    return os.environ.get('SQLITE_PATH', '/data/speedtest.db')


def get_connection():
    conn = sqlite3.connect(get_sqlite_path(), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        conn.close()
        return

    conn.executescript(BASE_SCHEMA)

    # DB credentials used to be mirrored into the setting table; they are only
    # ever read from the environment, so scrub any previously stored copies.
    conn.execute(
        "DELETE FROM setting WHERE setting IN ('db_host', 'db_port', 'db_name', 'db_user', 'db_password', 'last_db_sync')"
    )

    conn.executemany(
        "INSERT OR IGNORE INTO setting (setting, value) VALUES (?, ?)",
        [
            ('ping_host_1', '8.8.8.8'),
            ('ping_host_2', '1.1.1.1'),
            ('ping_host_3', '9.9.9.9'),
            ('ping_host_4', '208.67.222.222'),
            ('speedtest_interval_min', '5'),
        ]
    )
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    conn.close()


def get_host_hash():
    # Identity must come from the host's real MAC, captured by start.sh into
    # .env. Reading a MAC inside the container would hash the Docker-assigned
    # interface, which changes across network recreations.
    return os.environ.get('HOST_HASH') or None


def get_or_create_host():
    hostname = os.environ.get('HOST_HOSTNAME') or socket.gethostname()
    try:
        timezone = str(get_localzone())
    except Exception:
        timezone = 'UTC'
    host_hash = get_host_hash()
    if not host_hash:
        log.warning("HOST_HASH is not set — remote sync will fail until it is (re-run start.sh)")

    # The local DB describes exactly one host: reuse the single row, updating
    # it in place if the hostname/timezone/hash changed. Inserting a second
    # row would orphan existing data and confuse sync attribution.
    conn = get_connection()
    row = conn.execute("SELECT host_id, hostname, timezone, host_hash FROM host LIMIT 1").fetchone()
    if row is None:
        cursor = conn.execute(
            "INSERT INTO host (hostname, timezone, host_hash) VALUES (?, ?, ?)",
            (hostname, timezone, host_hash)
        )
        host_id = cursor.lastrowid
        conn.commit()
    else:
        host_id = row['host_id']
        if (row['hostname'], row['timezone'], row['host_hash']) != (hostname, timezone, host_hash):
            conn.execute(
                "UPDATE host SET hostname = ?, timezone = ?, host_hash = ? WHERE host_id = ?",
                (hostname, timezone, host_hash, host_id)
            )
            conn.commit()
    conn.close()
    return host_id
