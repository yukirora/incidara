---
name: triage-nodes
description: Triage nodes in triaged_unknown and triaged_hardware status. For unknown: categorize, investigate, decide. For hardware: verify issue still present, fill evidence gaps, then decide action or delegate to repair. Processes nodes in small batches to avoid context overflow.
allowed-tools: Agent Bash Read Write Edit Grep Glob
argument-hint: [unknown|hardware|node <hostname>] [--execute|--dry-run] [--exclude node1,node2]
---

# Triage Nodes

You are the triage orchestrator for nodes in `triaged_unknown` and `triaged_hardware` status.

**Entry points:**

| Invocation | Fetches | Categorize? |
|------------|---------|-------------|
| `triage unknown` | `triaged_unknown` nodes | Yes — match alerts to rules |
| `triage hardware` | `triaged_hardware` nodes | No — review existing reason |
| `triage node <hostname>` | That one node (any status) | Depends on its status |
| (no argument) | `triaged_unknown` nodes (default) | Yes |

For **what each alert pattern classifies as**, see `categorization-rules.md`.
For **how to investigate** each issue type (tools + decision tree), see `investigation-methodology.md`.
Config (SSH, LTP host, defaults) is in `config.md`.

---

## Sub-Skills & Scripts

All scripts live under `${CLAUDE_SKILL_DIR}/sub-skills/`. **MCP tools are primary; scripts are fallback when MCP is unavailable or insufficient.**

| Sub-Skill | Script | When to Use |
|-----------|--------|-------------|
| job-check | `bash ${CLAUDE_SKILL_DIR}/sub-skills/job-check/ltp.sh` | Check job logs, events, status. **Most useful:** `ltp.sh logs <user>~<job>` to see validation job container output. |
| node-check | `bash ${CLAUDE_SKILL_DIR}/sub-skills/node-check/node_check.sh <ip> <cmd>` | Run specific SSH commands on a node (GPU, NVLink, IB, dmesg, ports, drivers). Use when `probe_ssh` MCP tool doesn't cover the command you need. **Note: `dmesg` and `journalctl` require `sudo` — the script allows it.** |
| kubectl-check | `bash ${CLAUDE_SKILL_DIR}/sub-skills/kubectl-check/kubectl_check.sh` | K8s pod/node inspection — get/describe/logs. No MCP equivalent for kubectl queries. Read-only. |
| db-query | `bash ${CLAUDE_SKILL_DIR}/sub-skills/db-query/db_query.sh` | Ad-hoc SQL when MCP tools don't provide the specific query. Read-only. |
| execute-triage | `python3 ${CLAUDE_SKILL_DIR}/sub-skills/execute-triage/execute_triage.py` | Primary for `move_node_status`. Uses Alert Manager API (token required). Use `--dry-run` first when not in `--execute` mode. |

---

## MCP Tools vs Scripts — When to Use Which

| Task | Primary (MCP Tool) | Fallback (Script) |
|------|-------------------|--------------------|
| Get node list | `get_nodes_by_status` | `db_query.sh` + `get_triaged_unknown_nodes.sql` |
| Get node alerts | `get_node_alerts` | `db_query.sh` + `get_node_alerts.sql` |
| Get node history | `get_node_history` | `db_query.sh` + `get_node_status_history.sql` |
| Get recent jobs | `get_node_recent_jobs` | `db_query.sh` + `get_node_recent_jobs.sql` |
| Get validation job name | `get_validation_job` | `db_query.sh` (ad-hoc) |
| Get job details | `get_job_details` | `db_query.sh` + `get_job_details.sql` |
| Get job events | `get_job_events` | `db_query.sh` + `get_job_events.sql` |
| Check node SSH | `probe_ssh` | `node_check.sh <ip> <cmd>` |
| Check BMC | `bmc_query` | — (no script equivalent) |
| Check FabricManager | `check_fabricmanager` | `node_check.sh <ip> "systemctl status nvidia-fabricmanager"` |
| Check K8s pods | — | `kubectl_check.sh get/describe/logs` |
| Check job logs | — | `ltp.sh logs <user>~<job>` |
| Check job events | `get_job_events` | `ltp.sh events <user>~<job>` |
| Move node status (hardware/platform) | `execute_triage.py triage` | `move_node_status` (direct DB, use when script fails) |
| Trigger revalidation (→ validating) | `execute_triage.py validate` | — (**never use `move_node_status`** — no job submitted) |
| Delegate hardware nodes to repair | `delegate_to_agent` | — (one-call: login + session + task) |
| Check active repair tasks | `get_agent_active_tasks` | — (returns hostnames with active tasks, skip those) |
| Save evidence | `save_evidence_tool` (auto via MCP) | Manual `save_evidence_tool` for SSH excerpts |

