# Collect RMA Cases — Populate case_memory

Collect completed RMAs, classify with LLM, human reviews, then insert into case_memory.
Runs BEFORE analyze-rma-cases.

## When to Use

- Before running analyze-rma-cases (weekly or on-demand)
- Retry: re-run anytime — dedup skips already-inserted tickets

## MCP Tools Used

| Tool | Purpose |
|---|---|
| `query_completed_rmas` | Returns ALL raw data: vendor repair text, our classification/reason, alert types, SKU, claude_session_id. Dedup built-in (skip_existing=True). |
| `insert_case` | Write to case_memory |

**Do NOT call `get_node_history`, `get_node_alerts`, `get_node_detail`** — that data is already in the `query_completed_rmas` result. Those tools return 10-20KB per node and waste tokens.

## Pipeline

### Step 1: SCAN — One query gets everything

Call `query_completed_rmas(days=30)`.

**Dedup is built-in**: `skip_existing=True` (default) excludes ticket_ids already in case_memory. Only new RMAs are returned — no per-insert dedup needed.

Returns per RMA:
- **Vendor**: `repair_status`, `remark`, `vendor_ops`, `vendor_solutions`, `replace_sn`
- **Ours**: `our_action`, `our_reason`, `our_detail`
- **Context**: `alert_types`, `sku`, `hostname`, `onboard_id`, `ticket_id`, `rma_completed_at`

**Pre-filter before classification:**
- Skip rows where `our_reason` is null (no triage record — nothing to classify)
- **Do NOT skip rows where `vendor_ops` is null** — `已拒绝`/`已撤回` cases have null vendor_ops because the vendor did nothing, and that IS the verdict signal
- For rows with null `vendor_ops` AND `repair_status` not in `已拒绝`/`已撤回`: skip (truly no vendor data)

### Step 2: REASON — LLM classifies each case

For each RMA, reason from the raw data (NO additional tool calls):

**Fast-path for `已拒绝`/`已撤回`**: When `repair_status` is `已拒绝` or `已撤回`, the verdict is always `NO_FAULT_FOUND` with `vendor_answer_quality=NONE`. Set `vendor_repair_type=other`, `vendor_component=N/A`. Skip remaining steps — no vendor_ops to classify.

For `已完成` cases:

1. **Classify vendor repair** from `vendor_ops` + `vendor_solutions` + `remark`:
   - `vendor_repair_type`: gpu_replace, motherboard_replace, disk_replace, dimm_replace, cable_replace, chassis_replace, reseat, os_reinstall, config_change, other
   - `vendor_component`: GPU, Motherboard, NVMe, DIMM, IB_Cable, IB_NIC, NVSwitch, Chassis, N/A

2. **Determine verdict** by comparing `our_reason` vs vendor repair:
   - `REPAIR_CONFIRMED`: vendor replaced a hardware component AND the component matches the subsystem implied by our_reason
   - `MISCLASSIFIED`: we said X fault but vendor repaired a different component
   - `MAINTENANCE_FIX`: vendor performed a physical action that fixed the node but replaced no component (clean port, reseat, airflow, OS reinstall)
   - `NO_FAULT_FOUND`: vendor tested and found nothing wrong — node was healthy on arrival, likely self-recovered
   - `CONFIG_TASK`: vendor did config change or upgrade (BIOS, memory upgrade, etc.)

   **`repair_status` overrides**: The `repair_status` field is a strong signal:
   - `已拒绝` (rejected) → **NO_FAULT_FOUND** — vendor explicitly rejected the ticket, meaning they found no fault. This is stronger than a generic NFF because the vendor actively disputed our claim.
   - `已撤回` (withdrawn) → **NO_FAULT_FOUND** — we withdrew the ticket, implying the issue self-resolved or was misdiagnosed.
   - `已完成` (completed) → proceed with normal verdict logic above.
   - Empty/null → proceed with normal verdict logic above.

   **Note**: `VENDOR_MISS` is NOT set at collection time — it can only be determined retroactively when a node recurs after a `NO_FAULT_FOUND`. Use `vendor_answer_quality=MINIMAL/NONE` to flag suspicious vendor responses instead.

   **Key distinction**: `MAINTENANCE_FIX` vs `NO_FAULT_FOUND`:
   - "Cleaned switch port, recovered" → MAINTENANCE_FIX (vendor did something)
   - "NIC normal, monitored 10 min, no flap" → NO_FAULT_FOUND (vendor did nothing, node was fine)
   - "OS reinstalled, recovered" → MAINTENANCE_FIX (vendor did something)
   - "Adjusted airflow, temps normalized" → MAINTENANCE_FIX (vendor did something)

