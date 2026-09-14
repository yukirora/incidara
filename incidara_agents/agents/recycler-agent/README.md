# Recycler Agent

Poll-driven agent for routine node lifecycle operations: RMA ticket monitoring and node reallocation.

After the repair agent completes hardware repair (RMA ticket submitted, node moved to `ua`), the recycler agent takes over:

1. **Ticket tracking** — Poll vendor RMA tickets for `ua` nodes. When repair is complete, close the ticket and move the node to `ready_ua`.
2. **Node reallocation** — Take `ready_ua` nodes through the full bring-up pipeline: reset, full config, sysinfo collection, onboard clone, and allocate.

The recycler closes the loop: `ua` → `ready_ua` → `allocated_ua`.

## Role & Permissions

**MCP role: `ops`** — full read + write + execute. All tools auto-allowed (no human approval needed — routine post-RMA operations).

| Capability | Tools |
|---|---|
| Read node data | `get_nodes_by_status`, `get_node_detail`, `get_node_history`, `get_node_alerts`, `get_node_recent_jobs`, `get_job_details`, `get_job_events`, `get_validation_job`, `get_existing_reasons`, `query_rma_cases` |
| Diagnose | `probe_ssh`, `check_fabricmanager`, `bmc_query`, `bmc_screenshot` |
| Ticket lifecycle | `get_ua_nodes_with_tickets`, `get_ticket_status`, `complete_rma` |
| Reallocation | `reallocate_node`, `reset_node`, `run_full_config`, `run_config_stage`, `collect_sysinfo`, `clone_and_allocate` |
| Status transitions | `move_node_status`, `submit_validation`, `submit_triage_alert` |
| Execute remotely | `run_ssh_command`, `run_kubectl` |
| RMA | `submit_rma_ticket` |
| Evidence | `save_evidence_tool`, `get_node_evidence_tool`, `delete_node_evidence_tool` |

## Skills

| Skill | Status checked | Pattern | What it does |
|-------|---------------|---------|--------------|
| `ticket-check` | `ua` | 3-step (LLM in loop) | Fetch → check ticket per node → complete RMA or escalate |
| `node-reallocation` | `ready_ua` | Per-node pipeline (~20-30 min each) | Reset → config → sysinfo → allocate |

### Ticket Check: 3-Step Pattern

The LLM stays in the loop for ticket check — fast API calls, per-node decisions:

1. `get_ua_nodes_with_tickets()` — fetch all ua nodes + ticket IDs
2. `get_ticket_status(ticket_id)` — check per node (LLM sees result, decides action)
3. `complete_rma()` or `move_node_status()` — LLM decides: complete, escalate, or skip

Based on `repairStatus`:
- **Terminal** (completed, repaired) → `complete_rma()` → moves to `ready_ua`
- **In-progress** (repairing, pending) → skip, check next run
- **Anomalous** (rejected, cancelled, on-hold) → move to `triaged_unknown` for re-diagnosis

### Node Reallocation: Per-Node Pipeline

Reallocation is long-running (~20-30 min per node). Call `reallocate_node(hostname)` per node.
Use a working file to track progress across nodes (same pattern as triage agent).

Steps inside `reallocate_node(hostname)`:
1. Probe SSH — reset if unreachable
2. Run full config (7 stages + k8s) — takes ~20-30 minutes
3. Collect sysinfo (sbsysinfo, SKU, serial)
4. Clone onboard record + move to `allocated_ua`
5. On failure → move to `triaged_unknown`

### Tool Patterns

| Pattern | Tools | Use case |
|---------|-------|----------|
| Ticket check (3-step) | `get_ua_nodes_with_tickets` → `get_ticket_status` → `complete_rma`/`move_node_status` | LLM decides per node |
| Reallocation (per-node) | `reallocate_node(hostname)` | One call = entire pipeline for one node |
| Granular (one-off) | `probe_ssh`, `reset_node`, `run_full_config`, `collect_sysinfo`, `clone_and_allocate` | Step-by-step recovery, debugging |

## Node Status Flow

```
                    Repair Agent
                    ───────────
                         │
                         ▼
┌─────────┐    ┌─────────┐    ┌──────────┐    ┌────────────┐
│  ua     │───►│ ready_ua │───►│ allocated │    │ triaged_   │
│ (RMA    │    │ (ready   │    │  _ua      │    │  unknown   │
│  ticket │    │  to      │    │ (back in  │    │ (on        │
│  open)  │    │  recycle)│    │  cluster) │    │  failure)  │
└─────────┘    └─────────┘    └────────────┘    └────────────┘
     ▲                              ▲                   │
     │                              │                   ▼
     │         Recycler Agent       │            Triage Agent
     │         ─────────────        │            ────────────
     │                              │
     └── ticket-check ──────────────┘
         (close RMA ticket
          when repair done)

         node-reallocation ─────────┘
         (reset → config → allocate)
```

