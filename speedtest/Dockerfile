FROM python:3.12-slim-bookworm

# Ookla speedtest CLI from packagecloud, with the repo and key configured
# explicitly instead of piping their install script into a root shell.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg sqlite3 && \
    curl -fsSL https://packagecloud.io/ookla/speedtest-cli/gpgkey | gpg --dearmor -o /usr/share/keyrings/ookla.gpg && \
    . /etc/os-release && \
    echo "deb [signed-by=/usr/share/keyrings/ookla.gpg] https://packagecloud.io/ookla/speedtest-cli/debian ${VERSION_CODENAME} main" \
        > /etc/apt/sources.list.d/speedtest.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends speedtest && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The compose file bind-mounts the repo's speedtest/ over /app for live code
# updates; this COPY makes the image self-contained when run without it.
COPY . .

CMD ["python", "supervisor.py"]