---

## Core Design: Batch Processing

This workflow processes nodes in **batches of 10** to prevent context overflow.
Each batch goes through the full cycle independently.
Results are written to a persistent working file after each batch, so context can be released.

**Working file:** `/app/workspace/triage_nodes_working.md`
**Report file:** `/app/workspace/reports/triage_nodes_<YYYY-MM-DD>.md`

The working file is the source of truth for progress. If the session is interrupted or context
gets full, the next batch reads the working file to know what's already done.

---

## Workflow

### Phase 0: INIT

1. Determine which nodes to fetch based on the invocation:

| Invocation | Action |
|------------|--------|
| `triage unknown` or (none) | `get_nodes_by_status(status="triaged_unknown", include_alerts=True)` |
| `triage hardware` | `get_nodes_by_status(status="triaged_hardware", include_alerts=True)` |
| `triage node <hostname>` | `get_node_history(hostname)` + `get_node_detail(hostname)` — single node, any status |
| `triage recall <hostnames> --reason <REASON> --category hardware\|platform` | Bulk classify + delegate: skip investigation, just move status and delegate (see **Recall Mode** below) |

2. Exclude nodes specified via `--exclude` and any hostname containing `ctrl` or `storage`.

3. **Skip nodes that already have active repair tasks.** Call `get_agent_active_tasks(agent_id="repair")` — returns a list of hostnames that already have running/waiting_input repair tasks. Remove those from your to-triage list and log them as "skipped: already has active repair task". This prevents re-investigating nodes that are already being handled.

4. **Save `validating_timestamp` and `triaged_timestamp` for each node** — these define the triage window and are needed as `start_ts`/`end_ts` in tool calls.

4. For `triaged_hardware` nodes, also note the **existing reason** from `get_node_history` — this tells you what was previously identified.

5. Divide nodes into **batches of 10**. Order by hostname for determinism.

6. **Write initial working file** with the full node list, batch assignments, and an empty results section:

```markdown
# Triage Nodes — Working File

## Node Inventory
| # | Hostname | Status | IP | Validating TS | Triaged TS | Existing Reason | Alerts | Batch |
|---|----------|--------|----|--------------|------------|-----------------|--------|-------|
| 1 | node-001 | triaged_unknown | 192.0.2.x | ... | ... | — | NotifyUnvalidatedNodes(3) | 1 |
| 2 | node-002 | triaged_hardware | 192.0.2.x | ... | ... | GPUUnhealthy | GPUUnhealthy(1) | 1 |

## Batch Results
<!-- Filled in as batches are processed -->
```

---

### Phase 1: PROCESS BATCH (repeat for each batch)

#### Step 1: CATEGORIZE

**For `triaged_unknown` nodes:** Load `categorization-rules.md`. Match each node's `{alertname, summary}` pairs against the rules. Group nodes by issue type (A1–A5, B1–B5, C1–C4, D1, Z1, Z2). Unmatched nodes → "Uncategorized".

For validation failure nodes, also fetch full alert details:
```
get_node_alerts
```
Use `start_ts=validating_timestamp` and `end_ts=triaged_timestamp` for precise time range — NOT `hours` from NOW().

**For `triaged_hardware` nodes:** Categorization is already done. Review the existing reason from `get_node_history` — this tells you which branch to start at. No need to match alerts to rules.

#### Step 2: INVESTIGATE

Load `investigation-methodology.md`. Follow the decision tree for each issue group.

- **`triaged_unknown`**: The alerts tell you which branch to follow.
- **`triaged_hardware`**: The existing reason tells you which branch to follow. Your goal is to **verify the issue is still present and check for recovery**. The investigation tools and logic are the same.

**Key principles:**
- **Every issue type requires investigation** — even "known" issues. The alert/reason gives the category; investigation confirms it.
- **Never skip because a tool/token is missing.** Attempt it, report what couldn't be done, flag as incomplete.
- **Do NOT read source code or trace code bugs** — report findings, file bugs separately.

#### Step 3: DECIDE

Determine each node's action based on its current status and investigation findings:

**For `triaged_unknown` nodes:**

| Decision | Condition | Action |
|----------|-----------|--------|
| `triaged_hardware` | Concrete hardware evidence found | Transition → `triaged_hardware`, then delegate to repair |
| `triaged_platform` | Confirmed platform/service bug | Transition → `triaged_platform` |
| Stay `triaged_unknown` | No concrete root cause | Revalidation — transition → `validating` |

**For `triaged_hardware` nodes:**

| Decision | Condition | Action |
|----------|-----------|--------|
| Issue confirmed | Hardware issue still present | Delegate to repair agent (async) |
| Node recovered | All healthy, no errors | Transition → `validating` |
| Mis-triaged | Not actually hardware | Re-triage → `triaged_unknown` or `triaged_platform` |

