# Feedback Agent

Closes the intelligence loop: mines closed RMA outcomes, diagnoses why NFF/misclassification happened, patches skill files, and monitors results.

The feedback agent is the **only agent that modifies skill files**. All other agents consume skills; this one improves them.

## Workflow

```
Completed RMAs ──► collect-rma-cases ──► case_memory DB
                                              │
                                              ▼
                                    analyze-rma-cases ──► analysis_problems DB
                                                              │
                                  ┌───────────────────────────┤
                                  ▼                           ▼
                          case-diagnosis              (human review)
                                  │
                                  ▼
                          patch-and-validate ──► git branch + commit
                                  │
                                  ▼
                          human approval gate
                                  │
                                  ▼
                        update_problem(patch_created)
                                  │
                                  ▼
                     deploy-feedback-agent-patches ──► rebuild + restart agents
                                  │
                                  ▼
                        update_problem(monitoring)
                                  │
                                  ▼
                     check-monitoring ──► resolved / unresolved
```

## Role & Permissions

**MCP role: `feedback`** — read node data + write to knowledge DB. No destructive node operations.

| Capability | Tools |
|---|---|
| Read node data | `get_nodes_by_status`, `get_node_detail`, `get_node_history`, `get_node_alerts`, `get_node_recent_jobs`, `get_job_details`, `get_job_events`, `get_validation_job`, `get_existing_reasons`, `query_rma_cases` |
| Diagnose | `probe_ssh`, `check_fabricmanager` |
| Knowledge DB | `insert_case`, `query_similar_cases`, `query_completed_rmas`, `get_rule_stats`, `update_problem_tool`, `get_problems`, `get_problem_history` |
| Evidence | `save_evidence_tool`, `get_node_evidence_tool` |

**Approval model:** All skill patches require human approval before commit/deploy (gate in patch-and-validate). Collection and analysis run autonomously.

## Skills

| Skill | What it does |
|---|---|
| `collect-rma-cases` | Fetch completed RMAs from vendor ticket API → classify verdict → human review → insert into `case_memory` |
| `analyze-rma-cases` | Compute rule stats → identify NFF/misclassification patterns → create `analysis_problems` → delegate per-problem tasks |
| `skill-optimize` | Per-problem closed loop: case-diagnosis → patch-and-validate → human gate → commit. Orchestrates the fix. |
| `deploy-feedback-agent-patches` | Review git branch → PR → merge → deploy (rebuild + restart affected agents on remote) |
| `pr-creation` | Create Codeup merge requests via API |
| `pr-list` | Query Codeup merge requests |
| `triage-nodes` | Re-investigation capability for case-diagnosis step (shared with triage agent) |

### Status Lifecycle for Problems

```
open ──► patch_created ──► monitoring ──► resolved
  │           │                │
  ▼           ▼                ▼
blocked     open            unresolved
```

- **open**: needs diagnosis
- **patch_created**: branch committed, awaiting deploy
- **monitoring**: deployed, watching for results
- **resolved**: confirmed fix works
- **unresolved**: fix didn't work
- **blocked**: can't fix (infrastructure limitation, etc.)

## MCP Servers

| Server | Package | Purpose |
|---|---|---|
| `node-operations` | `/opt/node-operations` | Node DB queries, diagnosis tools |
| `agent-evidence` | `/opt/agent-evidence` | Investigation evidence persistence |
| `agent-feedback` | `/opt/agent-feedback` | Knowledge DB: `case_memory` + `analysis_problems` tables |

### Knowledge DB Schema

**`case_memory`** — completed RMA outcomes:
- `hostname`, `rma_ticket_id`, `fault_type`, `our_reason`, `vendor_repair`
- `vendor_verdict` (REPAIR_CONFIRMED, MISCLASSIFIED, MAINTENANCE_FIX, NO_FAULT_FOUND, CONFIG_TASK)
- `vendor_answer_quality` (DETAILED, MODERATE, MINIMAL, NONE)
- `our_evidence`, `our_investigation` (JSONB)

**`analysis_problems`** — append-only problem tracking:
- `problem_id`, `title`, `fault_type`, `status`, `diagnosis` (JSONB)
- `patch_summary`, `patch_commit`, `monitor_expectation`, `pr_url`
- Each update inserts a new row; `get_problem_history()` returns full timeline

Provisioning: `infra/postgresql/init.sql` → `make pg-up`

## Data Dependencies

