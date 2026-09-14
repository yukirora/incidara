# Node Operations MCP Server

MCP server for AI agent node operations: SSH probing, BMC checks, node reset, config pipeline, RMA tickets, and DB status management.

## Installation

```bash
cd incidara_agents/mcp-servers/node-operations
pip install -e ".[all]"
```

For development (no DB/MCP deps):
```bash
pip install -e ".[dev]"
```

## Roles

| Role | Capabilities | Use Case |
|------|-------------|----------|
| `readonly` | read DB, probe SSH | Investigation |
| `diagnosis` | readonly + fabricmanager check + move status | Triage agent |
| `ops` | diagnosis + reset, RMA ticket, config stages, ticket status | Repair agent |
| `full` | all tools | Engineer ad-hoc via Claude Code |

## Claude Code MCP Setup

Add to `.claude/settings.json`:

### Triage Agent (diagnosis role)
```json
{
  "mcpServers": {
    "node-operations": {
      "command": "python",
      "args": ["mcp_server.py", "--role", "diagnosis"],
      "cwd": "incidara_agents/mcp-servers/node-operations",
      "env": {
        "SSH_USER": "operator",
        "SSH_KEY_PATH": "/path/to/key",
        "SSH_TIMEOUT": "30"
      }
    }
  }
}
```

### Repair Agent (ops role)
```json
{
  "mcpServers": {
    "node-operations": {
      "command": "python",
      "args": ["mcp_server.py", "--role", "ops"],
      "cwd": "incidara_agents/mcp-servers/node-operations",
      "env": {
        "SSH_USER": "operator",
        "SSH_KEY_PATH": "/path/to/key",
        "SSH_PASSWORD": "...",
        "RESET_SSH_USER": "...",
        "RESET_SSH_PASSWORD": "...",
        "BMC_PASSWORD": "...",
        "RESET_BMC_PASSWORD": "...",
        "TICKET_BASE_URL": "https://...",
        "TICKET_AUTH_ZNSL": "..."
      }
    }
  }
}
```

### Full Access (engineer)
```json
{
  "mcpServers": {
    "node-operations": {
      "command": "python",
      "args": ["mcp_server.py", "--role", "full"],
      "cwd": "incidara_agents/mcp-servers/node-operations",
      "env": {
        "SSH_USER": "operator",
        "SSH_KEY_PATH": "/path/to/key",
        "SSH_PASSWORD": "...",
        "RESET_SSH_USER": "...",
        "RESET_SSH_PASSWORD": "...",
        "BMC_PASSWORD": "...",
        "RESET_BMC_PASSWORD": "...",
        "TICKET_BASE_URL": "https://...",
        "TICKET_AUTH_ZNSL": "..."
      }
    }
  }
}
```

## Running Tests

```bash
cd incidara_agents/mcp-servers/node-operations
pip install -e ".[dev]"
python -m pytest tests/ -v
```
