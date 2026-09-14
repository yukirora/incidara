---
name: node-reallocation
description: "Reallocate ready_ua nodes: reset, config, sysinfo, clone, allocate. Auto-trigger for 'reallocate', 'reallocation', 'reallocate nodes'."
---

# Node Reallocation

Reallocate `ready_ua` nodes: reset, full config, sysinfo collection, onboard clone, allocate.

## When to Use

- Scheduled run (cron: every 15 min)
- Manual: "reallocate nodes" or "reallocation"
- One-off: "reallocate h200-XXX" (calls `reallocate_node()` directly, no delegation)

## Architecture

**Orchestrator mode**: Fetch `ready_ua` nodes → delegate each to the recycler agent as a
separate Chat UI task. Each node gets its own session, running `reallocate_node()` independently.
This makes every node visible and trackable in Chat UI, and a failure on one node doesn't
block the others.

**One-off mode**: For a single node, call `reallocate_node(hostname)` directly — no delegation needed.

---

## Workflow (Orchestrator Mode)

### Phase 1: FETCH

Call `get_nodes_by_status("ready_ua")` → get list of hostnames.

**Human review required for ctrl/master and storage nodes** — they are dangerous to reallocate automatically (K8s control plane, managed separately). For any hostname matching:
- `ctrl-` or `master-` prefix (K8s control plane nodes)
- `storage-` prefix (storage nodes managed separately)

List these separately and **ask the user for explicit approval** before delegating them. Do NOT delegate these nodes without confirmation.

If none remain after filtering → report "No ready_ua nodes to reallocate" and stop.

### Phase 2: DELEGATE

For each node in the list:

1. **Dedup check**: Call `get_agent_active_tasks(agent_id="recycler")` — if a task
   with this hostname already exists (active: running/waiting_input/busy), skip it.

2. **Delegate**: Call `delegate_to_agent(
     agent_id="recycler",
     prompt="/node-reallocation --hostname HOSTNAME",
     title="Reallocate: HOSTNAME",
     completion_mode="manual"
   )`

   - `completion_mode="manual"` — task stays visible in Chat UI after completion so user can review the summary.

3. **Log**: Record hostname + task_id + session_id

### Phase 3: REPORT

After all delegations:

```
# Reallocation Delegation Report

## Summary
- ready_ua nodes found: N
- Excluded (ctrl/master/storage): N
- Newly delegated: N
- Skipped (existing active task): N

## Delegated
| Hostname | Task ID | Session ID |
|----------|---------|------------|

## Skipped (already active)
| Hostname | Existing Task ID |
|----------|-----------------|

## Excluded (ctrl/master/storage — not approved)
| Hostname | Reason |
|----------|--------|

**⚠️ Ctrl/master/storage nodes require human review before reallocation.**
The following nodes were found in ready_ua but were NOT delegated:
| Hostname | Type | Reason |
|----------|------|--------|
To approve reallocation for any of these, say: "reallocate <hostname>"
```

No working file needed — Chat UI is the source of truth for task state.

---

## One-off: Reallocate a Single Node

If the user says "reallocate h200-XXX" or passes `--hostname`:

1. Call `get_node_detail(hostname)` to show node info (IP, category, current status)
2. Call `reallocate_node(hostname="HOSTNAME")` directly — no approval needed
3. Present summary of results after reallocation completes

This is also what each delegated task does — the prompt `/node-reallocation --hostname HOSTNAME`
tells the recycler to show info and run the pipeline.

---

## What `reallocate_node()` Does

Each delegated task runs this ~20-30 min pipeline:

1. Get node detail (IP, category)
2. Probe SSH — BMC power cycle + reset if unreachable
3. Run full config (7 stages + k8s)
4. Collect sysinfo (sbsysinfo, SKU, serial)
5. Clone onboard record + move to `allocated_ua`
6. On failure: move to `triaged_unknown`
