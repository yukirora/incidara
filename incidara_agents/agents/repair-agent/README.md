# Repair Agent

Diagnoses and repairs triaged hardware nodes. GPU-only scope (h200/b300).

**Two flows:**
- **Hardware repair**: investigate → collect evidence → draft ticket → user approval → `submit_rma_ticket` → node → `ua`
- **Platform repair**: diagnose → fix (kubectl, systemctl, etc.) → verify → `submit_validation` → node → `validating`

## Role & Permissions

**MCP role: `ops`** — full read + write, including destructive operations.

| Capability | Tools |
|---|---|
| Read node data | `get_nodes_by_status`, `get_node_detail`, `get_node_history`, `get_node_alerts`, `get_node_recent_jobs`, `get_job_details`, `get_job_events`, `get_validation_job`, `get_existing_reasons`, `query_rma_cases` |
| Diagnose | `probe_ssh`, `check_fabricmanager`, `bmc_query`, `bmc_screenshot` |
| Status transitions | `move_node_status`, `submit_validation`, `submit_triage_alert` |
| Execute remotely | `run_ssh_command`, `run_kubectl` |
| Repair actions | `reset_node`, `submit_rma_ticket`, `run_config_stage`, `get_ticket_status` |
| Evidence | `save_evidence_tool`, `get_node_evidence_tool`, `delete_node_evidence_tool` |

**Approval model:** Hardware repair always requires user approval before destructive actions. Platform repair executes directly without approval.

## Skills

| Skill | Purpose |
|---|---|
| `repair-nodes` | Main workflow — investigate, fix/submit ticket, verify, send to revalidation |
| `repair-ticket-summary-methodology` | Vendor-safe ticket writing — translate errors, strip workload identity, evidence completeness gate |

## MCP Servers

| Server | Package | Purpose |
|---|---|---|
| `node-operations` | `/opt/node-operations` | Node DB queries, SSH, BMC, status transitions, repair actions |
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
3. **Evidence gate**: Before submitting a ticket, `get_node_evidence_tool` checks that mandatory evidence per fault type exists. Missing evidence blocks ticket submission.
4. **Cross-agent handoff**: Repair agent reads evidence saved by triage agent via `get_node_evidence_tool(hostname, collected_by="triage")` — avoids re-running probes that triage already executed.

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
| `reports/repair_nodes_<YYYY-MM-DD>.md` | Final report — summary of all repaired nodes, actions taken, ticket IDs, outcomes. Written after Phase 3. |
| `sessions/` | Gateway event JSONL + session metadata. Written by `FsPersistence` (`SESSIONS_DIR`). Used for crash recovery on restart. |

Note: Unlike triage agent, the repair agent processes one node at a time (not batches), so it doesn't use a separate working file. Evidence is persisted to the evidence DB instead.

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
cd ~/incidara && bash scripts/deploy.sh repair
```

### Manual way

```bash
cd agents/repair-agent

# Build (from repo root)
make build          # builds claude-agent:latest, then claude-agent:repair

# Deploy
sudo make run       # starts container on port 8003

# Restart
sudo make restart

# Logs
make logs

# Health check
make health         # curl http://127.0.0.1:8003/health
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

Send a prompt via the gateway API (port 8003) or incidara-console. Examples:

```
# Repair a specific hardware node
Repair node h200-000265. It has GPU ECC errors.

# Platform repair
Node h200-000113 has FabricManager mismatch. Diagnose and fix.

# Check a node's status
What is the current status of h200-000795?
```

## Developer Guide

### Code Structure

