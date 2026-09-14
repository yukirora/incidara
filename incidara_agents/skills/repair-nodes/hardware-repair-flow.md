# Hardware Repair Flow

The linear execution pipeline from `triaged_hardware` → `deallocated_ua` → `ua`.
This flow is the same regardless of fault type — only the ticket content differs.

**All node categories** — process all node types (h200, b300, cpu, storage, ctrl). GPU-specific investigation (ECC, NVLink, FM) only applies to h200/b300; non-GPU nodes skip those steps.

For **what evidence to collect per fault type**, see `investigation-methodology.md`.
For **ticket wording and quality gate**, see `repair-ticket-summary-methodology` skill.
For **workflow orchestration** (scope, discover, report), see `SKILL.md`.

---

## Pipeline Overview

```
triaged_hardware
  │
  ├─ Step 1: Collect evidence (investigation-methodology.md)
  ├─ Step 2: Pass evidence completeness gate (repair-ticket-summary-methodology)
  ├─ Step 3: Generate ticket content (repair-ticket-summary-methodology)
  ├─ Step 4: Build ticket description
  ├─ Step 5: Manual approval checkpoint ← ALWAYS pause here
  ├─ Step 6: Write pre-submit report
  │
  │  ── After explicit user approval only ──
  │
  ├─ Step 7: move_node_status → deallocated_ua
  ├─ Step 8: reset_node
  ├─ Step 9: submit_rma_ticket
  └─ Step 10: Confirm deallocated_ua → ua
```

---

## Step 1: Collect Evidence

Follow `investigation-methodology.md` for the relevant fault branch.

The procedure is always:
1. Check existing saved evidence → `get_node_evidence_tool`
2. Check platform data → `get_node_alerts`, `get_node_history`, `get_node_detail`
3. Re-run live diagnostics → `probe_ssh` (always mandatory)
4. Fill gaps by fault type → follow per-fault-type guide
5. Save ad-hoc SSH excerpts → `save_evidence_tool`

---

## Step 2: Evidence Completeness Gate

**Read `${CLAUDE_SKILL_DIR}/repair-ticket-summary-methodology.md` now** — this is the canonical
reference for the quality gate and all ticket wording rules. You must read it before proceeding.

Call `get_node_evidence_tool(hostname)` to list all collected evidence.
Apply the completeness gate defined in `repair-ticket-summary-methodology.md`:

**Mandatory for ALL tickets:**
- [ ] FaultCode identified
- [ ] SSH reachability confirmed (or explicitly unreachable with details)
- [ ] Node category known (b300, h200, cpu, etc.)
- [ ] Triage reason known (tells which fault branch)

**Mandatory per fault type** — see the full checklist in `repair-ticket-summary-methodology.md`
(section: "Mandatory by fault type").

If blocked: go back to Step 1 and collect missing evidence. **Do NOT submit incomplete tickets.**

Exception: if SSH is unreachable and the unreachability IS the finding, document it and proceed with a conservative ticket + caveat in `internal_notes`.

---

## Step 3: Generate Ticket Content

**`repair-ticket-summary-methodology.md` must already be loaded from Step 2.** Use it now as
the canonical reference for all of the following:

1. **Evidence-to-wording mapping** — apply the Canonical Mapping section (FaultCode + reason → Chinese summary/reproducer)
2. **Vendor-safe translation** — keep all error messages and numeric values; strip workload identity and all Kubernetes/kubectl/kubelet/K8s orchestration terminology per the "What to STRIP" table
3. **b300 firmware addenda** — append by default for b300 nodes (see "b300 Handling" section)

Inputs:
- FaultCode from `get_node_detail`
- Evidence collected in Step 1
- Action history from `get_node_history`
- Node category

Outputs:
- `summary` (Chinese, vendor-facing)
- `reproducer` (Chinese, vendor-facing)
- `internal_notes` (optional; not submitted unless user asks)
- `gate_status`: `pass` or `blocked` with missing items

**Ticket content boundary:**
- Vendor-facing text: hardware fault symptoms, impact, reproducibility
- Include real error messages, log excerpts, concrete numeric values
- Strip only workload identity (job names, model names, user names, platform names)
- Internal runbook checks → keep in `internal_notes`, not in vendor ticket by default

---

## Step 4: Build Ticket Description

Format the Chinese ticket description:

```
【序列号】
{sn}
【BMC IP】
{mgmt_ip}
【SSH IP】
{ip}
【hostname】
{hostname}
【问题描述】
{summary}
【复现方式】
{reproducer}
```

