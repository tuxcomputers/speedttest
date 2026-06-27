import math
import subprocess
import time
import local_db

SPEEDTEST_INTERVAL = 5 * 60
CONNECTIVITY_INTERVAL = 10
SYNC_INTERVAL = 5 * 60
SYNC_OFFSET = 30


def next_aligned(interval, offset=0):
    now = time.time()
    adjusted = now - offset
    next_base = math.ceil(adjusted / interval) * interval
    next_run = next_base + offset
    if next_run <= now:
        next_run += interval
    return next_run


def spawn(script):
    return subprocess.Popen(['python', script])


def is_running(p):
    return p is not None and p.poll() is None


local_db.init_db()

processes = {'speedtest': None, 'connectivity': None, 'data_sync': None}

next_speedtest = time.time()
next_connectivity = time.time()
next_sync = next_aligned(SYNC_INTERVAL, SYNC_OFFSET)

while True:
    now = time.time()
    connectivity_running = is_running(processes['connectivity'])

    if now >= next_connectivity and not connectivity_running:
        processes['connectivity'] = spawn('connectivity_monitor.py')
        next_connectivity = now + CONNECTIVITY_INTERVAL

    # Hold speedtest and data_sync while connectivity process is running (internet may be down)
    if now >= next_speedtest and not is_running(processes['speedtest']) and not connectivity_running:
        processes['speedtest'] = spawn('speed_test.py')
        next_speedtest = next_aligned(SPEEDTEST_INTERVAL)

    if now >= next_sync and not is_running(processes['data_sync']):
        processes['data_sync'] = spawn('data_sync.py')
        next_sync = next_aligned(SYNC_INTERVAL, SYNC_OFFSET)

    next_event = min(next_connectivity, next_speedtest, next_sync)
    time.sleep(max(0.1, next_event - time.time()))