```
incidara_agents/
├── agents/
│   ├── claude-agent/           # Base image (gateway server, SDK adapter)
│   │   ├── src/                #   TypeScript: Express server, ClaudeAdapter, routes
│   │   ├── Dockerfile          #   Generic runtime (no MCP servers)
│   │   └── entrypoint.sh       #   SSH agent, LTP token, bind mount warmup
│   └── repair-agent/           # This agent
│       ├── Dockerfile          #   FROM claude-agent:latest + pip install MCP servers
│       ├── Makefile            #   build / run / stop / sync-skills
│       ├── setup-mcp.sh        #   Writes ~/.claude.json + settings.json at startup
│       └── env.example         #   Required env vars template
├── mcp_servers/
│   ├── node-operations/        # Node DB, SSH, BMC, status transitions, repair actions
│   │   ├── mcp_server.py       #   MCP tool definitions + ROLE_TOOLS access control
│   │   └── node_operations/    #   db_read.py, db_write.py, ssh.py, bmc.py, reset.py, ...
│   └── agent-evidence/         # Investigation evidence persistence
│       └── agent_evidence/     #   evidence_db.py
├── infra/
│   └── postgresql/             # Infrastructure PostgreSQL
│       ├── init.sql            #   Schema: investigation_evidence table
│       └── Makefile            #   pg-up / pg-down / pg-psql
└── skills/
    └── repair-nodes/           # Skill files (auto-synced to container on each run)
        ├── SKILL.md            #   Main workflow orchestration
        ├── hardware-repair-flow.md
        ├── platform-repair-flow.md
        ├── investigation-methodology.md
        └── repair-ticket-summary-methodology/  # Vendor-safe ticket writing
```

### How It Works

1. **Container starts** → `entrypoint.sh` sets up SSH, LTP token, warms bind mounts
2. **`setup-mcp.sh` runs** → reads `.env`, writes `~/.claude.json` (MCP server config) + `settings.json` (tool permissions)
3. **Gateway server launches** → `node dist/server.js` on port 8003
4. **Prompt arrives** → `POST /sessions` → ClaudeAdapter calls `query()` from `@anthropic-ai/claude-code` SDK
5. **SDK discovers MCP servers** from `~/.claude.json`, launches them as stdio subprocesses
6. **SDK calls MCP tools** → `node-operations` queries DB, runs SSH, submits tickets; `agent-evidence` auto-saves evidence
7. **Events stream** via SSE to connected clients (incidara-console)

### MCP Role System

`node-operations` uses `ROLE_TOOLS` to control which tools each role can access. Set via `MCP_SERVER_ROLE` env var:

| Role | Can read | Can write | Can execute |
|------|----------|-----------|-------------|
| `readonly` | ✅ All read tools + probe/bmc/query | ❌ | ❌ |
| `diagnosis` | ✅ All read tools + probe/bmc/query | ✅ `move_node_status`, `submit_validation` | ❌ |
| `ops` | ✅ All read tools + probe/bmc/query | ✅ All writes + `reset_node`, `submit_rma_ticket` | ✅ `run_ssh_command`, `run_kubectl` |

Repair agent uses `ops`. Triage agent uses `diagnosis`.

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
ssh <user>@<remote> 'cd <repo-path> && bash scripts/deploy.sh repair'
```

### Mock Mode (for testing without real DB/SSH)

Set `REPLAY_MOCK_DIR` to a directory of JSON mock files. Each file: `{"result": "<string>"}`. The repair-draft-agent supports this out of the box.

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_AUTH_TOKEN` | Claude API key |
| `POSTGRES_CONNECTION_STR` | Platform DB connection string |
| `MCP_SERVER_ROLE` | Must be `ops` |
| `SSH_USER` / `SSH_PASSWORD` | Node SSH access (agent forwarding + pexpect fallback) |
| `RESET_SSH_USER` / `RESET_SSH_PASSWORD` | Post-reset SSH user (ubuntu) |
| `BMC_PASSWORD` / `RESET_BMC_PASSWORD` | BMC ipmitool access (pre/post reset) |
| `TICKET_BASE_URL` / `TICKET_AUTH_ZNSL` | RMA ticket API |
| `EVIDENCE_DB_URL` | Agent evidence DB (PostgreSQL) |
| `AGENT_NAME` | Set to `repair` |
| `LTP_HOST` / `LTP_TOKEN` | Alert Manager API |
| `BUNDLES_ROOT` | Init bundle scripts path (default `/opt/node-operations/bundles`) |


