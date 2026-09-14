# Detection agent Docker image.
# Extends the base claude-agent image with switch-operations + feishu-bitable + node-operations.
# Proactive infrastructure monitoring — probes switches and user reports.

FROM claude-agent:latest

# pexpect needed by switch-operations for interactive switch SSH
RUN pip3 install --break-system-packages --no-cache-dir pexpect pyyaml

COPY mcp_servers/switch-operations /opt/switch-operations
COPY mcp_servers/feishu-bitable /opt/feishu-bitable
COPY mcp_servers/node-operations /opt/node-operations
COPY mcp_servers/agent-evidence /opt/agent-evidence

COPY ltp-platform/src/ltp-storage-common /opt/ltp-platform/ltp-storage-common
COPY ltp-platform/src/postgresql-sdk/sdk /opt/ltp-platform/postgresql-sdk

RUN pip3 install --break-system-packages --no-cache-dir \
      /opt/switch-operations \
      /opt/feishu-bitable \
      /opt/node-operations \
      /opt/agent-evidence \
      /opt/ltp-platform/ltp-storage-common \
      /opt/ltp-platform/postgresql-sdk

# Detection-specific entrypoint
COPY agents/detection-agent/entrypoint.sh /detection-entrypoint.sh
RUN chmod +x /detection-entrypoint.sh
COPY agents/detection-agent/setup-mcp.sh /usr/local/bin/setup-mcp.sh
RUN chmod +x /usr/local/bin/setup-mcp.sh
