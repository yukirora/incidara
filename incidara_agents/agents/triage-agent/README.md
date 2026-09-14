# Triage Agent

Investigates and categorizes unhealthy nodes in `triaged_unknown` and `triaged_hardware` status. Delegates confirmed hardware issues to the repair agent via incidara-console.

**Workflow:** fetch nodes → categorize by alert type → investigate (SSH, DB, BMC) → classify (hardware/platform) → delegate hardware to repair agent.

## Role & Permissions

**MCP role: `diagnosis`** — read access plus limited write tools. No destructive operations.

| Capability | Tools |
|---|---|
| Read node data | `get_nodes_by_status`, `get_node_detail`, `get_node_history`, `get_node_alerts`, `get_node_recent_jobs`, `get_job_details`, `get_job_events`, `get_validation_job`, `get_existing_reasons`, `query_rma_cases` |
| Diagnose | `probe_ssh`, `check_fabricmanager`, `bmc_query`, `bmc_screenshot` |
| Status transitions | `move_node_status`, `submit_validation` |
| Delegation | `get_agent_active_tasks` (dedup check before delegating) |
| Evidence | `save_evidence_tool`, `get_node_evidence_tool`, `delete_node_evidence_tool` |

**Not available** (ops-only): `reset_node`, `submit_rma_ticket`, `run_ssh_command`, `run_kubectl`, `run_config_stage`, `submit_triage_alert`.

## Skills

| Skill | Purpose |
|---|---|
| `triage-nodes` | Batch workflow — categorize, investigate, classify, delegate |

## MCP Servers

| Server | Package | Purpose |
|---|---|---|
| `node-operations` | `/opt/node-operations` | Node DB queries, SSH probes, BMC, status transitions, delegation |
| `agent-evidence` | `/opt/agent-evidence` | Investigation evidence persistence (auto-save from node-ops tools) |

### Evidence DB

The `agent-evidence` MCP server persists investigation artifacts to a **separate PostgreSQL** (not the platform DB). This lets both triage and repair agents share evidence across sessions.

**Schema:**

| Column | Type | Description |
|--------|------|-------------|
| `id` | BIGSERIAL | Auto-increment PK |
| `node_name` | VARCHAR | Node hostname |
| `collected_at` | TIMESTAMPTZ | When evidence was collected |
| `collected_by` | VARCHAR | Which agent collected it (auto-set from `AGENT_NAME` env var, not user-supplied) |
| `source` | VARCHAR | Evidence source: `probe_ssh`, `nvidia_smi`, `dmesg`, `ib_stat`, `job_log`, `alert`, `nvlink`, `fabricmanager`, `job_list`, `job_detail`, `job_events`, `other` |
| `category` | VARCHAR | Fault subsystem: `gpu`, `ib`, `nvlink`, `pcie`, `cpu`, `memory`, `platform`, `unknown` |
| `summary` | TEXT | 1-line interpretation (e.g., "GPU 4: 42 uncorrectable ECC, Xid 79") |
| `content` | TEXT | Raw output — full tool/command output, not just interpretation |
| `metadata` | JSONB | Optional structured fields (e.g., `{"gpu_index": 4, "ecc_count": 42}`) |

**3-layer enforcement:**

1. **Auto-save**: `node-operations` MCP tools (`probe_ssh`, `get_node_alerts`, `get_node_history`, etc.) automatically INSERT to evidence DB as a side effect. Agent doesn't need to call `save_evidence_tool` explicitly for these.
2. **Proactive save**: For ad-hoc SSH output or curated excerpts, agent calls `save_evidence_tool` directly.
3. **Cross-agent handoff**: Triage agent saves evidence during investigation, then delegates the node to repair agent. Repair agent reads the triage evidence via `get_node_evidence_tool(hostname, collected_by="triage")` to avoid re-investigating what triage already collected.

### Provisioning the Evidence DB

The evidence DB runs on the **infrastructure PostgreSQL** (`infra/postgresql/`). The schema is in `infra/postgresql/init.sql` and is applied automatically on first container start.

```bash
# From the infra directory on the DB host:
cd infra/postgresql
cp .env.example .env   # fill in real credentials
make pg-up
```

This creates the `investigation_evidence` table + indexes. After that, set `EVIDENCE_DB_URL` in the agent's `.env` to point to this PostgreSQL instance.

> **Note**: The MCP server also calls `ensure_table()` at startup as a safety net, so the table is created even if `init.sql` hasn't run yet. But `init.sql` is the source of truth for the schema — any future migrations should go there.