`sn`, `mgmt_ip`, `ip` come from `get_node_detail`.
`summary` and `reproducer` come from Step 3.

---

## Step 5: Present Draft Ticket for User Approval

**ALWAYS pause here.** Present the full ticket package to the user for review:

- hostname
- serial number
- current status (`triaged_hardware`)
- planned transition sequence (`triaged_hardware` → `deallocated_ua` → `ua`)
- generated Chinese `summary` (vendor-facing)
- generated Chinese `reproducer` (vendor-facing)
- full vendor-facing ticket description body
- separate internal operator notes (not submitted unless user asks)

**Do NOT proceed until the user explicitly approves.**
- Do NOT move the node to `deallocated_ua` before approval.
- Do NOT reset the node before approval.
- Do NOT call `submit_rma_ticket` before approval.

**If permission is denied, STOP.** When the user clicks "deny" on a `run_kubectl_dangerous`, `submit_triage_alert`, `submit_rma_ticket`, or `reset_node` tool call, the action is explicitly rejected. Do NOT retry or find alternative ways to achieve the same outcome. Report "Action denied by user" and stop investigating this node.

---

## Step 6: Write Pre-Submit Report

After preparation is complete (and before any state change), write the prepared ticket package to the report file.

The report must include:
- Node identity
- Evidence summary
- Vendor-facing summary/reproducer
- Full vendor-facing ticket body
- Internal notes
- Planned execution sequence

Report path: `reports/repair_nodes_<YYYY-MM-DD>.md`

---

## Step 7: Enter Repair Queue (`triaged_hardware` → `deallocated_ua`)

After explicit approval, call `move_node_status`:
- `from_status` = `triaged_hardware`
- `to_status` = `deallocated_ua`
- `reason` = `InitiateRMA`
- `detail` = JSON with the vendor-facing ticket content:

```json
{
  "summary": "1. GPU ECC Error\n2. 请帮助确认本台机器是否已经更新用于解决 Phison 和 HGX 上的 CX8 通讯问题的固件...\n3. 请帮助确认本台机器是否已经更新用于解决 B300 Xid 194 问题的固件。",
  "reproducer": "1. nvidia-smi\n2. 请帮助确认固件是否已经升级，如未升级，请按照厂商提供的操作说明操作。\n3. 请帮助确认固件是否已经升级，如未升级，请按照厂商提供的操作说明操作。"
}
```

This is the same `summary` and `reproducer` from Step 3/4 — the Chinese vendor-facing text.

---

## Step 8: Reset Node

Call `reset_node`. This tool automatically handles both cases:

**SSH reachable:** Runs teardown → create_debug_user → clear_node (which sets BMC password on-box).

**SSH unreachable:** Resets BMC user password remotely to the vendor-access password (`RESET_BMC_PASSWORD`) via `set_bmc_password_remote.sh`, then skips on-box reset. The vendor needs BMC credentials to diagnose and repair the node when they receive it. Provide `mgmt_ips` and `category` so the tool knows which BMC IPs to target and which BMC user to set (`admin` for b300, `root` for h200).

If reset fails partway through, log the error but continue to ticket submission —
the node will be physically handled by the repair vendor regardless.

---

## Step 9: Submit RMA Ticket

Call `submit_rma_ticket` with the node identity and the generated ticket content from Step 4.

Record the returned `ticket_id`.

`submit_rma_ticket` is expected to persist the subsequent `deallocated_ua → ua` transition together with `InitiateRMA` ticket metadata.

---

## Step 10: Confirm Post-Submit Transition (`deallocated_ua` → `ua`)

Ensure the transition from `deallocated_ua` to `ua` is persisted:
- If `submit_rma_ticket` already persisted it, verify and report it.
- If not, persist explicitly via `move_node_status` with `ticket_id` in the detail.

The repair agent does NOT run the full config pipeline, check ticket completion, or clone onboard records. That is the node-recycler's job (ua → ready_ua → allocated_ua).

---

## Execution Model

The hardware repair flow always follows this pattern:

1. **Steps 1–6**: Collect evidence, generate draft ticket, present to user
2. **Step 5**: Wait for explicit user approval of the draft ticket
3. **Steps 7–10**: After approval, execute (move status, reset, submit RMA)

There is no `--execute` flag. The approval gate is always active — the agent never proceeds to
state changes without user confirmation. This ensures the user reviews the vendor-facing ticket
wording before any irreversible action.
