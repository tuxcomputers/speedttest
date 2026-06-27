#!/usr/bin/env bash

set -euo pipefail

git pull

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

FORCE_SERVER=false
if [[ "${1:-}" == "-server" ]]; then
    FORCE_SERVER=true
fi

# ── .env bootstrap ────────────────────────────────────────────────────────────

if [[ ! -f ".env" ]]; then
    echo "Creating .env..."
    NIC=$(ip route show default | awk '/default/ {print $5}')
    HOST_HASH=$(cat /sys/class/net/${NIC}/address | sha256sum | cut -c1-16)
    TIMEZONE=$(timedatectl show --property=Timezone --value 2>/dev/null || echo "UTC")
    cat > .env <<EOF
HOST_HOSTNAME=$(hostname)
HOST_HASH=${HOST_HASH}
TIMEZONE=${TIMEZONE}
EOF
    echo ".env created."
fi

# ── DB configuration ──────────────────────────────────────────────────────────

has_db_settings() {
    grep -q "^DB_HOST=." .env 2>/dev/null
}

if [[ "${FORCE_SERVER}" == true ]] || ! has_db_settings; then
    echo ""
    read -p "Connect to a PostgreSQL server? [y/n]: " connect_pg
    if [[ "${connect_pg}" =~ ^[Yy]$ ]]; then
        echo ""
        read -p "Local Docker container or remote server? [local/remote]: " pg_type
        echo ""

        sed -i '/^DB_/d' .env
        sed -i '/^COMPOSE_PROFILES/d' .env

        if [[ "${pg_type}" == "local" ]]; then
            cat >> .env <<EOF
DB_HOST=db
DB_PORT=5432
DB_NAME=speedtest
DB_USER=speedtest
DB_PASSWORD=speedtest
COMPOSE_PROFILES=local_db
EOF
            echo "Local PostgreSQL container configured."
        else
            read -p "Host:        " db_host
            read -p "Port [5432]: " db_port
            db_port="${db_port:-5432}"
            read -p "Database:    " db_name
            read -p "Username:    " db_user
            read -s -p "Password:    " db_password
            echo ""
            cat >> .env <<EOF
DB_HOST=${db_host}
DB_PORT=${db_port}
DB_NAME=${db_name}
DB_USER=${db_user}
DB_PASSWORD=${db_password}
COMPOSE_PROFILES=
EOF
            echo "Remote PostgreSQL configured."
        fi
    else
        sed -i '/^COMPOSE_PROFILES/d' .env
        echo "COMPOSE_PROFILES=" >> .env
        echo "Skipping PostgreSQL — running local SQLite only."
    fi
fi

# ── Image rebuild check ───────────────────────────────────────────────────────

hash_file="/tmp/speedttest-image.hash"
current_hash=$(cat Dockerfile requirements.txt *.py | md5sum | cut -d' ' -f1)
stored_hash=""
[[ -f "${hash_file}" ]] && stored_hash=$(cat "${hash_file}")

if [[ "${current_hash}" != "${stored_hash}" ]] || ! docker image inspect speedttest-speedtest &>/dev/null; then
    echo "Image out of date or missing — rebuilding..."
    docker compose up --build -d
    echo "${current_hash}" > "${hash_file}"
else
    echo "Image up to date — starting..."
    docker compose up -d
fi
