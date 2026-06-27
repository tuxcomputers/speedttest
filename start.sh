#!/usr/bin/env bash

set -euo pipefail

git pull

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Create .env if it doesn't exist
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
    echo ".env created with hostname=$(hostname), hash=${HOST_HASH}, timezone=${TIMEZONE}"
fi

# Check if image needs rebuilding by hashing all build-relevant files
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
