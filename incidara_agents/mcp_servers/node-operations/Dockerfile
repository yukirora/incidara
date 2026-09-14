# node-operations MCP server — separate container.
#
# Runs as SSE server on configurable port. Role determines which tools are available.
# Bind to 127.0.0.1 by default — only accessible from the same node.
#
# Build context: incidara_agents/

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

# Install the platform storage SDK included in this repository.
COPY ltp-platform/src/ltp-storage-common /opt/ltp-platform/ltp-storage-common
COPY ltp-platform/src/postgresql-sdk/sdk /opt/ltp-platform/postgresql-sdk
RUN pip install --no-cache-dir \
      /opt/ltp-platform/ltp-storage-common \
      /opt/ltp-platform/postgresql-sdk && \
    pip install --no-cache-dir pytest

# Copy node-operations code from the incidara_agents build context.
COPY mcp_servers/node-operations/mcp_server.py /opt/node-operations/
COPY mcp_servers/node-operations/node_operations /opt/node-operations/node_operations
COPY mcp_servers/node-operations/bundles /opt/node-operations/bundles

ENV PYTHONPATH=/opt/node-operations

COPY mcp_servers/node-operations/entrypoint.sh /entrypoint.sh
COPY mcp_servers/node-operations/tests/ tests/
RUN chmod +x /entrypoint.sh

EXPOSE 8081

ENTRYPOINT ["/entrypoint.sh"]