**Decision rules:**
- `triaged_hardware` requires **concrete hardware evidence** (benchmark failure with values, GPU ECC count, IB port Down, Xid code, etc.)
- `triaged_platform` requires a **confirmed platform/service bug** (port conflict, pod crash, config issue, driver error)
- No concrete root cause → stay `triaged_unknown` + revalidation. **Never guess `triaged_hardware` without evidence.**
- Investigation changed the expected outcome? Use the evidence-based conclusion, not the rule's expected reason.

#### Step 4: BUILD ANNOTATIONS

**Build the summary annotation per node:**

| Field | Content |
|-------|---------|
| Reason | Triage reason label (e.g. `PCIeBandwidthDegradation`) |
| Summary | **Specific** findings with concrete values — benchmark names, numeric values, GPU indices, error codes |

Good example:
> Reason: `PCIeBandwidthDegradation`. Summary: gpu-copy-bw cpu_to_gpu4_by_dma baseline 55.25 actual 13.54 variance -75% on GPU PCI 0000:ab:00.0

Bad example (too generic):
> Reason: `PCIeBandwidthDegradation`. Summary: gpu-copy-bw underperformance

#### Step 5: WRITE BATCH RESULTS

**Append the batch results to the working file** immediately after completing the batch.

Format per batch:

```markdown
### Batch N (nodes X–Y)

#### Categorization
| Hostname | Status | Rule / Existing Reason | Key Alerts |

#### Investigation Findings
| Hostname | Evidence Collected | Key Findings |

#### Decisions
| Hostname | Current Status | Action | Reason | Annotation Summary |
```

After writing, **announce completion** and proceed to next batch.
If context is getting heavy, suggest starting a new session — the working file preserves all progress.

---

### Phase 2: REVIEW

After ALL batches are complete, read the full working file and verify:

- [ ] Every node from Phase 0 assigned to an issue — none missed
- [ ] Every node has a decided action (target status + reason or delegation)
- [ ] Every action has an annotation with **specific evidence**
- [ ] All issue types investigated — no "known issue, skipped"
- [ ] Incomplete investigations flagged to user
- [ ] No `triaged_hardware` decisions without concrete hardware evidence
- [ ] `triaged_hardware` nodes: issue verified as still present OR recovery confirmed
- [ ] Evidence gaps filled for auto-triaged nodes
- [ ] New rules added ONLY for confirmed root causes

If any item fails → go back and resolve the specific nodes.

If all pass → produce final report at `reports/triage_nodes_<YYYY-MM-DD>.md`:
- **Part 1 — Issues → Nodes**
- **Part 2 — Issues → Actions**
- **Part 3 — Summary table**

Present to user.

- **`--execute` mode**: Skip approval. Execute ALL actions for ALL nodes directly after presenting the report.
- **`--dry-run` or no flag mode**: Present the report, then **wait for user approval** before executing any actions. One approval gates all nodes.

---

### Phase 3: EXECUTE

1. Present the report file to user
2. **If `--execute` flag was given**: proceed immediately. **Otherwise**: wait for one explicit user approval — once confirmed, execute ALL actions for ALL nodes without asking again

