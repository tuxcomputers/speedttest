import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import local_db
from log_setup import get_logger

log = get_logger('connectivity')

# Gaps in monitoring shorter than this (scheduling delays, container
# restarts) are not worth recording as 'unknown' outages.
GAP_THRESHOLD = 60

CHECK_TIMEOUT = 3        # per-host timeout for the normal 10s cadence
OUTAGE_CHECK_TIMEOUT = 1.5  # tighter timeout inside the outage loop
STATUS_WRITE_INTERVAL = 10  # min seconds between heartbeat writes during an outage


def get_ping_hosts():
    conn = local_db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM setting WHERE setting LIKE 'ping_host_%' ORDER BY setting")
    hosts = [row[0] for row in cursor.fetchall() if row[0]]
    conn.close()
    return hosts or ['8.8.8.8']


def dns_query_ok(host, timeout):
    """Send a real DNS query (A record for example.com) and require a matching
    response. A bare TCP connect to port 53 succeeds against captive portals
    and DNS-intercepting middleboxes; an answered query is much stronger
    evidence of actual internet reachability."""
    txid = os.urandom(2)
    query = (
        txid
        + b'\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'  # RD flag, 1 question
        + b'\x07example\x03com\x00'                    # QNAME example.com
        + b'\x00\x01\x00\x01'                          # QTYPE A, QCLASS IN
    )
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(query, (host, 53))
        resp, _ = sock.recvfrom(512)
        return resp[:2] == txid and bool(resp[2] & 0x80)  # our txid, QR=response
    except Exception:
        return False
    finally:
        if sock is not None:
            sock.close()


def tcp_connect_ok(host, timeout):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, 53))
        sock.close()
        return True
    except Exception:
        return False


def check_internet(single_host=False, timeout=CHECK_TIMEOUT):
    hosts = get_ping_hosts()
    if single_host:
        hosts = hosts[:1]
    # Check all hosts concurrently so a full sweep is bounded by the timeout,
    # not timeout * len(hosts).
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        if any(pool.map(lambda h: dns_query_ok(h, timeout), hosts)):
            return True
        # Fall back to a TCP connect for networks that filter outbound UDP/53.
        if any(pool.map(lambda h: tcp_connect_ok(h, timeout), hosts)):
            log.info("DNS queries failed but TCP connect to port 53 succeeded — treating as connected")
            return True
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


def get_open_outage(host_id):
    conn = local_db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT outage_id FROM outage WHERE host_id = ? AND end_time IS NULL ORDER BY start_time DESC LIMIT 1",
        (host_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return row['outage_id'] if row else None


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


def main():
    local_db.init_db()
    host_id = local_db.get_or_create_host()

    last_check = get_last_connectivity_check(host_id)
    startup_time = now_utc()
    if last_check is not None:
        gap_seconds = (startup_time - last_check).total_seconds()
        if gap_seconds > GAP_THRESHOLD:
            record_unknown_gap(host_id, last_check, startup_time)
            log.info(f"Recorded unknown gap of {gap_seconds:.0f}s ({last_check} to {startup_time})")

    outage_id = get_open_outage(host_id)
    connected = check_internet(single_host=outage_id is not None)

    try:
        update_status(host_id, connected)
    except Exception as e:
        log.error(f"Failed to update network status: {e}")

    if connected:
        if outage_id is not None:
            try:
                close_outage(outage_id)
                log.info(f"Internet restored, closed outage #{outage_id}")
            except Exception as e:
                log.error(f"Failed to close outage: {e}")
        return

    if outage_id is None:
        try:
            outage_id = open_outage(host_id, now_utc())
            log.warning(f"Internet down, opened outage #{outage_id}")
        except Exception as e:
            log.error(f"Failed to open outage: {e}")
    else:
        log.warning("No internet connection (outage ongoing)")

    # Network is down — poll rapidly until restored, then exit. Status
    # heartbeats are throttled so a long outage doesn't hammer the SQLite
    # file (which usually lives on an SD card) every tick.
    last_status_write = time.monotonic()
    while not connected:
        time.sleep(1)
        connected = check_internet(single_host=True, timeout=OUTAGE_CHECK_TIMEOUT)
        if connected or time.monotonic() - last_status_write >= STATUS_WRITE_INTERVAL:
            try:
                update_status(host_id, connected)
                last_status_write = time.monotonic()
            except Exception as e:
                log.error(f"Failed to update network status: {e}")
        if connected:
            if outage_id is not None:
                try:
                    close_outage(outage_id)
                    log.info(f"Internet restored, closed outage #{outage_id}")
                except Exception as e:
                    log.error(f"Failed to close outage: {e}")


if __name__ == '__main__':
    main()