3. **Re-review all REPAIR_CONFIRMED** — for each case, explicitly check: does `vendor_component` match the subsystem implied by `our_reason`? If NOT, change to `MISCLASSIFIED`. Examples:
   - `our_reason=NodeCrash` + `vendor_component=IB_NIC` → **MISCLASSIFIED** (we said generic crash, actual cause was IB)
   - `our_reason=NVLinkFailure` + `vendor_component=IB_NIC` or `IB_Cable` → **MISCLASSIFIED** (NCCL nvlink-sharp fails from IB issues too)
   - `our_reason=IBLinkFlapping` + `vendor_component=GPU` → **MISCLASSIFIED** (GPU caused IB symptoms)
   - `our_reason=NVLinkFailure` + `vendor_component=GPU` → **REPAIR_CONFIRMED** (NVLink is GPU interconnect, GPU replacement fixes NVLink)
   - `our_reason=NodeCrash` + `vendor_component=Motherboard` → **REPAIR_CONFIRMED** (crash is consistent with motherboard fault)

   **This step is mandatory.** Do NOT skip it. Many cases initially look like REPAIR_CONFIRMED but are actually MISCLASSIFIED when you compare the subsystem.

4. **Assign confidence** (`vendor_confidence` — confidence in OUR classification):
   - `HIGH`: specific component replaced
   - `MEDIUM`: repair described, component identifiable
   - `LOW`: reseat/reinstall/no fault found/ambiguous

4. **Assess vendor answer quality** (`vendor_answer_quality` — how thorough the vendor response is):
   - `DETAILED`: swap test with serial numbers, root cause identified, clear evidence trail
   - `MODERATE`: physical action taken with some reasoning (e.g., "cleaned switch port, recovered")
   - `MINIMAL`: generic check, no evidence of depth (e.g., "NIC normal, monitored 10 min")
   - `NONE`: empty or boilerplate response (e.g., "检查正常", blank)

   **Tip**: `NO_FAULT_FOUND` + `vendor_answer_quality=DETAILED` = genuine self-recovery (trust it).
   `NO_FAULT_FOUND` + `vendor_answer_quality=MINIMAL` = suspicious, likely `VENDOR_MISS`.

5. **Build `our_evidence`**: `{"alert_types": <from query>, "sku": <from query>}`

Write all decisions to `/app/workspace/reports/collect-review-<date>.json`.

### Step 3: REVIEW — Human reviews LLM decisions

Present the review **grouped by verdict**, with full vendor repair detail. Ask user to approve or correct:

```
### REPAIR_CONFIRMED
| # | Hostname | Our Reason | Vendor Repair (raw) | Vendor Component | Conf | Quality |
|---|----------|------------|---------------------|------------------|------|---------|
| 1 | h200-000705 | NodeCrash | op: 更换主板, solution: 更换备机主板后验证通过 | Motherboard | HIGH | DETAILED |
| 2 | h200-000436 | IBLinkFlapping | op: 清洁交换机端口, solution: 清洁交换机端口后IB恢复正常 | IB_Cable | MEDIUM | MODERATE |

### MISCLASSIFIED
| # | Hostname | Our Reason | Vendor Repair (raw) | Vendor Component | Conf | Quality |
|---|----------|------------|---------------------|------------------|------|---------|
| 3 | h200-000142 | NodeCrash | op: 更换IB网卡模块, solution: 更换IB NIC模块后链路恢复 | IB_NIC | HIGH | DETAILED |

### MAINTENANCE_FIX
| # | Hostname | Our Reason | Vendor Repair (raw) | Vendor Component | Conf | Quality |
|---|----------|------------|---------------------|------------------|------|---------|
| 4 | h200-000232 | NodeCrash | op: 调整风道, solution: 调整风道后温度恢复正常 | N/A | LOW | MODERATE |

### NO_FAULT_FOUND
| # | Hostname | Our Reason | Vendor Repair (raw) | Vendor Component | Conf | Quality |
|---|----------|------------|---------------------|------------------|------|---------|
| 5 | h200-000265 | NodeCrash | op: NIC检查正常, solution: 网卡检查正常，监控30分钟无异常 | N/A | LOW | MINIMAL |

### CONFIG_TASK
...
```

