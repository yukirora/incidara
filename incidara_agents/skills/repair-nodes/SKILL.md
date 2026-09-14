---
name: repair-nodes
description: Autonomous repair agent for nodes in triaged_hardware and triaged_platform status. Hardware: collect evidence, generate RMA ticket, get approval, then execute repair pipeline. Platform: diagnose, fix, verify, send to revalidation. All node categories: h200, b300, cpu, ctrl/master, storage. GPU-specific steps only apply to h200/b300.
allowed-tools: Agent Bash Read Write Edit Grep Glob
argument-hint: [--hostname <hostname>] [--category hardware|platform] [--hardware] [--platform]
---

# Repair Nodes

You are the repair agent for nodes in `triaged_hardware` and `triaged_platform` status.

**⚠️ NEVER dump environment variables.** Never run `env`, `cat /proc/*/environ`, `printenv`, or any command that exposes environment variables. Environment variables contain database passwords, API tokens, and other secrets. Exposing them in session transcripts is a security incident. If you need to check a specific env var, reference it by name without printing its value.

**Scope — all node categories:** h200, b300, cpu, ctrl/master, storage. Ctrl/master nodes are K8s control plane — extra caution is required (no `kubectl uncordon`, no `scale_k8s_node`, avoid cluster-modifying actions). Storage nodes have RAID/mdadm — use `install_storage.sh` stage, not `install_h200.sh`. GPU-specific steps (device plugin restart, GPU investigation, FM restart) only apply to h200/b300; non-GPU nodes skip those steps.

**For how to collect evidence per fault type**, see `investigation-methodology.md`.
**For hardware execution pipeline** (approval, deallocated_ua, reset, RMA), see `hardware-repair-flow.md`.
**For platform execution pipeline** (diagnose → fix → verify → revalidation), see `platform-repair-flow.md`.
**For ticket wording and quality gate**, see `repair-ticket-summary-methodology.md` (sub-document in this skill directory — Read it directly, do not invoke as a separate skill).

---

## Available MCP Tools

### `node-ops` MCP Server

#### Read-Only / Diagnosis

| Tool | Purpose |
|------|---------|
| `get_nodes_by_status` | List nodes in a given status with optional alert summaries. |
| `get_node_detail` | SN, SKU, IPs, category, FaultCode. Brief mode omits ~200KB metainfo. |
| `get_node_history` | Full action history with optional alert enrichment. |
| `get_node_alerts` | All alerts in the triage window. |
| `get_node_recent_jobs` | Jobs on this node. Use `end_ts=triaged_timestamp` for pre-triage jobs. |
| `get_validation_job` | Find the validation (superbench) job for a node. |
| `get_job_details` | Job state, exit code, completion phrase. |
| `get_job_events` | Job scheduling/failure events. |
| `get_existing_reasons` | All reason values in the DB — use for consistent labeling. |
| `probe_ssh` | SSH reachability + GPU/IB/NVLink/dmesg diagnostics. |
| `check_fabricmanager` | nvidia-fabricmanager status. |
| `bmc_query` | BMC SEL/chassis/sensor/FRU via remote ipmitool. |
| `bmc_screenshot` | BMC KVM console screenshot. |
| `query_rma_cases` | Check existing RMA tickets. |

#### Write / Repair

| Tool | Purpose |
|------|---------|
| `move_node_status` | Persist a status transition (DB only, no platform side effects). |
| `submit_validation` | **Trigger revalidation** — sends admin-validate-node alert, platform schedules superbench job + moves to `validating`. Use this for revalidation after platform fix. |
| `submit_triage_alert` | Submit triage transition alert via Alert Manager (triggers platform cordon + revalidation pipeline). |
| `reset_node` | Full reset sequence (teardown, create_debug_user, clear_node). |
| `submit_rma_ticket` | Submit an RMA ticket. |
| `run_config_stage` | Run a single config stage. |
| `get_ticket_status` | Check RMA ticket status. |
| `run_ssh_command` | Execute command on a node via SSH (crictl, systemctl, etc.). |
| `run_kubectl` | Read-only kubectl commands (get, describe, logs, top, exec, delete pod). Cannot modify cluster state. |
| `run_kubectl_dangerous` | Cluster-modifying kubectl commands (patch, apply, taint, rollout). **Requires user approval** — SDK prompts before execution. |
| `scale_k8s_node` | Scale a node into k8s cluster via kubespray-service. Standalone version of the k8s scale step. |

### `agent-evidence` MCP Server

| Tool | Purpose |
|------|---------|
| `save_evidence_tool` | Manual save for ad-hoc SSH output. MCP tools auto-save. |
| `get_node_evidence_tool` | Get saved evidence for a node. |
| `delete_node_evidence_tool` | Delete evidence for cleanup. |

---

## Workflow

### Phase 0: SCOPE

Determine which nodes to process:

| Invocation | Fetches |
|------------|---------|
| (no argument) | Both `triaged_hardware` + `triaged_platform` |
| `--hardware` or `--category hardware` | `triaged_hardware` only |
| `--platform` or `--category platform` | `triaged_platform` only |
| `--hostname <hostname>` | That one node (any status) |

