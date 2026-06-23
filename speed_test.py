import subprocess
import json
import math
import os
import time
import psycopg2
from psycopg2 import OperationalError

INTERVAL_MINUTES = 5


def get_connection():
    return psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', 5432),
        dbname=os.environ['DB_NAME'],
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD']
    )


def sleep_until_next_run():
    interval_seconds = INTERVAL_MINUTES * 60
    now = time.time()
    next_run = math.ceil(now / interval_seconds) * interval_seconds
    time.sleep(next_run - now)


def wait_for_db():
    while True:
        try:
            conn = get_connection()
            conn.close()
            return
        except OperationalError:
            print("Waiting for database...")
            time.sleep(5)


def run_speedtest():
    result = subprocess.run(['speedtest', '--format', 'json'], capture_output=True, text=True)
    return json.loads(result.stdout)


def save_results(data):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        'INSERT INTO tests (timestamp, isp, packet_loss, result_id, result_url) VALUES (%s, %s, %s, %s, %s) RETURNING test_id',
        (data['timestamp'], data['isp'], data['packetLoss'],
         data['result']['id'], data['result']['url'])
    )
    test_id = cursor.fetchone()[0]

    p = data['ping']
    cursor.execute(
        'INSERT INTO ping (test_id, latency, jitter, low, high) VALUES (%s, %s, %s, %s, %s)',
        (test_id, p['latency'], p['jitter'], p['low'], p['high'])
    )

    for table, section in [('download', data['download']), ('upload', data['upload'])]:
        lat = section['latency']
        cursor.execute(
            f'INSERT INTO {table} (test_id, bandwidth_mbps, bytes, elapsed, latency_iqm, latency_low, latency_high, latency_jitter) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
            (test_id, section['bandwidth'] * 8 / 1e6, section['bytes'], section['elapsed'],
             lat['iqm'], lat['low'], lat['high'], lat['jitter'])
        )

    s = data['server']
    cursor.execute(
        'INSERT INTO server (test_id, remote_server_id, host, port, name, location, country, ip) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
        (test_id, s['id'], s['host'], s['port'], s['name'], s['location'], s['country'], s['ip'])
    )

    conn.commit()
    conn.close()
    return test_id


wait_for_db()

while True:
    sleep_until_next_run()
    data = run_speedtest()
    test_id = save_results(data)
    print(f"Saved test #{test_id}: {data['download']['bandwidth'] * 8 / 1e6:.2f} Mbps down, {data['upload']['bandwidth'] * 8 / 1e6:.2f} Mbps up")
