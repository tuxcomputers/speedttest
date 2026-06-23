import socket
import time
from datetime import datetime, timezone
import local_db

NORMAL_INTERVAL = 10
FAST_INTERVAL = 1
GAP_THRESHOLD = 20


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


def now_str():
    return now_utc().isoformat()


def get_last_connectivity_check(host_id):
    try:
        conn = local_db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT last_connectivity_check FROM network_status WHERE host_id = ?", (host_id,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return datetime.fromisoformat(row[0])
        return None
    except Exception:
        return None


def update_status(host_id, is_connected):
    conn = local_db.get_connection()
    conn.execute("""
        INSERT INTO network_status (host_id, last_connectivity_check, is_connected)
        VALUES (?, ?, ?)
        ON CONFLICT (host_id) DO UPDATE SET
            last_connectivity_check = excluded.last_connectivity_check,
            is_connected = excluded.is_connected
    """, (host_id, now_str(), 1 if is_connected else 0))
    conn.commit()
    conn.close()


def open_outage(host_id, start_time, status='outage'):
    conn = local_db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO outage (host_id, start_time, status) VALUES (?, ?, ?)",
        (host_id, start_time.isoformat(), status)
    )
    outage_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return outage_id


def close_outage(outage_id):
    conn = local_db.get_connection()
    conn.execute("UPDATE outage SET end_time = ? WHERE outage_id = ?", (now_str(), outage_id))
    conn.commit()
    conn.close()


def record_unknown_gap(host_id, start_time, end_time):
    conn = local_db.get_connection()
    conn.execute(
        "INSERT INTO outage (host_id, start_time, end_time, status) VALUES (?, ?, ?, 'unknown')",
        (host_id, start_time.isoformat(), end_time.isoformat())
    )
    conn.commit()
    conn.close()


local_db.init_db()
host_id = local_db.get_or_create_host()

# Check for monitoring gap since last run
last_check = get_last_connectivity_check(host_id)
startup_time = now_utc()
if last_check is not None:
    gap_seconds = (startup_time - last_check).total_seconds()
    if gap_seconds > GAP_THRESHOLD:
        record_unknown_gap(host_id, last_check, startup_time)
        print(f"Recorded unknown gap of {gap_seconds:.0f}s ({last_check} to {startup_time})")

outage_id = None

while True:
    connected = check_internet()

    try:
        update_status(host_id, connected)
    except Exception as e:
        print(f"Failed to update network status: {e}")

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
