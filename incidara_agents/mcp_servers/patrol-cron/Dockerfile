# patrol-cron MCP server + cron sync daemon + collector jobs.
#
# Runs three processes:
#   1. cron daemon (OS)           — executes collector jobs on schedule
#   2. cron_sync.py --watch (bg)  — syncs DB → crontab
#   3. mcp_server.py (fg)         — serves rule CRUD tools over SSE
#
# Build context: mcp_servers/patrol-cron/
#   docker build -t patrol-cron .

# Pin to Debian 12 (bookworm) — Debian 13 ships OpenSSH 10.0 which drops
# legacy algorithms needed by older switches (S6000C uses SHA1 kex only).
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        cron \
        curl \
        openssh-client \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        psycopg2-binary \
        paramiko \
        pexpect \
        pyyaml \
        requests \
        pandas \
        joblib \
        pytest \
        fastmcp>=3.0.0

WORKDIR /opt/patrol-cron

# Copy MCP server entry point + patrol_cron package
# Build context: mcp_servers/patrol-cron/
COPY mcp_server.py /opt/patrol-cron/
COPY patrol_cron /opt/patrol-cron/patrol_cron
COPY tests /opt/patrol-cron/tests

# Add to Python path
ENV PYTHONPATH=/opt/patrol-cron

# Directories for flock files and logs
RUN mkdir -p /tmp/patrol_cron/logs

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