## Difference from Repair Agent

| | Repair Agent | Recycler Agent |
|---|---|---|
| **Input status** | `triaged_hardware`, `triaged_platform` | `ua`, `ready_ua` |
| **What it does** | Diagnose, collect evidence, submit RMA | Track RMA, reallocate nodes |
| **Runs full config?** | No (explicitly forbidden) | Yes (core responsibility) |
| **Ticket interaction** | Submit new RMA tickets | Poll & close existing RMA tickets |
| **Approval required?** | Yes (hardware repair) | No (automated) |
| **Typical duration** | 5-15 min per node | 20-30 min per node |
| **Failure mode** | Stop and ask human | Log error, move to triaged_unknown, continue |

## MCP Servers

| Server | Package | Purpose |
|--------|---------|---------|
| `node-operations` | `/opt/node-operations` | Node DB queries, SSH, BMC, status transitions, repair actions |
| `agent-evidence` | `/opt/agent-evidence` | Investigation evidence persistence (auto-save from node-ops tools) |

### Evidence DB

The `agent-evidence` MCP server persists artifacts to a **separate PostgreSQL** (not the platform DB). Shared with triage and repair agents.

See repair-agent README for full schema. 3-layer enforcement: auto-save (MCP tools), proactive save (ad-hoc SSH), evidence gate (ticket submission).

Provisioning: `infra/postgresql/init.sql` → `make pg-up`

## Data Dependencies

| Path (host) | Mount (container) | Purpose |
|---|---|---|
| `$AGENT_DATA/.env` | env file | Secrets (DB creds, API keys, tokens) |
| `$AGENT_DATA/workspace/` | `/app/workspace` | Reports, working files, session JSONL |
| `$AGENT_DATA/claude-home/` | `/root/.claude` | SDK internal data: MCP config, permissions |
| `$AGENT_DATA/skills/` | `/app/workspace/.claude/skills` | Skill files (auto-synced from repo on each run) |
| `$SSH_AUTH_SOCK` | same path | SSH agent forwarding socket |

### Workspace Files

| File | Purpose |
|---|---|
| `reports/ticket_check_<YYYY-MM-DD>.md` | Ticket check results — per-node status and actions |
| `reports/reallocation_<YYYY-MM-DD>.md` | Reallocation results — per-node outcomes |
| `sessions/` | Gateway JSONL persistence (crash recovery) |

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
cd ~/incidara && bash scripts/deploy.sh recycler
```

### Manual way

```bash
cd agents/recycler-agent

# Build (from repo root)
make build          # builds claude-agent:latest, then claude-agent:recycler

# Deploy
sudo make run       # starts container on port 8004

# Restart
sudo make restart

# Logs
make logs

# Health check
make health         # curl http://127.0.0.1:8004/health
```

### How to Use

Send a prompt via the gateway API (port 8004) or incidara-console.

**Scheduled (via incidara-console):**
- Ticket check: every 30 min during business hours
- Reallocation: every 15 min

**Manual:**
```
Run ticket-check
Run node-reallocation
```

**One-off (single node):**
```
Check ticket for h200-000795
Reallocate h200-000260
```

## Environment Variables

See `env.example` for the full list. Key vars:

| Variable | Description | Source |
|----------|-------------|--------|
| `MCP_SERVER_ROLE` | Must be `ops` | — |
| `AGENT_NAME` | Must be `recycler` | — |
| `PORT` | Gateway port (default 8004) | — |
| `CLUSTER_ID` | Cluster identifier (written to DB records) | `cluster.common.cluster-id` |
| `POSTGRES_CONNECTION_STR` | Platform DB (node status/actions) | `postgresql.connection-str` |
| `POSTGRES_SCHEMA` | DB schema (default `ltp_sdk`) | `alert-manager.postgresql.schema` |
| `EVIDENCE_DB_URL` | Agent evidence DB (PostgreSQL) | — |
| `TICKET_BASE_URL` | Vendor ticket API base URL | `physical.ticket-base-url` |
| `TICKET_AUTH_ZNSL` | Vendor ticket API auth token | `physical.ticket-auth-znsl` |
| `TICKET_TIMEOUT` | Ticket API timeout (default 15) | — |
| `K8S_MASTER_USER` | K8s master SSH user (for full config) | `physical.k8s-master-user` |
| `K8S_MASTER_IP` | K8s master IP (for full config) | `physical.k8s-master-ip` |
| `DATA_MOUNT` | Data mount path (default `/mnt/md0`) | `physical.data-mount` |
| `SYS_MOUNT` | System mount path (default `/mntsys`) | — |
| `EXT_MOUNT` | Extended mount path (default `/mntext`) | — |
| `KUBESPRAY_DIR` | Kubespray directory (default `/opt/kubespray`) | `physical.kubespray-dir` |
| `KUBESPRAY_SERVICE_URL` | Kubespray service URL | `physical.kubespray-service-url` |
