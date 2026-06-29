# speedttest

A self-hosted internet speed and connectivity monitoring system designed to run on one or more hosts (Raspberry Pi, desktop, server, etc.) and aggregate results into a central PostgreSQL database.

## How it works

Each host runs a single Docker container. Inside that container a supervisor process schedules two tasks:

| Task | Interval | Script |
|---|---|---|
| Speed test | Every 5 minutes, clock-aligned | `speed_test.py` |
| Connectivity check | Every 10 seconds (1 second during outage) | `connectivity_monitor.py` |

All scripts are bind-mounted from the repo directory. The supervisor spawns each script as a fresh process at the right time, so a `git pull` + `docker compose restart app` on the host takes effect immediately — no image rebuild required.

### Speed test

Runs the [Ookla speedtest CLI](https://www.speedtest.net/apps/cli) and records download speed, upload speed, ping latency/jitter, packet loss, server details, and a link to the full result. Bandwidth is stored in Mbps (rounded to 2 decimal places). The CLI is given a 120-second timeout — if it hangs beyond that the run is abandoned and the next scheduled slot runs cleanly. Some fields (`packet_loss`, `result_url`) are optional and stored as NULL when the CLI omits them.

The speed test runs on every 5-minute clock-aligned mark, but only if no outage is currently open — the supervisor checks the local outage table before spawning the script. If an outage is active the slot is skipped but the database sync is still triggered, so closed outage records and network status continue to reach the remote database even when internet testing is paused. The database sync is also triggered on every completed speed test.

### Connectivity monitor

Checks internet connectivity by opening a TCP connection to port 53 on a configurable set of hosts (default: 8.8.8.8, 1.1.1.1, 9.9.9.9, 208.67.222.222). Connectivity monitoring starts immediately when the container starts; the speed test waits for the first clock-aligned 5-minute mark.

Under normal conditions the monitor is spawned every 10 seconds, runs a single check, and exits. If the connection is lost it writes the outage start time immediately and enters a 1-second loop, updating status on each tick. When connectivity is restored it writes `end_time`, closes the outage, and exits.

Gaps in monitoring (e.g. system reboots) are recorded as `unknown` status outage entries so the history remains complete.

### Database sync

Triggered by the speed test on completion. Syncs locally accumulated data to a central PostgreSQL database. Data is always written to a local SQLite database first, then synced to PostgreSQL. This means the system keeps recording even when the remote database is unreachable.

The sync pushes:
- Speed test results (`test`, `ping`, `download`, `upload`, `server`)
- Closed outage records
- Current network status

**Duplicate detection** prevents double-insertion if the same data is synced more than once:
- Tests are matched by `host_id` + `timestamp` — if a matching test is found in PostgreSQL the test and all its children are skipped
- Outages are matched by `host_id` + `start_time` + `end_time`
- Network status is always upserted

**Ping hosts are pulled from the remote database**, not pushed. The remote database is the source of truth for which hosts to ping — change them once in PostgreSQL and all connected hosts pick up the new values on their next sync. On first connection, local defaults are pushed up if the remote has none yet.

**`last_db_sync`** is recorded on the PostgreSQL `host` record at the end of every successful sync. This is the authoritative sync timestamp — always read from the remote. If it is NULL (new server or first run) all local records are queued for sync regardless of their local sync state, and the duplicate detection ensures nothing is inserted twice.

### Multi-host support

Each host is identified by a SHA-256 hash of its MAC address (first 16 characters), stored as `host_hash`. This identity survives OS reinstalls. When a host first connects to PostgreSQL it registers itself (or finds its existing record) by `host_hash`, keeping its local `remote_id` in sync. All data is written to PostgreSQL under the correct `host_id` automatically.

All timestamps are stored in UTC. Each host records its IANA timezone (e.g. `Australia/Brisbane`) so viewer applications can display times in local time.

## Architecture

```
Host (Pi / tower / etc.)
├── SQLite (local, always-on)
│   ├── test + ping/download/upload/server
│   ├── outage
│   ├── network_status
│   └── setting (ping hosts, DB config)
└── Docker container
    └── supervisor.py
        ├── → speed_test.py             (every 5 min — skipped if outage open)
        │       └── → data_sync.py      (triggered on completion)
        └── → connectivity_monitor.py   (every 10s / 1s loop during outage)

Central PostgreSQL
├── host  (includes last_db_sync)
├── test + ping/download/upload/server
├── outage
├── network_status
└── setting (ping hosts — source of truth)
```

## Setup

### Requirements

- Docker (with Compose)
- Git

On a Raspberry Pi, install Docker with:
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
newgrp docker
```

### Starting

```bash
git clone <repo>
cd speedttest
./start.sh
```

`start.sh` handles everything on first run:
- Creates a `.env` file with the hostname, MAC-based host hash, and timezone
- Asks whether to connect to a PostgreSQL database (local Docker container or remote server)
- Builds the Docker image and starts the container

On subsequent runs it pulls the latest code, checks whether the image needs rebuilding (only when `Dockerfile` or `requirements.txt` change), and recreates the container.

Pass `-server` to re-run the database configuration questions:
```bash
./start.sh -server
```

### PostgreSQL options

When asked `Connect to a PostgreSQL server? [y/n]`, answering `y` presents:

```
PostgreSQL:
  local container [l]
  remote server   [r]
  exit            [e]
Choice:
```

| Choice | Effect |
|---|---|
| `l` | Starts a PostgreSQL 16 container on the same host, accessible on port 5432 |
| `r` | Connects to an existing PostgreSQL server; prompts for host, port, database, user, and password |
| `e` or anything else | Exits the script |

Answering `n` to the first question runs in SQLite-only mode with no sync.

## Querying data

All timestamps in PostgreSQL are stored in UTC. To display them in each host's local time, join to the `host` table and use a double `AT TIME ZONE`:

```sql
SELECT
    t.test_id,
    t.timestamp AT TIME ZONE 'UTC' AT TIME ZONE h.timezone AS local_time,
    h.hostname,
    h.timezone
FROM test t
JOIN host h ON h.host_id = t.host_id
ORDER BY t.timestamp DESC;
```

The first `AT TIME ZONE 'UTC'` tells PostgreSQL the stored value is UTC; the second converts it to the IANA timezone recorded for that host.

## Database schema

### `host`
| Column | Type | Notes |
|---|---|---|
| `host_id` | SERIAL PK | |
| `hostname` | TEXT UNIQUE | |
| `timezone` | TEXT | IANA timezone string (e.g. `Australia/Brisbane`) |
| `host_hash` | TEXT | SHA-256 of MAC address, first 16 characters |
| `remote_id` | TEXT | PG `host_id` cached in local SQLite |
| `last_db_sync` | TIMESTAMP(0) | UTC timestamp of last successful sync — authoritative |

### `test`
Parent record for each speed test run. Children: `ping`, `download`, `upload`, `server`.

All timestamps are `TIMESTAMP(0)` — second precision, UTC.

### `network_status`
One row per host — current connectivity state and timestamps for the last check and last DB write.

### `outage`
| Column | Notes |
|---|---|
| `start_time` | When connectivity was lost |
| `end_time` | When it was restored (`NULL` if ongoing) |
| `status` | `outage` or `unknown` (gap in monitoring) |

### `setting`
Key/value store. `ping_host_1` through `ping_host_4` control which hosts are used for connectivity checks. Managed centrally in PostgreSQL — the remote value overwrites local on every sync.