**Rules for the table:**
- **Group by verdict** (REPAIR_CONFIRMED, MISCLASSIFIED, MAINTENANCE_FIX, NO_FAULT_FOUND, CONFIG_TASK)
- **Vendor Repair** column shows the **raw vendor message** from `vendor_ops` + `vendor_solutions` — do NOT summarize or abbreviate. The human reviewer needs to see exactly what the vendor wrote.
- **Our Reason** is the `our_reason` field (e.g., `NodeCrash`, `IBLinkFlapping`, `NVLinkFailure`), not the action path.

Wait for user approval before proceeding. User may:
- Approve all → proceed to Step 4
- Correct specific rows → update the working file, then proceed
- Skip rows → remove from working file

**CRITICAL: This is a HARD STOP.** Do NOT proceed to Step 4 until the user explicitly says "approve", "approved", "proceed", "insert", "go ahead", or similar. If the user only sends corrections, apply them, update the table, and **present the updated table again** — then stop and wait for explicit approval. Corrections are NOT approval.

**With `--execute` flag**: skip this review step, auto-proceed to Step 4.

### Step 4: MINE + INSERT — One case at a time

For each approved case with an `our_reason` (skip if null):

**IMPORTANT: After each insert, remove that case from your working list.** Do NOT re-insert cases you have already inserted. The agent tends to re-include already-inserted cases in subsequent tool call batches — this is the #1 cause of duplicate inserts. Track which `rma_ticket_id`s you have already inserted and skip them on subsequent calls.

`insert_case` is idempotent (ON CONFLICT upsert on hostname+rma_ticket_id), so accidental re-inserts won't create duplicates — but they waste API calls and tokens.

1. **Insert case**: call `insert_case()` with all fields. Pass the **full `sessions` array** from `query_completed_rmas` as `claude_session_id` (as a JSON string). Do NOT extract a single session ID.

| Field | Source |
|---|---|
| `hostname` | query result |
| `our_classification` | `our_action` + `our_reason` combined |
| `our_reason` | `our_detail` (investigation summary) |
| `our_evidence` | `{"alert_types": [...], "sku": "..."}` |
| `vendor_verdict` | reviewed in Step 2 |
| `vendor_confidence` | reviewed in Step 3 |
| `vendor_answer_quality` | reviewed in Step 4 |
| `vendor_repair_raw` | concatenated vendor text |
| `vendor_repair_type` | reviewed in Step 3 |
| `vendor_component` | reviewed in Step 3 |
| `rma_ticket_id` | `ticket_id` from query |
| `onboard_id` | `onboard_id` from query |
| `rma_completed_at` | `rma_completed_at` from query |
| `claude_session_id` | JSON string of the `sessions` array from query result (contains both repair and triage sessions) |

2. **Record finding verdict**: After each `insert_case`, call `record_finding_verdict_from_case` to backfill the verdict on any unjudged patrol findings for this hostname. This closes the feedback loop — detection rules learn from RMA outcomes.

   The tool automatically:
   - Finds unjudged `patrol_findings` for this hostname (before RMA date)
   - Maps `vendor_verdict` to finding verdict: `REPAIR_CONFIRMED`/`MAINTENANCE_FIX`/`CONFIG_TASK` → `confirmed`, `MISCLASSIFIED` → `rejected`, `NO_FAULT_FOUND` + reliable vendor → `rejected`
   - Skips `NO_FAULT_FOUND` with unreliable vendor (can't trust the NFF)
   - Creates `rma_finding_reconciliations` linking case ↔ finding

   If the tool returns no matches, that's fine — not all cases have corresponding patrol findings.

Transcript mining is done in the **analyze-rma-cases** skill, not here. This skill only inserts the raw case data.

Log progress after every 10 cases.

## Output

```markdown
## Collect Complete
- RMAs scanned: 50
- Skipped (null vendor_ops): 4
- LLM classified: 46
- Human reviewed & approved: 46
- Inserted: 43
- Skipped (duplicates): 0 (dedup at query time)
- Skipped (no triage record): 2
- Transcript mined: 38 (5 had no session found)
```
