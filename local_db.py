import os
import socket
import sqlite3
import subprocess
from tzlocal import get_localzone

SQLITE_PATH = os.environ.get('SQLITE_PATH', '/data/speedtest.db')


def get_connection():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS host (
            host_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname  TEXT NOT NULL UNIQUE,
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
            FOREIGN KEY (host_id) REFERENCES host(host_id)
        );

        CREATE TABLE IF NOT EXISTS ping (
            ping_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id   INTEGER NOT NULL,
            latency   REAL,
            jitter    REAL,
            low       REAL,
            high      REAL,
            FOREIGN KEY (test_id) REFERENCES test(test_id)
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
            FOREIGN KEY (test_id) REFERENCES test(test_id)
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
            FOREIGN KEY (test_id) REFERENCES test(test_id)
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
            FOREIGN KEY (test_id) REFERENCES test(test_id)
        );

        CREATE TABLE IF NOT EXISTS outage (
            outage_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            host_id    INTEGER NOT NULL,
            start_time TEXT,
            end_time   TEXT,
            status     TEXT,
            synced_at  TEXT,
            FOREIGN KEY (host_id) REFERENCES host(host_id)
        );

        CREATE TABLE IF NOT EXISTS network_status (
            host_id                  INTEGER PRIMARY KEY,
            last_connectivity_check  TEXT,
            is_connected             INTEGER,
            FOREIGN KEY (host_id) REFERENCES host(host_id)
        );

        CREATE TABLE IF NOT EXISTS setting (
            setting  TEXT PRIMARY KEY,
            value    TEXT
        );
    """)

    # DB settings always reflect current env (updated on every start)
    conn.executemany(
        "INSERT INTO setting (setting, value) VALUES (?, ?) ON CONFLICT (setting) DO UPDATE SET value = excluded.value",
        [
            ('db_host',     os.environ.get('DB_HOST', '')),
            ('db_port',     os.environ.get('DB_PORT', '')),
            ('db_name',     os.environ.get('DB_NAME', '')),
            ('db_user',     os.environ.get('DB_USER', '')),
            ('db_password', os.environ.get('DB_PASSWORD', '')),
        ]
    )
    # Ping hosts and status — only seed if not already set
    conn.executemany(
        "INSERT OR IGNORE INTO setting (setting, value) VALUES (?, ?)",
        [
            ('last_db_sync', ''),
            ('ping_host_1', '8.8.8.8'),
            ('ping_host_2', '1.1.1.1'),
            ('ping_host_3', '9.9.9.9'),
            ('ping_host_4', '208.67.222.222'),
        ]
    )
    conn.commit()
    conn.close()


def get_host_hash():
    if os.environ.get('HOST_HASH'):
        return os.environ['HOST_HASH']
    try:
        result = subprocess.run(
            "cat /sys/class/net/$(ip route show default | awk '/default/ {print $5}')/address | sha256sum | cut -c1-16",
            shell=True, capture_output=True, text=True
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def get_or_create_host():
    hostname = os.environ.get('HOST_HOSTNAME') or socket.gethostname()
    try:
        timezone = str(get_localzone())
    except Exception:
        timezone = 'UTC'
    host_hash = get_host_hash()

    conn = get_connection()
    conn.execute(
        "INSERT INTO host (hostname, timezone, host_hash) VALUES (?, ?, ?) ON CONFLICT (hostname) DO UPDATE SET timezone = excluded.timezone, host_hash = excluded.host_hash",
        (hostname, timezone, host_hash)
    )
    conn.commit()
    cursor = conn.cursor()
    cursor.execute("SELECT host_id FROM host WHERE hostname = ?", (hostname,))
    host_id = cursor.fetchone()[0]
    conn.close()
    return host_id
