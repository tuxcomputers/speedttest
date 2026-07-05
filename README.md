# speedttest

A self-hosted internet speed and connectivity monitoring system designed to run on one or more hosts (Raspberry Pi, desktop, server, etc.) and aggregate results into a central PostgreSQL database.

## How it works

Each host runs a single Docker container. Inside that container a supervisor process schedules two tasks:

| Task | Interval | Script |
|---|---|---|
| Speed test | Every 5 minutes (configurable), clock-aligned | `speed_test.py` |
| Connectivity check | Every 10 seconds (~1–3 s during outage) | `connectivity_monitor.py` |

All scripts are bind-mounted from the repo directory. The supervisor spawns each script as a fresh process at the right time, so a `git pull` + `docker compose restart app` on the host takes effect immediately — no image rebuild required. (The image also contains a copy of the code so it can run standalone.) The container runs with `init: true` so short-lived worker processes are always reaped.

### Speed test

Runs the [Ookla speedtest CLI](https://www.speedtest.net/apps/cli) and records download speed, upload speed, ping latency/jitter, packet loss, server details, and a link to the full result. Bandwidth is stored in Mbps (rounded to 2 decimal places). The CLI is given a 120-second timeout — if it hangs beyond that the run is abandoned and the next scheduled slot runs cleanly. Some fields (`packet_loss`, `result_url`) are optional and stored as NULL when the CLI omits them.

The interval is controlled by the `speedtest_interval_min` setting (default 5 minutes). Like the ping hosts, it is managed centrally in PostgreSQL and pushed to every host on sync — change it once and all hosts pick it up. **Note on data usage:** at the default 5-minute interval a fast connection can transfer tens of GB per day; raise the interval on metered links.

The speed test runs on every clock-aligned mark, but only if no outage is currently open — the supervisor checks the local outage table before spawning the script. If an outage is active the slot is skipped but the database sync is still triggered, so closed outage records and network status continue to reach the remote database even when internet testing is paused. The database sync is also triggered on every completed speed test.

### Connectivity monitor

Checks internet connectivity by sending a real DNS query (A record for `example.com`) to a configurable set of DNS servers (default: 8.8.8.8, 1.1.1.1, 9.9.9.9, 208.67.222.222). A bare TCP connect can be spoofed by captive portals and DNS-intercepting middleboxes; an answered query is much stronger evidence of real internet access. If DNS queries fail everywhere, a TCP connect to port 53 is tried as a fallback for networks that filter outbound UDP. All hosts are checked in parallel, so a full sweep is bounded by the per-host timeout (3 s), not the number of hosts.

Connectivity monitoring starts immediately when the container starts; the speed test waits for the first clock-aligned mark.

Under normal conditions the monitor is spawned every 10 seconds, runs a single check, and exits. If the connection is lost it writes the outage start time immediately and enters a rapid polling loop (roughly every 1–3 seconds), then writes `end_time`, closes the outage, and exits when connectivity returns. During an outage, status heartbeat writes are throttled to one per 10 seconds to avoid hammering the SQLite file (usually on an SD card).

Gaps in monitoring longer than 60 seconds (e.g. system reboots) are recorded as `unknown` status outage entries so the history remains complete. Shorter scheduling hiccups are ignored.

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
- Unique indexes on `test(host_id, timestamp)` and `outage(host_id, start_time, end_time)` enforce this at the database level, so even two syncs racing each other cannot insert duplicates

Only one sync process runs at a time — a lock file prevents a slow sync from overlapping with the next scheduled one. Connections to PostgreSQL use a 10-second connect timeout, TCP keepalives, and a 60-second statement timeout so a flaky network can't hang a sync forever. Set `DB_SSLMODE=require` in `.env` to force TLS to the server (default `prefer`).

**Remote-managed settings** (`ping_host_1`–`ping_host_4`, `speedtest_interval_min`) are pulled from the remote database on every sync — the remote is the source of truth; change them once in PostgreSQL and all connected hosts pick up the new values. On first connection, local defaults are pushed up if the remote has none yet.

**`last_db_sync`** is recorded on the PostgreSQL `host` record at the end of every successful sync. This is the authoritative sync timestamp — always read from the remote. If it is NULL (new server or first run) all local records are queued for sync regardless of their local sync state, and the duplicate detection ensures nothing is inserted twice.

To spot hosts that have silently stopped syncing:

```sql
SELECT hostname, last_db_sync
FROM host
WHERE last_db_sync IS NULL
   OR last_db_sync < NOW() AT TIME ZONE 'UTC' - INTERVAL '30 minutes'
ORDER BY last_db_sync NULLS FIRST;
```

### Multi-host support

Each host is identified by a SHA-256 hash of its MAC address (first 16 characters), stored as `host_hash`. This identity survives OS reinstalls. The hash is captured by `start.sh` on the host and passed in via `.env` — it is never derived inside the container (the container's own MAC is Docker-assigned and unstable). When a host first connects to PostgreSQL it registers itself (or finds its existing record) by `host_hash`, keeping its local `remote_id` in sync. All data is written to PostgreSQL under the correct `host_id` automatically.

`host_hash` is unique in PostgreSQL; `hostname` is just a label and may collide (e.g. two default-named Raspberry Pis are fine).

All timestamps are stored in UTC. Each host records its IANA timezone (e.g. `Australia/Brisbane`) so viewer applications can display times in local time.

## Architecture

```
Host (Pi / tower / etc.)
├── SQLite (local, always-on)
│   ├── test + ping/download/upload/server
│   ├── outage
│   ├── network_status
│   └── setting (ping hosts, speed test interval)
└── Docker container (init: true)
    └── supervisor.py
        ├── → speed_test.py             (every 5 min — skipped if outage open)
        │       └── → data_sync.py      (triggered on completion, lock-protected)
        └── → connectivity_monitor.py   (every 10s / rapid loop during outage)

Central PostgreSQL
├── host  (includes last_db_sync)
├── test + ping/download/upload/server
├── outage
├── network_status
├── setting (ping hosts + interval — source of truth)
└── schema_migration (applied migrations)
```

## Repository layout

```
speedttest/
├── docker-compose.yml   # orchestrates the app + db services
├── start.sh             # bootstrap / deploy script
├── speedtest/           # the host agent — supervisor + scripts
├── db/
│   ├── schema/          # PostgreSQL schema (mounted as docker-entrypoint-initdb.d)
│   ├── migrations/      # schema migrations for existing databases
│   └── migrate.sh       # applies pending migrations
├── data/                # the agent's SQLite data (gitignored, created by start.sh)
├── database/            # local PostgreSQL data (gitignored, created by start.sh)
├── tests/               # pytest suite (SQLite unit tests + PG integration tests)
└── gui/                 # front-end (not yet built)
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

On subsequent runs it pulls the latest code (continuing with the current checkout if the pull fails), checks whether the image needs rebuilding (only when `speedtest/Dockerfile` or `speedtest/requirements.txt` change), and recreates the container.

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
| `l` | Starts a PostgreSQL 16 container on the same host, accessible on port 5432 so other hosts can sync to it. A random password is generated and stored in `.env`. Data is stored in `./database` |
| `r` | Connects to an existing PostgreSQL server; prompts for host, port, database, user, password, and whether to require SSL/TLS |
| `e` or anything else | Exits the script |

Answering `n` to the first question runs in SQLite-only mode with no sync.

**Security notes:** the local container's port 5432 is published on all interfaces so that other monitored hosts can reach it — keep it behind your firewall/NAT. When syncing across untrusted networks, enable SSL on the server and set `DB_SSLMODE=require`. DB credentials live only in `.env` (never committed, never copied into SQLite).

**Data location:** all persistent data lives in gitignored bind-mount directories, so it survives image rebuilds, container recreation, and even `docker compose down -v`. The agent's SQLite database is in `./data` (every host); the local PostgreSQL container's data is in `./database` (server hosts only). To back either up, stop the relevant container and copy the directory. Deployments that started on the old `sqlite_data`/`pgdata` named volumes are migrated automatically by `start.sh` the first time it runs after this change.

### Schema migrations

Fresh databases get the current schema automatically from `db/schema/*.sql`. When a schema change lands in `db/migrations/`, apply it to an existing database with:

```bash
./db/migrate.sh
```

It reads the connection settings from `.env`, records applied migrations in the `schema_migration` table, and is safe to run repeatedly (already-applied migrations are skipped; migrations are idempotent and run in a transaction).

## Development

```bash
pip install -r requirements-dev.txt
ruff check .
pytest                       # SQLite unit tests
```

The PostgreSQL integration tests need a throwaway server:

```bash
docker run --rm -d -p 5432:5432 -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test -e POSTGRES_DB=test postgres:16
TEST_DB_HOST=localhost TEST_DB_USER=test TEST_DB_PASSWORD=test TEST_DB_NAME=test pytest
```

CI (GitHub Actions) runs lint plus the full suite against a PostgreSQL 16 service on every push.

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
| `hostname` | TEXT | Label only — not unique |
| `timezone` | TEXT | IANA timezone string (e.g. `Australia/Brisbane`) |
| `host_hash` | TEXT UNIQUE | SHA-256 of MAC address, first 16 characters — the host identity key |
| `remote_id` | TEXT | PG `host_id` cached in local SQLite |
| `last_db_sync` | TIMESTAMP(0) | UTC timestamp of last successful sync — authoritative |

### `test`
Parent record for each speed test run. Children: `ping`, `download`, `upload`, `server` (all `ON DELETE CASCADE`).

All timestamps are `TIMESTAMP(0)` — second precision, UTC. `(host_id, timestamp)` is unique.

### `network_status`
One row per host — current connectivity state and timestamps for the last check and last DB write.

### `outage`
| Column | Notes |
|---|---|
| `start_time` | When connectivity was lost |
| `end_time` | When it was restored (`NULL` if ongoing) |
| `status` | `outage` or `unknown` (gap in monitoring) |

`(host_id, start_time, end_time)` is unique.

### `setting`
Key/value store. `ping_host_1` through `ping_host_4` control which DNS servers are used for connectivity checks; `speedtest_interval_min` controls the speed test interval. Managed centrally in PostgreSQL — the remote value overwrites local on every sync.

### `schema_migration`
Migrations applied by `db/migrate.sh`, by filename.
