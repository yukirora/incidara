# Feedback agent Docker image.
# Extends the base claude-agent image with agent-feedback + node-operations + ltp-sdk.
# Pure analysis — no ipmitool, no SSH.

FROM claude-agent:latest

COPY mcp_servers/agent-feedback /opt/agent-feedback
COPY mcp_servers/node-operations /opt/node-operations
COPY mcp_servers/agent-evidence /opt/agent-evidence

COPY ltp-platform/src/ltp-storage-common /opt/ltp-platform/ltp-storage-common
COPY ltp-platform/src/postgresql-sdk/sdk /opt/ltp-platform/postgresql-sdk

RUN pip3 install --break-system-packages --no-cache-dir \
      /opt/agent-feedback \
      /opt/node-operations \
      /opt/agent-evidence \
      /opt/ltp-platform/ltp-storage-common \
      /opt/ltp-platform/postgresql-sdk

# pr-creation.sh needs jq for Codeup API responses
RUN apt-get update -qq && apt-get install -y -qq jq && rm -rf /var/lib/apt/lists/*

COPY agents/feedback-agent/setup-mcp.sh /usr/local/bin/setup-mcp.sh
RUN chmod +x /usr/local/bin/setup-mcp.sh