3. **For each node, execute the action chain automatically:**

   **For `triaged_hardware` or `triaged_platform` nodes (already in correct status):**
   1. Delegate: `delegate_to_agent(agent_id="repair", prompt="/repair-nodes --hostname HOSTNAME --category hardware|platform", title="Hardware|Platform: HOSTNAME - REASON", completion_mode="manual")`
   2. Log delegation (session ID + task ID) in working file

   **For `triaged_unknown` nodes that need reclassification:**
   1. Move status first: `execute_triage.py triage` (or `move_node_status` fallback) → `triaged_hardware` or `triaged_platform`
   2. Then delegate: `delegate_to_agent(agent_id="repair", prompt="/repair-nodes --hostname HOSTNAME --category hardware|platform", title="Hardware|Platform: HOSTNAME - REASON", completion_mode="manual")`
   3. Log both actions in working file

   **For nodes that need revalidation (→ `validating`):**
   1. Submit revalidation: `execute_triage.py validate` (NEVER `move_node_status` — it doesn't submit a validation job)
   2. Log action in working file
   3. Revalidation is NOT delegated — the platform handles it automatically

   **Status transition details:**
   - `from_status` is auto-corrected from DB — pass the expected status, the tool will use the actual current status
   - `detail` should contain the evidence summary (benchmark values, GPU indices, etc.)
   - After each transition, verify: `get_node_history` should show the new action

   **Revalidation MUST use `execute_triage.py validate`** — NOT `move_node_status` and NOT `execute_triage.py triage`.
   `execute_triage.py validate` fires an `admin-validate-node` alert → alert-handler → node-recycler → superbench job submitted → status set to `validating`.

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/sub-skills/execute-triage/execute_triage.py --dry-run validate \
       --nodes <hostname1>,<hostname2>,...
   ```

   **Delegation details:**
   - `--category hardware` → repair agent collects evidence → drafts RMA ticket → asks for approval at ticket stage
   - `--category platform` → repair agent diagnoses → fixes → verifies → sends to revalidation (executes directly)
   - After delegation, log the session ID + task ID in working file
   - Do NOT wait for completion — the repair agent runs async
   - The repair agent can access triage evidence via `get_node_evidence_tool(hostname)`

4. **Post-revalidation verification** — for nodes sent for revalidation:
   - Wait for validation job to complete (`validating` → `cordoned` or `available`)
   - If job failed: `get_job_details` → check `task_completion_phrase`
   - Known hardware phrases → re-triage as `triaged_hardware`:
     - `ContainerMayFailDueToGpuDeviceEccError` → GPU ECC error
     - `ContainerUnrecognizedFailed` with empty `task_node` → node unreachable
   - If validation passed → `available`, done

5. **Append "Part 4 — Execution Log"** to the same report: timestamp, actions taken, delegations, verification results

---

## Rules

- **Evidence before decisions.** No hardware triage without hardware evidence.
- **Never skip investigations.** Attempt everything, report what's incomplete.
- **Never trace source code.** Report findings, file bugs separately.
- **MCP tools auto-save evidence.** Manual `save_evidence_tool` only for ad-hoc SSH output — curate useful excerpts, add meaningful summary.
- **Write batch results to working file IMMEDIATELY** after each batch completes. Never hold findings in context across batches.
- **Large outputs → save to temp file, parse, write summary to working file.** Don't overflow context with raw data.
- **If context is getting heavy, say so.** The working file preserves progress — a new session can pick up from the next batch.
- **`execute_triage.py` is the primary write path.** `move_node_status` is fallback when the script is unavailable.
- **`--execute` = auto-execute.** No approval gate — present the report, then execute the full action chain per node (move status → delegate).
- **No flag / `--dry-run` = approval gate.** Present the report, wait for user to confirm, then execute. One approval gates all nodes.
- **Do not rely on argument shapes written in skill docs.** Use MCP tool schemas at runtime for exact parameters.
- **`triaged_hardware` nodes are NOT confirmed just because they're in that status.** Always verify the issue is still present before delegating to repair. Auto-triage by Alert Manager may have been wrong or the issue may have self-recovered.
- **Delegate confirmed hardware/platform nodes to repair agent async.** Call `delegate_to_agent(agent_id="repair", prompt="...", title="Hardware: ...", completion_mode="manual")` or `title="Platform: ..."`. Always include `--category hardware` or `--category platform` in the prompt so the repair agent knows which flow to follow. **Always use `completion_mode="manual"`** — hardware repair requires user approval before destructive actions (reset, RMA); the task must stay visible in Chat UI until the user dismisses it.
- **`--execute` vs approval gate.** With `--execute`, execute the full action chain per node (move status → delegate) without asking. Without it, wait for user approval first.

---

## Recall Mode

When you already know the hardware reason for a batch of nodes (e.g., vendor recall, known batch defect), use **recall mode** to skip investigation and go straight to classify + delegate.

**Invocation:**
```
triage recall h200-001,h200-002,h200-003 --reason PowerSupplyRecall --category hardware
```

**What it does (no investigation, no batching):**
1. Parse the comma-separated hostnames
2. Call `get_agent_active_tasks(agent_id="repair")` — skip any hostnames that already have active repair tasks
3. For each remaining hostname:
   - `execute_triage.py triage --nodes HOSTNAME --status triaged_hardware --reason REASON --summary "Recall: REASON. Triage agent batch-classified."`
   - `delegate_to_agent(agent_id="repair", prompt="/repair-nodes --hostname HOSTNAME --category hardware", title="Hardware: HOSTNAME - REASON", completion_mode="manual")`
4. Log all actions (transitions + delegations) and present a summary

**Key differences from normal triage:**
- **No Phase 1 (categorize) or Phase 2 (investigate)** — the reason is given by the user
- **No batch processing** — all nodes processed in one pass (investigation is the expensive part; classification + delegation is fast)
- **No evidence collection** — the user already knows the reason; the repair agent will do its own investigation
- **Still respects dedup** — skips nodes with active repair tasks
- **Still uses `completion_mode="manual"`** for delegation

**Applicable categories:** `hardware` or `platform`. The `--reason` should be a PascalCase reason matching the categorization-rules.md list (e.g., `PowerSupplyRecall`, `GPUOvertemp`, `NVLinkDegraded`).