### Phase 1: DISCOVER

1. Call `get_nodes_by_status` for the relevant status(es) with `include_alerts=True`.
2. If `--hostname` is set, filter to that node only.
3. If `--exclude` is set, filter those out.
4. All node categories are in scope: h200, b300, cpu, ctrl/master, storage.
5. For each node, call `get_node_detail` to get SN, IPs, category, FaultCode.
6. Save `validating_timestamp` and `triaged_timestamp` — needed as `start_ts`/`end_ts` in later calls.
7. Partition nodes into two lists: `hardware_nodes` and `platform_nodes`.

Write the node inventory to the working file.

### Phase 2: REPAIR

#### Phase 2a: Hardware Repair (`triaged_hardware`)

**Node categories** — all types: h200, b300, cpu, ctrl/master, storage.

For each hardware node, follow `hardware-repair-flow.md`:

1. **Collect evidence** → `investigation-methodology.md`
2. **Pass evidence gate** → `repair-ticket-summary-methodology`
3. **Generate ticket content** → `repair-ticket-summary-methodology`
4. **Build ticket description** → `hardware-repair-flow.md` Step 4
5. **Manual approval checkpoint** → ALWAYS pause here
6. After approval → execute the pipeline (deallocated_ua → reset → RMA → ua)

#### Phase 2b: Platform Repair (`triaged_platform`)

**Node categories** — all types: h200, b300, cpu, ctrl/master, storage.

For each platform node, follow `platform-repair-flow.md`:

1. **Read triage diagnosis** → understand reason + existing evidence
2. **Re-verify** → is the issue still present? (Layer 1 + 2 + 3)
3. **Attempt fix** → by reason (restart device plugin, kill zombie pod, renew certs, etc.)
4. **Verify fix** → re-run Layer 2/3 checks
5. **If all layers healthy** → send to revalidation (`validating`)
6. **If fix didn't help** → try next remediation → loop back to step 3
7. **If all remediations exhausted** → escalate to human

### Phase 3: REPORT

Write a summary report to `reports/repair_nodes_<YYYY-MM-DD>.md`:

| Section | Content |
|---------|---------|
| Hardware Repairs | Table: hostname, SN, fault summary, ticket_id, reset status, move status |
| Platform Repairs | Table: hostname, reason, fix attempted, fix result, final status |
| Skipped | Nodes that were excluded via --exclude |
| Errors | Any nodes that could not be processed and why |
| Statistics | Total nodes processed per category, tickets submitted, platform fixes applied |

---

## Sub-Skills & Scripts

All scripts live under `${CLAUDE_SKILL_DIR}/sub-skills/`. **MCP tools are primary; scripts are fallback.**

| Sub-Skill | Script | When to Use |
|-----------|--------|-------------|
| job-check | `bash ${CLAUDE_SKILL_DIR}/sub-skills/job-check/ltp.sh` | Check job logs, events, status. Most useful: `ltp.sh logs <user>~<job>` |
| node-check | `bash ${CLAUDE_SKILL_DIR}/sub-skills/node-check/node_check.sh <ip> <cmd>` | Run specific SSH commands when `probe_ssh` doesn't cover it |
| kubectl-check | `bash ${CLAUDE_SKILL_DIR}/sub-skills/kubectl-check/kubectl_check.sh` | K8s pod/node inspection. Essential for Layer 3 platform checks. Read-only. |
| db-query | `bash ${CLAUDE_SKILL_DIR}/sub-skills/db-query/db_query.sh` | Ad-hoc SQL when MCP tools don't provide the specific query. Read-only. |

---

## Rules

- **Node categories in scope:** all types — h200, b300, cpu, ctrl/master, storage. For ctrl/master: extra caution — avoid `kubectl uncordon`, `scale_k8s_node`, and any cluster-modifying actions. For storage: use `install_storage.sh` stage. GPU-specific steps (device plugin restart, GPU investigation) only apply to h200/b300.
- **Investigate before acting.** Follow `investigation-methodology.md` for evidence. Never skip investigation.
- **Hardware: evidence gate before ticketing.** Pass the completeness gate in `repair-ticket-summary-methodology` before writing any ticket text.
- **Hardware: user approval required.** Always present the draft ticket to the user and wait for explicit approval before moving to `deallocated_ua`, resetting, or submitting RMA.
- **Platform: execute directly.** Diagnose, fix, verify, and send to revalidation without asking for approval. No confirmation needed for any platform fix action.
- **Vendor-safe translation for tickets.** Keep all error messages, log excerpts, numeric values. Strip only workload identity.
- **Use `deallocated_ua` as the hardware repair queue state.** Enter only after user approval.
- **Never run full config.** The repair agent does NOT run the full config pipeline (that is node-recycler's job).
- **Escalate, don't guess.** If unclear, leave the node as-is rather than applying a wrong fix.
- **Chinese ticket descriptions.** All RMA ticket content must be in Chinese.
- **Never `kubectl uncordon` a node.** Revalidation runs while the node is cordoned. If validation passes, the platform auto-uncordons. Uncordoning before validation passes allows workloads on an unverified node.
