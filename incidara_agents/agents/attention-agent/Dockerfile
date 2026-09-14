FROM claude-agent:latest

# Skills (including attention_queue scanner) are mounted at runtime via sync-skills.
# No Python package install needed — the scanner runs from within the skill directory.
COPY agents/attention-agent/setup-mcp.sh /usr/local/bin/setup-mcp.sh
RUN chmod +x /usr/local/bin/setup-mcp.sh
