import os
import socket
import sqlite3
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
            timezone  TEXT NOT NULL DEFAULT 'UTC'
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
    """)
    conn.close()


def get_or_create_host():
    hostname = socket.gethostname()
    try:
        timezone = str(get_localzone())
    except Exception:
        timezone = 'UTC'

    conn = get_connection()
    conn.execute(
        "INSERT INTO host (hostname, timezone) VALUES (?, ?) ON CONFLICT (hostname) DO UPDATE SET timezone = excluded.timezone",
        (hostname, timezone)
    )
    conn.commit()
    cursor = conn.cursor()
    cursor.execute("SELECT host_id FROM host WHERE hostname = ?", (hostname,))
    host_id = cursor.fetchone()[0]
    conn.close()
    return host_id
