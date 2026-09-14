# Triage-unknown agent Docker image.
# Extends the base claude-agent image with MCP server packages
# and ltp-platform SDK dependencies.

FROM claude-agent:latest

# Copy MCP server packages
COPY mcp_servers/node-operations /opt/node-operations
COPY mcp_servers/agent-evidence /opt/agent-evidence

# Copy ltp-platform SDK packages (private — not on PyPI)
COPY ltp-platform/src/ltp-storage-common /opt/ltp-platform/ltp-storage-common
COPY ltp-platform/src/postgresql-sdk/sdk /opt/ltp-platform/postgresql-sdk

# Install MCP servers + ltp SDKs
RUN pip3 install --break-system-packages --no-cache-dir \
      /opt/node-operations \
      /opt/agent-evidence \
      /opt/ltp-platform/ltp-storage-common \
      /opt/ltp-platform/postgresql-sdk

# ipmitool for remote BMC queries (SEL log, chassis status, sensor readings)
RUN apt-get update -qq && apt-get install -y -qq ipmitool && rm -rf /var/lib/apt/lists/*
COPY agents/triage-agent/setup-mcp.sh /usr/local/bin/setup-mcp.sh
RUN chmod +x /usr/local/bin/setup-mcp.sh
