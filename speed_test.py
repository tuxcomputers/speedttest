import subprocess
import json
import local_db


def run_speedtest():
    result = subprocess.run(
        ['speedtest', '--format', 'json', '--accept-license', '--accept-gdpr'],
        capture_output=True, text=True
    )
    return json.loads(result.stdout)


def save_results(data, host_id):
    conn = local_db.get_connection()
    cursor = conn.cursor()

    cursor.execute(
        'INSERT INTO test (host_id, timestamp, isp, packet_loss, result_id, result_url) VALUES (?, ?, ?, ?, ?, ?)',
        (host_id, data['timestamp'], data['isp'], data['packetLoss'],
         data['result']['id'], data['result']['url'])
    )
    test_id = cursor.lastrowid

    p = data['ping']
    cursor.execute(
        'INSERT INTO ping (test_id, latency, jitter, low, high) VALUES (?, ?, ?, ?, ?)',
        (test_id, p['latency'], p['jitter'], p['low'], p['high'])
    )

    for table, section in [('download', data['download']), ('upload', data['upload'])]:
        lat = section['latency']
        cursor.execute(
            f'INSERT INTO {table} (test_id, bandwidth_mbps, bytes, elapsed, latency_iqm, latency_low, latency_high, latency_jitter) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (test_id, round(section['bandwidth'] * 8 / 1e6, 2), section['bytes'], section['elapsed'],
             lat['iqm'], lat['low'], lat['high'], lat['jitter'])
        )

    s = data['server']
    cursor.execute(
        'INSERT INTO server (test_id, remote_server_id, host, port, name, location, country, ip) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (test_id, s['id'], s['host'], s['port'], s['name'], s['location'], s['country'], s['ip'])
    )

    conn.commit()
    conn.close()
    return test_id


local_db.init_db()
host_id = local_db.get_or_create_host()

try:
    data = run_speedtest()
    test_id = save_results(data, host_id)
    print(f"Saved test #{test_id}: {data['download']['bandwidth'] * 8 / 1e6:.2f} Mbps down, {data['upload']['bandwidth'] * 8 / 1e6:.2f} Mbps up")
except Exception as e:
    print(f"Test failed: {e}")

subprocess.Popen(['python', 'data_sync.py'])