## Data Dependencies

| Path (host) | Mount (container) | Purpose |
|---|---|---|
| `$AGENT_DATA/.env` | env file | Secrets (DB creds, API keys, tokens) |
| `$AGENT_DATA/workspace/` | `/app/workspace` | Reports, working files, session JSONL |
| `$AGENT_DATA/claude-home/` | `/root/.claude` | SDK internal data: conversations, MCP config (`~/.claude.json`), permissions (`settings.json`) |
| `$AGENT_DATA/skills/` | `/app/workspace/.claude/skills` | Skill files (auto-synced from repo on each run) |
| `$SSH_AUTH_SOCK` | same path | SSH agent forwarding socket |

### Workspace Files

Files the agent reads/writes during execution, all under `/app/workspace/`:

| File | Purpose |
|---|---|
| `triage_nodes_working.md` | Progress tracking — node list, batch assignments, per-node results. Written after each batch. Source of truth for resuming interrupted sessions. |
| `reports/triage_nodes_<YYYY-MM-DD>.md` | Final report — summary of all triaged nodes, classifications, and actions taken. Written after all batches complete. |
| `reports/triage_recall_<YYYY-MM-DD>.md` | Recall mode report — bulk classification results without investigation. |
| `sessions/` | Gateway event JSONL + session metadata. Written by `FsPersistence` (`SESSIONS_DIR`). Used for crash recovery on restart. |

## Build & Deploy

### Quick way (from remote server)

```bash
# 1. Start SSH agent (required for node access via agent forwarding)
eval $(ssh-agent -s)
ssh-add ~/.ssh/id_rsa

# 2. Add .env
cp env.example $AGENT_DATA/.env
vim $AGENT_DATA/.env    # fill in real credentials

# 3. Deploy (run from repo root)
cd ~/incidara/compose/rendered && docker compose up -d --build triage-agent
```

### Manual way

```bash
cd incidara_agents/agents/triage-agent

# Build (from repo root)
make build          # builds claude-agent:latest, then claude-agent:triage

# Deploy
sudo make run       # starts container on port 8000

# Restart
sudo make restart

# Logs
make logs

# Health check
make health         # curl http://127.0.0.1:8000/health
```

### Runtime Directory

```
$AGENT_DATA                 ← runtime data (.env, workspace/, claude-home/, skills/)
  .env                       ← secrets (DB creds, API keys, tokens)
  workspace/                 → /app/workspace  (reports, working files)
  claude-home/               → /root/.claude   (SDK conversations)
  skills/                    → /app/workspace/.claude/skills (auto-synced from repo on each run)
```

### How to Use

Send a prompt via the gateway API (port 8000) or incidara-console. Examples:

```
# Triage all unknown nodes
Triage all triaged_unknown nodes.

# Triage with auto-execute (no approval gate)
Triage all triaged_unknown nodes --execute

# Triage hardware nodes (skip categorization)
Triage all triaged_hardware nodes.

# Triage a specific node
Triage node h200-000265.

# Recall mode — bulk classify + delegate known hardware issues
Triage unknown nodes in recall mode.
```

### Execution Modes

| Mode | Flag | Behavior |
|---|---|---|
| Dry-run | (none) | Present report, wait for user approval before executing actions |
| Auto-execute | `--execute` | Execute actions without approval gate |
| Recall | `recall` | Bulk classify + delegate known hardware issues without investigation |

## Developer Guide

### Code Structure

```
incidara_agents/
├── agents/
│   ├── claude-agent/           # Base image (gateway server, SDK adapter)
│   │   ├── src/                #   TypeScript: Express server, ClaudeAdapter, routes
│   │   ├── Dockerfile          #   Generic runtime (no MCP servers)
│   │   └── entrypoint.sh       #   SSH agent, LTP token, bind mount warmup
│   └── triage-agent/           # This agent
│       ├── Dockerfile          #   FROM claude-agent:latest + pip install MCP servers
│       ├── Makefile            #   build / run / stop / sync-skills
│       ├── setup-mcp.sh        #   Writes ~/.claude.json + settings.json at startup
│       └── env.example         #   Required env vars template
├── mcp_servers/
│   ├── node-operations/        # Node DB, SSH, BMC, status transitions, delegation
│   │   ├── mcp_server.py       #   MCP tool definitions + ROLE_TOOLS access control
│   │   └── node_operations/    #   db_read.py, db_write.py, ssh.py, bmc.py, delegation.py, ...
│   └── agent-evidence/         # Investigation evidence persistence
│       └── agent_evidence/     #   evidence_db.py
├── infra/
│   └── postgresql/             # Infrastructure PostgreSQL
│       ├── init.sql            #   Schema: investigation_evidence table
│       └── Makefile            #   pg-up / pg-down / pg-psql
└── skills/
    └── triage-nodes/           # Skill files (auto-synced to container on each run)
        ├── SKILL.md            #   Main workflow orchestration
        ├── categorization-rules.md   #   Alert → classification mapping
        ├── investigation-methodology.md  #   What to collect per issue type
        ├── config.md           #   SSH/Jump host config
        ├── scripts/            #   SSH setup, kubectl/node check helpers
        └── sub-skills/         #   DB query SQL files
```

