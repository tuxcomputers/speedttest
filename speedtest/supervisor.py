import math
import subprocess
import sys
import time

import local_db
from log_setup import get_logger

log = get_logger('supervisor')

DEFAULT_SPEEDTEST_INTERVAL = 5 * 60
CONNECTIVITY_INTERVAL = 10
PRUNE_INTERVAL = 24 * 60 * 60


def get_speedtest_interval():
    """Interval comes from the setting table (remote-managed via sync), so it
    can be tuned centrally without touching code. Minimum 1 minute."""
    try:
        conn = local_db.get_connection()
        row = conn.execute("SELECT value FROM setting WHERE setting = 'speedtest_interval_min'").fetchone()
        conn.close()
        if row and row[0]:
            return max(60, int(row[0]) * 60)
    except Exception as e:
        log.error(f"Failed to read speedtest interval: {e}")
    return DEFAULT_SPEEDTEST_INTERVAL


def next_aligned(interval):
    now = time.time()
    return math.ceil(now / interval) * interval


def spawn(script):
    return subprocess.Popen([sys.executable, script])


def is_running(p):
    return p is not None and p.poll() is None


def outage_active():
    try:
        conn = local_db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM outage WHERE end_time IS NULL")
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except Exception as e:
        log.error(f"Outage check failed (assuming no outage): {e}")
        return False


def main():
    local_db.init_db()

    processes = {'speedtest': None, 'connectivity': None, 'sync': None, 'prune': None}

    next_speedtest = next_aligned(get_speedtest_interval())
    next_connectivity = time.time()
    next_prune = time.time() + 60  # first prune shortly after startup, then daily

    while True:
        # Reap any finished children so they don't linger as zombies.
        for p in processes.values():
            if p is not None:
                p.poll()

        now = time.time()

        if now >= next_connectivity and not is_running(processes['connectivity']):
            processes['connectivity'] = spawn('connectivity_monitor.py')
            next_connectivity = now + CONNECTIVITY_INTERVAL

        # Re-read the interval each pass so a lowered value takes effect
        # without waiting out the old (longer) schedule.
        interval = get_speedtest_interval()
        next_speedtest = min(next_speedtest, next_aligned(interval))

        if now >= next_speedtest and not is_running(processes['speedtest']):
            if outage_active():
                if not is_running(processes['sync']):
                    processes['sync'] = spawn('data_sync.py')
            else:
                processes['speedtest'] = spawn('speed_test.py')
            next_speedtest = next_aligned(interval)

        if now >= next_prune and not is_running(processes['prune']):
            processes['prune'] = spawn('prune.py')
            next_prune = now + PRUNE_INTERVAL

        next_event = min(next_connectivity, next_speedtest, next_prune)
        time.sleep(max(0.1, next_event - time.time()))


if __name__ == '__main__':
    main()
