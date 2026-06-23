import os
import socket
import time
from datetime import datetime, timezone
import psycopg2
from psycopg2 import OperationalError

NORMAL_INTERVAL = 10   # seconds between checks when connected
FAST_INTERVAL = 1      # seconds between checks when disconnected
GAP_THRESHOLD = 20     # gap larger than this at startup → recorded as unknown


def get_connection():
    return psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', 5432),
        dbname=os.environ['DB_NAME'],
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD']
    )


def wait_for_db():
    while True:
        try:
            conn = get_connection()
            conn.close()
            return
        except OperationalError:
            print("Waiting for database...")
            time.sleep(5)


def get_or_create_host():
    hostname = socket.gethostname()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO host (hostname) VALUES (%s) ON CONFLICT (hostname) DO UPDATE SET hostname = EXCLUDED.hostname RETURNING host_id",
        (hostname,)
    )
    host_id = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return host_id


def check_internet():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(("8.8.8.8", 53))
        sock.close()
        return True
    except Exception:
        return False


def now_utc():
    return datetime.now(timezone.utc)


def get_last_checked(host_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT last_checked FROM connectivity WHERE host_id = %s", (host_id,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def update_status(host_id, is_connected):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO connectivity (host_id, last_checked, is_connected)
        VALUES (%s, NOW(), %s)
        ON CONFLICT (host_id) DO UPDATE SET
            last_checked = EXCLUDED.last_checked,
            is_connected = EXCLUDED.is_connected
    """, (host_id, is_connected))
    conn.commit()
    conn.close()


def open_outage(host_id, start_time, status='outage'):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO outage (host_id, start_time, status) VALUES (%s, %s, %s) RETURNING outage_id",
        (host_id, start_time, status)
    )
    outage_id = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return outage_id


def close_outage(outage_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE outages SET end_time = NOW() WHERE outage_id = %s",
        (outage_id,)
    )
    conn.commit()
    conn.close()


def record_unknown_gap(host_id, start_time, end_time):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO outage (host_id, start_time, end_time, status) VALUES (%s, %s, %s, 'unknown')",
        (host_id, start_time, end_time)
    )
    conn.commit()
    conn.close()


wait_for_db()
host_id = get_or_create_host()

# Check for monitoring gap since last run
last_checked = get_last_checked(host_id)
startup_time = now_utc()
if last_checked is not None:
    gap_seconds = (startup_time - last_checked).total_seconds()
    if gap_seconds > GAP_THRESHOLD:
        record_unknown_gap(host_id, last_checked, startup_time)
        print(f"Recorded unknown gap of {gap_seconds:.0f}s ({last_checked} to {startup_time})")

outage_id = None

while True:
    connected = check_internet()

    try:
        update_status(host_id, connected)
    except Exception as e:
        print(f"Failed to update connectivity status: {e}")

    if connected:
        if outage_id is not None:
            try:
                close_outage(outage_id)
                print(f"Internet restored, closed outage #{outage_id}")
            except Exception as e:
                print(f"Failed to close outage: {e}")
            outage_id = None
        interval = NORMAL_INTERVAL
    else:
        if outage_id is None:
            try:
                outage_id = open_outage(host_id, now_utc())
                print(f"Internet down, opened outage #{outage_id}")
            except Exception as e:
                print(f"Failed to open outage: {e}")
        print("No internet connection")
        interval = FAST_INTERVAL

    time.sleep(interval)
