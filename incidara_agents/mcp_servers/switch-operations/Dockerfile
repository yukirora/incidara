# switch-operations MCP server — standalone container.
#
# Build context: repo root (incidara/)
# COPY paths are relative to repo root.

FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/switch-operations

COPY pyproject.toml .
COPY switch_operations/ switch_operations/
COPY mcp_server.py .
COPY tests/ tests/

RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir pytest

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8090

ENTRYPOINT ["/entrypoint.sh"]
