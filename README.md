# speedttest

A self-hosted internet speed and connectivity monitoring system designed to run on one or more hosts (Raspberry Pi, desktop, server, etc.) and aggregate results into a central PostgreSQL database.

## How it works

Each host runs a single Docker container. Inside that container a supervisor process manages three tasks on a schedule:

| Task | Interval | Script |
|---|---|---|
| Speed test | Every 5 minutes, clock-aligned | `speed_test.py` |
| Connectivity check | Every 10 seconds | `connectivity_monitor.py` |
| Database sync | Every 5 minutes at :30s | `data_sync.py` |

All scripts are bind-mounted from the repo directory. The supervisor spawns each script as a fresh process at the right time, so a `git pull` on the host takes effect on the next scheduled run — no container rebuild required.

### Speed test

Runs the [Ookla speedtest CLI](https://www.speedtest.net/apps/cli) and records download speed, upload speed, ping latency/jitter, packet loss, server details, and a link to the full result. Bandwidth is stored in Mbps (rounded to 2 decimal places).

### Connectivity monitor

Checks internet connectivity by opening a TCP connection to port 53 on a configurable set of hosts (default: 8.8.8.8, 1.1.1.1, 9.9.9.9, 208.67.222.222). Under normal conditions it runs once every 10 seconds and exits.

If the connection is lost the monitor enters a 1-second loop, recording the outage start time and updating status on each tick. When connectivity is restored it closes the outage record and exits. While the monitor is in its outage loop the supervisor holds off on running speed tests — there is no point testing a down connection.

Gaps in monitoring (e.g. system reboots) are recorded as `unknown` status outage entries so the history is complete.

### Database sync

Syncs locally accumulated data to a central PostgreSQL database. Data is always written to a local SQLite database first, then synced to PostgreSQL. This means the system keeps recording even when the remote database is unreachable.

The sync pushes:
- Speed test results (test, ping, download, upload, server)
- Closed outage records
- Current network status

**Ping hosts are pulled from the remote database**, not pushed. The remote database is the source of truth for which hosts to ping — change them once in PostgreSQL and all connected hosts pick up the new values on their next sync. On first connection, local defaults are pushed up if the remote has none.

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
│   └── setting (ping hosts, DB config, last sync)
└── Docker container
    └── supervisor.py
        ├── → speed_test.py        (every 5 min)
        ├── → connectivity_monitor.py  (every 10s / 1s during outage)
        └── → data_sync.py         (every 5 min at :30s)

Central PostgreSQL
├── host
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

On subsequent runs it pulls the latest code, checks whether the image needs rebuilding (only when `Dockerfile` or `requirements.txt` change), and restarts the container.

Pass `-server` to re-run the database configuration questions:
```bash
./start.sh -server
```

### PostgreSQL options

When asked about PostgreSQL:

| Choice | Effect |
|---|---|
| `l` — local container | Starts a PostgreSQL 16 container on the same host, accessible on port 5432 |
| `r` — remote server | Connects to an existing PostgreSQL server; prompts for host, port, database, user, and password |
| anything else | Exits — re-run `./start.sh` when ready |

If no PostgreSQL is configured the system runs in SQLite-only mode and skips the sync step.

## Database schema

### `host`
| Column | Type | Notes |
|---|---|---|
| `host_id` | SERIAL PK | |
| `hostname` | TEXT UNIQUE | |
| `timezone` | TEXT | IANA timezone string |
| `host_hash` | TEXT | SHA-256 of MAC address (16 chars) |
| `remote_id` | TEXT | PG `host_id` cached locally |

### `test`
Parent record for each speed test run. Children: `ping`, `download`, `upload`, `server`.

### `network_status`
One row per host — current connectivity state and timestamps for the last check and last DB write.

### `outage`
| Column | Notes |
|---|---|
| `start_time` | When connectivity was lost |
| `end_time` | When it was restored (NULL if ongoing) |
| `status` | `outage` or `unknown` (gap in monitoring) |

### `setting`
Key/value store. `ping_host_1` through `ping_host_4` control which hosts are used for connectivity checks. Managed centrally in PostgreSQL.