| Path (host) | Mount (container) | Purpose |
|---|---|---|
| `$AGENT_DATA/.env` | env file | Secrets (DB creds, API keys, tokens) |
| `$AGENT_DATA/workspace/` | `/app/workspace` | Reports, working files, session JSONL |
| `$AGENT_DATA/claude-home/` | `/root/.claude` | SDK internal data: MCP config, permissions |
| `$AGENT_DATA/skills/` | `/app/workspace/.claude/skills` | Skill files (auto-synced from repo on each run) |
| `$AGENT_DATA/ssh/deploy_key` | `/root/.ssh/deploy_key` | Git deploy key for codeup repo access |
| `/data/agents/triage-unknown/workspace/sessions` | `/mnt/sessions/triage` | Triage session meta.json (read-only) |
| `/data/agents/repair/workspace/sessions` | `/mnt/sessions/repair` | Repair session meta.json (read-only) |
| `/data/agents/recycler/workspace/sessions` | `/mnt/sessions/recycler` | Recycler session meta.json (read-only) |
| `/data/agents/triage-unknown/claude-home/projects` | `/mnt/transcripts/triage` | Triage SDK transcripts (read-only) |
| `/data/agents/repair/claude-home/projects` | `/mnt/transcripts/repair` | Repair SDK transcripts (read-only) |
| `/data/agents/recycler/claude-home/projects` | `/mnt/transcripts/recycler` | Recycler SDK transcripts (read-only) |
| `$REPO_ROOT/skills` | `/mnt/skills` | Read-only reference copy of current deployed skills |
| `$SSH_AGENT_SOCK` | same path | SSH agent forwarding socket |

### Workspace Files

| File | Purpose |
|---|---|
| `reports/collect-review-<date>.json` | Collect-rma-cases human review table |
| `reports/vendor_feedback_<date>.md` | Analyze-rma-cases weekly report |
| `sessions/` | Gateway JSONL persistence (crash recovery) |

## Build & Deploy

### Quick way (from remote server)

```bash
# 1. Start SSH agent (required for git deploy key + node SSH)
eval $(ssh-agent -s)
ssh-add ~/.ssh/id_rsa

# 2. Add .env
cp env.example $AGENT_DATA/.env
vim $AGENT_DATA/.env    # fill in real credentials

# 3. Deploy (run from repo root)
cd ~/incidara/compose/rendered && docker compose up -d --build feedback-agent
```

### Manual way

```bash
cd incidara_agents/agents/feedback-agent

# Build (from repo root)
make build          # builds claude-agent:latest, then claude-agent:feedback

# Deploy
sudo make run       # starts container on port 8006

# Restart
sudo make restart

# Logs
make logs

# Health check
make health         # curl http://127.0.0.1:8006/health
```

### How to Use

Send a prompt via the gateway API (port 8006) or incidara-console.

**Scheduled (via incidara-console):**
- Collect RMA cases: weekly
- Analyze RMA cases: after each collect completes
- Check monitoring: weekly

**Manual:**
```
Run collect-rma-cases
Run analyze-rma-cases
Run skill-optimize problem_id=11
Check monitoring status
```

## Difference from Other Agents

| | Triage | Repair | Recycler | **Feedback** |
|---|---|---|---|---|
| **Operates on** | Live nodes | Live nodes | Live nodes | **Historical RMA data** |
| **Reads** | Node status, alerts | Node status, alerts | Node status, tickets | **case_memory, transcripts** |
| **Writes** | Node status transitions | RMA tickets, fixes | Node reallocation | **Skill files, git commits** |
| **Modifies skills?** | No | No | No | **Yes (only agent)** |
| **Needs SSH?** | Yes (diagnose) | Yes (fix) | Yes (reset/config) | **Only for deploy** |

## Environment Variables

See `.env.example` for the full list. Key vars:

| Variable | Description | Source |
|----------|-------------|--------|
| `MCP_SERVER_ROLE` | Must be `feedback` | — |
| `AGENT_NAME` | Must be `feedback` | — |
| `PORT` | Gateway port (default 8006) | — |
| `EVIDENCE_DB_URL` | Agent evidence DB (PostgreSQL .23:5434) | — |
| `POSTGRES_CONNECTION_STR` | Platform DB (.19:5432) — read vendor RMA data | `postgresql.connection-str` |
| `POSTGRES_SCHEMA` | DB schema (default `ltp_sdk`) | `alert-manager.postgresql.schema` |
| `CHAT_UI_URL` | Chat UI URL (for delegation + reports) | — |
| `CHAT_UI_DB_URL` | Chat UI DB — session ID lookup for transcript mining | — |
| `TICKET_BASE_URL` | Vendor ticket API base URL | `physical.ticket-base-url` |
| `TICKET_AUTH_ZNSL` | Vendor ticket API auth token | `physical.ticket-auth-znsl` |
| `DEPLOY_HOST` | Remote host IP for agent rebuilds | — |
| `BUILD_ROOT` | Build root on remote host | — |
| `CODEUP_TOKEN` | Codeup API token (for MR creation) | — |
| `CODEUP_ORG_ID` | Codeup organization ID | — |
| `GIT_REPO_URL` | Git repo URL (for skill patches) | — |
| `GIT_AUTHOR_NAME` | Git commit author name | — |
| `GIT_AUTHOR_EMAIL` | Git commit author email | — |
