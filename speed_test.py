import subprocess
import json
import sqlite3
from datetime import datetime

# Run the Speedtest CLI command and get the result in JSON format
def run_speedtest():
    result = subprocess.run(['speedtest', '--format', 'json'], capture_output=True, text=True)
    return json.loads(result.stdout)

# Store the results in the SQLite database
def save_results(data):
    conn = sqlite3.connect('speedtest_results.db')
    cursor = conn.cursor()
    
    # Create table if not exists
    cursor.execute('''CREATE TABLE IF NOT EXISTS results
                      (timestamp TEXT, download REAL, upload REAL, ping REAL)''')
    
    # Insert results
    cursor.execute('INSERT INTO results (timestamp, download, upload, ping) VALUES (?, ?, ?, ?)',
                   (datetime.now().isoformat(), data['download']['bandwidth'] / 1e6,  # Convert to Mbps
                    data['upload']['bandwidth'] / 1e6, data['ping']['latency']))
    
    conn.commit()
    conn.close()

speedtest_data = run_speedtest()
save_results(speedtest_data)
