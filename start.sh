#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

git pull

# ── Docker check ──────────────────────────────────────────────────────────────

if ! command -v docker &>/dev/null; then
    echo "Docker is not installed."
    if grep -qi "raspberry pi" /proc/cpuinfo 2>/dev/null || grep -qi "raspbian\|raspberry" /etc/os-release 2>/dev/null; then
        echo ""
        echo "Raspberry Pi detected. To install Docker, run:"
        echo ""
        echo "  curl -fsSL https://get.docker.com -o get-docker.sh"
        echo "  sudo sh get-docker.sh"
        echo "  sudo usermod -aG docker \$USER"
        echo "  newgrp docker"
        echo ""
    else
        echo "Please install Docker: https://docs.docker.com/engine/install/"
    fi
    exit 1
fi

if ! docker info &>/dev/null; then
    echo "Cannot connect to the Docker daemon — permission denied."
    echo ""
    echo "Your user is not in the docker group. Run:"
    echo ""
    echo "  sudo usermod -aG docker \$USER"
    echo "  newgrp docker"
    echo ""
    echo "Then run this script again."
    exit 1
fi

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
        echo "PostgreSQL:"
        echo "  local container [l]"
        echo "  remote server   [r]"
        echo "  exit            [e]"
        read -p "Choice: " pg_choice
        echo ""

        if [[ "${pg_choice}" == "l" ]]; then
            sed -i '/^DB_/d' .env
            sed -i '/^COMPOSE_PROFILES/d' .env
            cat >> .env <<EOF
DB_HOST=db
DB_PORT=5432
DB_NAME=speedtest
DB_USER=speedtest
DB_PASSWORD=speedtest
COMPOSE_PROFILES=local_db
EOF
            echo "Local PostgreSQL container configured."

        elif [[ "${pg_choice}" == "r" ]]; then
            sed -i '/^DB_/d' .env
            sed -i '/^COMPOSE_PROFILES/d' .env
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

        else
            echo "Exiting."
            exit 0
        fi
    else
        sed -i '/^COMPOSE_PROFILES/d' .env
        echo "COMPOSE_PROFILES=" >> .env
        echo "Skipping PostgreSQL — running local SQLite only."
    fi
fi

# ── Image rebuild check ───────────────────────────────────────────────────────

hash_file="/tmp/speedttest-image.hash"
current_hash=$(cat Dockerfile requirements.txt | md5sum | cut -d' ' -f1)
stored_hash=""
[[ -f "${hash_file}" ]] && stored_hash=$(cat "${hash_file}")

if [[ "${current_hash}" != "${stored_hash}" ]] || ! docker image inspect speedttest-app &>/dev/null; then
    echo "Image out of date or missing — rebuilding..."
    docker compose up --build -d --remove-orphans
    echo "${current_hash}" > "${hash_file}"
else
    echo "Restarting containers to pick up latest code..."
    docker compose up -d --remove-orphans --force-recreate
fi
