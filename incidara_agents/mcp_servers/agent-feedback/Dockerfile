# agent-feedback MCP server — standalone container.
#
# Build context: mcp_servers/agent-feedback/

FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/agent-feedback

COPY pyproject.toml .
COPY agent_feedback/ agent_feedback/
COPY mcp_server.py .

RUN pip install --no-cache-dir "fastmcp>=3.0.0" -e . && \
    pip install --no-cache-dir pytest

COPY entrypoint.sh /entrypoint.sh
COPY tests/ tests/
RUN chmod +x /entrypoint.sh

EXPOSE 8091

ENTRYPOINT ["/entrypoint.sh"]