### How It Works

1. **Container starts** → `entrypoint.sh` sets up SSH, LTP token, warms bind mounts
2. **`setup-mcp.sh` runs** → reads `.env`, writes `~/.claude.json` (MCP server config) + `settings.json` (tool permissions)
3. **Gateway server launches** → `node dist/server.js` on port 8000
4. **Prompt arrives** → `POST /sessions` → ClaudeAdapter calls `query()` from `@anthropic-ai/claude-code` SDK
5. **SDK discovers MCP servers** from `~/.claude.json`, launches them as stdio subprocesses
6. **SDK calls MCP tools** → `node-operations` queries DB, probes SSH, checks BMC; `agent-evidence` auto-saves evidence
7. **Events stream** via SSE to connected clients (incidara-console)
8. **Delegation** → `delegate_to_agent` tool creates repair tasks via Chat UI gateway API

### MCP Role System

`node-operations` uses `ROLE_TOOLS` to control which tools each role can access. Set via `MCP_SERVER_ROLE` env var:

| Role | Can read | Can write | Can execute |
|------|----------|-----------|-------------|
| `readonly` | ✅ All read tools + probe/bmc/query | ❌ | ❌ |
| `diagnosis` | ✅ All read tools + probe/bmc/query | ✅ `move_node_status`, `submit_validation` | ❌ |
| `ops` | ✅ All read tools + probe/bmc/query | ✅ All writes + `reset_node`, `submit_rma_ticket` | ✅ `run_ssh_command`, `run_kubectl` |

Triage agent uses `diagnosis`. Repair agent uses `ops`.

### Delegation Flow

When triage classifies a node as `triaged_hardware`:

1. `get_agent_active_tasks("repair")` — check if repair already has a task for this hostname (dedup)
2. `delegate_to_agent(hostname, ...)` — creates a new task via `POST /api/agents/repair-draft/sessions` on Chat UI
3. Repair agent picks up the task and begins investigation

### Adding a New MCP Tool

1. Add the tool function in `mcp_servers/node-operations/mcp_server.py` using `_register("tool_name")`
2. Add the tool name to the appropriate role set in `ROLE_TOOLS`
3. Add the tool name to `settings.json` permissions in `setup-mcp.sh`
4. Rebuild: `make build && sudo make run`

### Adding a New Skill

1. Create skill directory under `incidara_agents/skills/`
2. Add the skill name to `AGENT_SKILLS` in the agent's `Makefile`
3. Rebuild: `make build && sudo make run` (skills are auto-synced on each `make run`)

### Local Development

```bash
# Edit MCP server code locally, then:
rsync -avz incidara_agents/mcp_servers/node-operations/ \
  -e "ssh -A" <user>@<remote>:<repo-path>/incidara_agents/mcp_servers/node-operations/

# Rebuild + restart on remote:
ssh <user>@<remote> 'cd <repo-path>/compose/rendered && docker compose up -d --build triage-agent'
```

## Environment Variables

See `env.example` for the full list. Required:

| Variable | Description |
|---|---|
| `ANTHROPIC_AUTH_TOKEN` | Claude API key |
| `POSTGRES_CONNECTION_STR` | Platform DB connection string |
| `MCP_SERVER_ROLE` | Must be `diagnosis` |
| `BMC_PASSWORD` | BMC ipmitool access |
| `EVIDENCE_DB_URL` | Agent evidence DB (PostgreSQL) |
| `AGENT_NAME` | Set to `triage` |
| `LTP_HOST` / `LTP_TOKEN` | Alert Manager API for `submit_validation` |
| `CHAT_UI_URL` | Chat UI for delegation (default `http://127.0.0.1:3001`) |
| `CHAT_UI_USER` / `CHAT_UI_PASSWORD` | Chat UI delegation auth |
| `JUMP_HOST` | SSH jump host for reaching nodes |

