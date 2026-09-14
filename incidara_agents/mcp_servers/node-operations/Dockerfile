# node-operations MCP server — separate container.
#
# Runs as SSE server on configurable port. Role determines which tools are available.
# Bind to 127.0.0.1 by default — only accessible from the same node.
#
# Build context: repo root (incidara/incidara/)

FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client \
        ipmitool \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        psycopg2-binary \
        paramiko \
        pyyaml \
        requests \
        pexpect \
        fastmcp>=3.0.0

WORKDIR /opt/node-operations

# Copy ltp-storage-common and postgresql-sdk from platform registry image
COPY --from=192.0.2.10:5000/docker.io/lucialtp/postgresql-sdk:latest /app/sdk/ltp-storage-common /opt/ltp-platform/ltp-storage-common
COPY --from=192.0.2.10:5000/docker.io/lucialtp/postgresql-sdk:latest /app/sdk /opt/ltp-platform/postgresql-sdk
RUN pip install --no-cache-dir \
      /opt/ltp-platform/ltp-storage-common \
      /opt/ltp-platform/postgresql-sdk && \
    pip install --no-cache-dir pytest

# Copy node-operations code from build context
COPY mcp_server.py /opt/node-operations/
COPY node_operations /opt/node-operations/node_operations
COPY bundles /opt/node-operations/bundles

ENV PYTHONPATH=/opt/node-operations

COPY entrypoint.sh /entrypoint.sh
COPY tests/ tests/
RUN chmod +x /entrypoint.sh

EXPOSE 8081

ENTRYPOINT ["/entrypoint.sh"]
