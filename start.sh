#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Don't let a dirty tree or offline remote block a deploy of existing code
git pull --ff-only || echo "WARNING: git pull failed — continuing with the code already checked out."

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

# Portable in-place sed (macOS requires an explicit empty suffix)
sedi() {
    if [[ "$(uname)" == "Darwin" ]]; then
        sed -i '' "$@"
    else
        sed -i "$@"
    fi
}

# ── .env bootstrap ────────────────────────────────────────────────────────────

if [[ ! -f ".env" ]]; then
    echo "Creating .env..."

    # Get MAC address — Linux uses ip+sysfs, macOS uses route+ifconfig
    if command -v ip &>/dev/null; then
        NIC=$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')
        MAC=$(cat /sys/class/net/${NIC}/address 2>/dev/null)
    else
        NIC=$(route get default 2>/dev/null | awk '/interface:/ {print $2}')
        MAC=$(ifconfig "${NIC}" 2>/dev/null | awk '/ether/ {print $2}')
    fi

    if command -v sha256sum &>/dev/null; then
        HOST_HASH=$(printf '%s\n' "${MAC}" | sha256sum | cut -c1-16)
    else
        HOST_HASH=$(printf '%s\n' "${MAC}" | shasum -a 256 | cut -c1-16)
    fi

    # Get timezone — Linux uses timedatectl, macOS uses the localtime symlink
    TIMEZONE=$(timedatectl show --property=Timezone --value 2>/dev/null \
        || readlink /etc/localtime 2>/dev/null | sed 's|.*/zoneinfo/||' \
        || echo "UTC")

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
            # Keep an existing password — the Postgres volume was initialised
            # with it and only honours POSTGRES_PASSWORD on first init.
            db_password=$(grep '^DB_PASSWORD=' .env 2>/dev/null | head -n1 | cut -d= -f2- || true)
            if [[ -n "${db_password}" ]]; then
                echo "Reusing existing database password from .env."
            else
                db_password=$(openssl rand -hex 16 2>/dev/null \
                    || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
                echo "Generated a random database password (stored in .env)."
            fi
            sedi '/^DB_/d' .env
            sedi '/^COMPOSE_PROFILES/d' .env
            cat >> .env <<EOF
DB_HOST=db
DB_PORT=5432
DB_NAME=speedtest
DB_USER=speedtest
DB_PASSWORD=${db_password}
DB_SSLMODE=prefer
COMPOSE_PROFILES=local_db
EOF
            echo "Local PostgreSQL container configured."

        elif [[ "${pg_choice}" == "r" ]]; then
            sedi '/^DB_/d' .env
            sedi '/^COMPOSE_PROFILES/d' .env
            read -p "Host:        " db_host
            read -p "Port [5432]: " db_port
            db_port="${db_port:-5432}"
            read -p "Database:    " db_name
            read -p "Username:    " db_user
            read -s -p "Password:    " db_password
            echo ""
            read -p "Require SSL/TLS? (server must have it enabled) [y/N]: " db_ssl
            if [[ "${db_ssl}" =~ ^[Yy]$ ]]; then
                db_sslmode=require
            else
                db_sslmode=prefer
            fi
            cat >> .env <<EOF
DB_HOST=${db_host}
DB_PORT=${db_port}
DB_NAME=${db_name}
DB_USER=${db_user}
DB_PASSWORD=${db_password}
DB_SSLMODE=${db_sslmode}
COMPOSE_PROFILES=
EOF
            echo "Remote PostgreSQL configured."

        else
            echo "Exiting."
            exit 0
        fi
    else
        sedi '/^COMPOSE_PROFILES/d' .env
        echo "COMPOSE_PROFILES=" >> .env
        echo "Skipping PostgreSQL — running local SQLite only."
    fi
fi

# ── Image rebuild check ───────────────────────────────────────────────────────

# Repo-local so it survives reboots and doesn't collide across clones/users
hash_file=".image.hash"
if command -v md5sum &>/dev/null; then
    current_hash=$(cat speedtest/Dockerfile speedtest/requirements.txt | md5sum | cut -d' ' -f1)
else
    current_hash=$(cat speedtest/Dockerfile speedtest/requirements.txt | shasum -a 256 | cut -d' ' -f1)
fi
stored_hash=""
[[ -f "${hash_file}" ]] && stored_hash=$(cat "${hash_file}")

if [[ "${current_hash}" != "${stored_hash}" ]] || [[ -z "$(docker compose images -q app 2>/dev/null)" ]]; then
    echo "Image out of date or missing — rebuilding..."
    docker compose up --build -d --remove-orphans
    echo "${current_hash}" > "${hash_file}"
else
    echo "Restarting containers to pick up latest code..."
    docker compose up -d --remove-orphans --force-recreate
fi
