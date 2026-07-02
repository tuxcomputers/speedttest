#!/usr/bin/env bash
#
# Applies pending SQL migrations from db/migrations/ to the configured
# PostgreSQL database. Applied migrations are recorded in the
# schema_migration table so each file runs exactly once.
#
# Uses the DB_* settings from .env. Run this once after pulling schema
# changes — fresh databases (initialised from db/*.sql) don't need it, but
# running it anyway is harmless.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"
MIGRATIONS_DIR="${SCRIPT_DIR}/migrations"

if [[ ! -f "${REPO_DIR}/.env" ]]; then
    echo "No .env found — run start.sh first." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1091
source "${REPO_DIR}/.env"
set +a

if [[ -z "${DB_HOST:-}" ]]; then
    echo "No PostgreSQL configured (DB_HOST is empty) — nothing to migrate." >&2
    exit 0
fi

# Pick a psql: the local db container if that profile is active, otherwise a
# local psql binary, otherwise a throwaway postgres container.
if [[ "${COMPOSE_PROFILES:-}" == *local_db* ]]; then
    run_psql() {
        docker compose --project-directory "${REPO_DIR}" exec -T db \
            psql -v ON_ERROR_STOP=1 -q -U "${DB_USER}" -d "${DB_NAME}" "$@"
    }
elif command -v psql &>/dev/null; then
    run_psql() {
        PGPASSWORD="${DB_PASSWORD}" psql -v ON_ERROR_STOP=1 -q \
            -h "${DB_HOST}" -p "${DB_PORT:-5432}" -U "${DB_USER}" -d "${DB_NAME}" "$@"
    }
else
    run_psql() {
        docker run --rm -i -e PGPASSWORD="${DB_PASSWORD}" postgres:16 \
            psql -v ON_ERROR_STOP=1 -q \
            -h "${DB_HOST}" -p "${DB_PORT:-5432}" -U "${DB_USER}" -d "${DB_NAME}" "$@"
    }
fi

run_psql -c "CREATE TABLE IF NOT EXISTS schema_migration (
    migration  TEXT PRIMARY KEY,
    applied_at TIMESTAMP(0) NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
)"

applied=0
for file in "${MIGRATIONS_DIR}"/*.sql; do
    [[ -e "${file}" ]] || continue
    name="$(basename "${file}")"

    already=$(run_psql -tA -c "SELECT 1 FROM schema_migration WHERE migration = '${name}'")
    if [[ -n "${already}" ]]; then
        echo "Skipping ${name} (already applied)"
        continue
    fi

    echo "Applying ${name}..."
    {
        echo "BEGIN;"
        cat "${file}"
        echo "INSERT INTO schema_migration (migration) VALUES ('${name}');"
        echo "COMMIT;"
    } | run_psql -f -
    applied=$((applied + 1))
done

echo "Done — applied ${applied} migration(s)."
