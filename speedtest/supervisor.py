import math
import subprocess
import time
import local_db

SPEEDTEST_INTERVAL = 5 * 60
CONNECTIVITY_INTERVAL = 10


def next_aligned(interval):
    now = time.time()
    return math.ceil(now / interval) * interval


def spawn(script):
    return subprocess.Popen(['python', script])


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
    except Exception:
        return False


local_db.init_db()

processes = {'speedtest': None, 'connectivity': None}

next_speedtest = next_aligned(SPEEDTEST_INTERVAL)
next_connectivity = time.time()

while True:
    now = time.time()

    if now >= next_connectivity and not is_running(processes['connectivity']):
        processes['connectivity'] = spawn('connectivity_monitor.py')
        next_connectivity = now + CONNECTIVITY_INTERVAL

    if now >= next_speedtest and not is_running(processes['speedtest']):
        if outage_active():
            subprocess.Popen(['python', 'data_sync.py'])
        else:
            processes['speedtest'] = spawn('speed_test.py')
        next_speedtest = next_aligned(SPEEDTEST_INTERVAL)

    next_event = min(next_connectivity, next_speedtest)
    time.sleep(max(0.1, next_event - time.time()))
