# Analyze RMA Cases

Analyze completed RMA cases in case_memory to find misclassification patterns, investigation gaps, and systemic issues. Produce actionable proposals for human review.

**Prerequisite:** `collect-rma-cases` skill must have been run first to populate case_memory.

## When to Use

- After `collect-rma-cases` has populated case_memory with recent RMAs
- Weekly scheduled review
- Manual: "analyze vendor feedback"

## Architecture

Problem-driven workflow: stats → patterns → synthesize problems → prioritize → delegate. Show findings in chat after each step.

## Workflow

### Step 1: Get Stats

### Step 0: Check Monitoring Problems

Before running the full pipeline, check if any `monitoring` problems are ready to evaluate.

Read `sub-skills/check-monitoring/SKILL.md` and execute it.

This step resolves or escalates monitoring problems before we invest time in re-analyzing the same issues.

### Step 1: Get Stats

Show your answers in chat after this step.

Two MCP calls:

1. **`get_rule_stats()`** — per fault_type stats: total, correct, wrong, accuracy_pct, misclassified, nff_reliable, nff_unreliable, maintenance_fix, config_task. RecallForUpgrade and test excluded.
2. **`get_misclass_paths()`** — misclassification paths: fault_type → vendor_component with count and hostnames

**Answer these 4 questions:**

1. **Which fault types have wrong cases?** — any fault type with misclassified > 0 OR nff > 0
2. **HOW are they wrong?** — misclassified (wrong component → see misclass_paths), NFF (agent couldn't prove the fault)
3. **How bad is each problem?** — volume × wrongness. 22 cases at 27% > 2 cases at 50%
4. **Can we trust the vendor data?** — You can't determine this without investigation. Node recurrence after NFF might be a different fault. Vendor verbosity doesn't indicate correctness. The only way to know is to investigate each case — check the transcript to see what evidence the agent gathered and whether it was sufficient.

### Step 1.5: Fetch All Wrong Cases

**Core principle: Every individual NFF or MISCLASSIFIED case needs investigation.** Not just fault types with bad accuracy — even a fault type at 87.5% accuracy with 1 NFF case has a gap that must be investigated.

Fetch cases for every fault type that has **any** wrong cases (nff_reliable > 0 OR nff_unreliable > 0 OR misclassified > 0 in `get_rule_stats()`). Call **`query_similar_cases(classification="<fault_type>", limit=50)`** per fault type.

Do NOT call `query_similar_cases(classification="")` — that returns all cases at once (150K+ characters, exceeds token limits). Fetch only what you need.

Do NOT skip fault types with low volume or high accuracy. Even 1 NFF case on a 90%+ accuracy fault type = that specific case needs investigation.

### Step 2: Detect Patterns

- **Pattern A: Misclassification paths** — from `get_misclass_paths()`. ≥2 same path → systematic gap; 1 case → propose only if missed check is clear
- **Pattern B: Rack clusters** — extract rack from hostname `lg-cmc-<rack>-<pos>-<sku>-<sn>`, group cases by rack. Only flag if same rack has ≥3 cases with the SAME fault type or SAME vendor component — that may indicate shared infrastructure. A rack with mixed faults at high volume is just fleet distribution, not a pattern.
- **Pattern C: SKU clusters** — ≥3 same SKU + same vendor_component, ≤60 days. Skip if single-SKU dominated fleet
- **Pattern D: NFF cases** — **Every NFF case needs investigation, no exceptions.** Do NOT skip NFF unreliable cases — vendor quality only affects what you conclude AFTER investigating the transcript, not whether you investigate. If the agent submitted FaultCode JSON with no investigation → clear gap regardless of vendor quality. If the agent investigated thoroughly but vendor still said NFF → flag for recurrence monitoring. **Also check `repair_status`**: `已拒绝` (rejected) is the strongest NFF signal — vendor actively disputed our claim; `已撤回` (withdrawn) means we pulled the ticket, implying misdiagnosis.
- **Pattern E: Repeat offenders** — Call **`get_repeat_offenders(min_cases=2)`** to systematically find ALL hostnames with ≥2 cases. Cross-fault-type repeat offenders are especially important: if a node had NodeCrash→MAINT_FIX, then NvidiaSmiDoubleEccError→NFF, that's likely the same underlying hardware fault manifesting differently. Don't just look at per-fault-type stats — a fault type with 87.5% accuracy might hide a cross-type repeat pattern. For each repeat offender, call `get_cases_by_hostname` to get the full history and check if existing problems already cover it.
- **Pattern F: Switch-level issues** — some faults are at the switch/fabric layer (NVSwitch, IB switch), not at endpoint components (individual GPUs, NICs). Signal: alert evidence includes NVLinkFailure, NVSwitch errors, or IB fabric errors, but vendor checklist only checks endpoints. These cases need different investigation and ticket structure — explicitly call out "switch-level / 交换层" in the ticket.

**Output:** List of patterns with type, description, affected cases (hostname + case_id), significance. Show in chat.

### Step 3: Synthesize Problems

**⚠️ Before synthesizing, call `get_problems()` to get ALL existing problems.** You must check dedup DURING synthesis, not after. Every problem you identify must be checked against existing problems right away — otherwise you'll mark things as "NEW" that already exist.

Combine Step 1 (which fault types are wrong, how bad, can we trust the data) with Step 2 (patterns) into a list of **problems**. Each problem is a group of related cases, NOT one case.

For each problem, answer: **What can we learn from this pattern to answer the Step 1 questions?**

**Format:**

| # | Problem | Fault Type | Cases | Existing Problem? (dedup check) | Is This a Real Problem? | Why / Why Not | Next Step |
|---|---------|-----------|-------|-------------------------------|------------------------|---------------|-----------|
| 1 | NodeCrash NFF — 15/22 cases returned as NFF (5 reliable, 10 unreliable). Agent couldn't prove hardware fault to vendor, or vendor missed it. Investigation status unknown. | NodeCrash | IDs: 218, 214, 212, 211, 200, 219, 216, 215, 207, 201, 199, 196, 190, 136, 134 | "HOW are they wrong?" — NFF dominates | YES — 15 NFF cases is a systematic pattern | Same root cause likely: agent doesn't investigate deeply enough to prove or disprove the fault. Rack concentration is symptom not cause. | case-diagnosis on representative cases |
| 2 | NodeCrash → IB_NIC misclassification — we classified as NodeCrash but vendor fixed IB_NIC. Different root cause from Problem 1: agent missed a specific check (IB NIC error counters). | NodeCrash | ID:210 | "HOW are they wrong?" — wrong component | YES — specific missed check even with only 1 case | IB NIC counter check would have led to correct classification. This is a classification logic gap, not an investigation depth gap. | case-diagnosis on ID:210 |
| 3 | ModelPerformanceDegradation repeat offender — h200-000795 took 3 RMAs to find NVSwitch fault | ModelPerf | IDs: 209, 202, 135 | "HOW wrong?" — 1 NFF unreliable, but eventual REPAIR_CONFIRMED provides ground truth | YES — 3 RMAs for same host | Agent couldn't isolate component on first 2 attempts. Could NVLink/NVSwitch health check have caught it sooner? | case-diagnosis on ID:209 |

**Reasoning principles:**

- **Every wrong case is a potential gap** — do NOT dismiss fault types as "low signal" or "not actionable at this volume." Even 1 wrong case means something was missed. Low volume affects confidence, not whether the problem exists.
- **Merge problems with the same root cause** — NFF reliable and NFF unreliable within the same fault type likely have the same root cause (agent didn't investigate enough). But MISCLASSIFIED is a different root cause (agent investigated but classified wrong, or missed a check that would have led to a different classification). Keep MISCLASSIFIED as a separate problem.
- **Merge alias fault types** — some fault types are different names for the same fault. Before finalizing the problem list, check whether any problems from different fault types are actually the same thing and should be merged. Known aliases:
  - `DiskError` = `nvme_disk_error_v1` — same fault, different naming
  - When merging, list all alias fault types in the problem row and include cases from all merged types.
- **Don't assume investigation depth from summary fields** — `our_investigation` is always `{}`, and `our_reason` format (JSON vs free text) is NOT a reliable indicator of whether the agent actually investigated. Only case-diagnosis (transcript mining) can determine what the agent did. State "investigation status unknown" until case-diagnosis is run.
- **Detect repeating patterns** — if many cases in a fault type show the same wrong outcome, that's a systematic pattern. Group them as one problem.
- A pattern is only a **real problem** if the agent's behavior caused the wrong outcome AND we can fix it with a skill change
- **Rack clusters need same-fault concentration to be meaningful** — a rack with many NFF cases is just noise. A rack where most cases are the same fault type (e.g., IB issues on 5+ nodes) or same vendor component may indicate shared infrastructure. Only flag if same-fault concentration is anomalous.
- Workflow mismatches (high maint_fix) mean correct diagnosis, correct outcome — not a problem, no action needed
- NFF unreliable cases ARE worth investigating — the vendor's shallow investigation doesn't prove we were right
- A single MISCLASSIFIED case IS worth investigating if the missed check is clear and specific
- **A single NFF case IS a problem** — do NOT drop it, do NOT group it into a "low priority" bucket. Either merge it into an existing problem with the same root cause, or create its own problem.

Show the problem synthesis in chat.

### Step 3.5: Completeness Check

**Before moving to Step 4, verify that EVERY NFF and MISCLASSIFIED case is covered by at least one problem.**

Go through all cases fetched in Step 1.5. For each case where `vendor_verdict` is `NO_FAULT_FOUND` or `MISCLASSIFIED`:
- Is this case included in any problem from Step 3?
- If NOT → you missed it. Either add it to an existing problem (same root cause) or create a new problem.

This step prevents the agent from silently dropping cases like "1 NFF on a high-accuracy fault type" — those are exactly the cases most likely to be overlooked.

### Step 4: Prioritize

For each real problem from Step 3, list which cases to diagnose and in what order.

**DO NOT deprioritize or skip problems based on low case count.** A single NFF case IS a problem that needs investigation. Do NOT group individual NFF cases into a "single-case NFFs" bucket with "light" priority — every case gets its own problem entry or gets merged into an existing problem with the same root cause.

| Priority | Problem # | Cases to Diagnose | Why These Cases | Depth |
|---|---|---|---|---|
| 1 | 2 | ID:210 | Misclass — highest signal, will reveal what check was missed | Full |
| 2 | 1 | ID:212, ID:218 | NFF reliable representatives | Full |
| 3 | 1 | ID:190, ID:196 | NFF unreliable — check if agent investigated at all | Full |
| 4 | 3 | ID:209 | Repeat offender first visit | Full |

Show the priority list and stop. Each case in the list should be investigated using `case-diagnosis` skill separately.

### Step 5: Persist Problems and Delegate

For each real problem from Step 3, decide persist + delegate actions based on existing problem status.

**5a. For each problem from Step 3, classify into one of these cases:**

**How to match against existing problems:** Match if either (a) overlapping `case_ids`, OR (b) same `fault_type` AND any hostname in your cases also appears in the existing problem's cases (check via `get_cases_by_hostname`). Same-node same-fault = same problem, even if different case_ids.

| Case | Existing problem? | Insert? | Delegate? | Reason |
|---|---|---|---|---|
| **New problem** | No match in DB | ✅ Yes | ✅ Yes | Brand new problem — insert and delegate |
| **Needs work** (open/no task, blocked, unresolved) | Problem exists in `open`/`blocked`/`unresolved` but no active task | Merge case_ids | ✅ Yes | Needs diagnosis/fix — delegate with merged context. For `unresolved`: include what previous fix tried and why it failed. For `blocked`: new cases may unblock. |
| **Already being worked** | `open` problem AND active task with `Problem #<id>` in title | ❌ Skip | ❌ Skip | Already being worked on — merge new case_ids via `update_problem_tool` only |
| **Patch created** | `patch_created` problem exists | ❌ Skip | ❌ Skip | Patch written but not deployed. Merge new case_ids as additional evidence. Same-hostname cases signal patch not yet deployed. |
| **Won't fix, same mechanism** | `wont_fix` problem exists, new cases are same root cause | ❌ Skip | ❌ Skip | Already decided not to fix this mechanism. Don't reopen. |
| **Won't fix, different mechanism** | `wont_fix` problem exists, but new cases reveal a DIFFERENT root cause | ✅ Yes (new problem) | ✅ Yes | Different mechanism = different problem. Reference old problem ID. |
| **Resolved (regression)** | `resolved` problem exists | ✅ Yes (new problem) | ✅ Yes | Fix didn't hold — regression. Reference old problem ID. High priority. |

**How to check:** Call `get_problems()` with no filters to get ALL existing problems with their latest status. Match by overlapping `case_ids` OR same `fault_type` + same hostname. Then call `get_agent_active_tasks(agent_id="feedback")` and check each problem ID against task titles (must contain `Problem #<id>`).

**⚠️ Human approval required before delegation.** After case-diagnosis routes a problem to `skill-optimize` or `automate-detection-pattern`, the delegation uses `completion_mode="manual"`. The human must review and approve the diagnosis + planned fix before the agent proceeds. Never auto-approve.

**5b. Persist new problems** via `insert_analysis_problem_tool`:
- `title`: short title (e.g. "NodeCrash NFF — 15/22 cases no fault found")
- `fault_type`: the fault type (e.g. "NodeCrash", "ModelPerformanceDegradation")
- `prompt`: the full delegation prompt you're about to send
- `case_ids`: list of case IDs

**Do NOT write `diagnosis`, `patch_summary`, or `patch_commit`.** These fields are reserved for case-diagnosis (diagnosis) and skill-optimize (patch). analyze-rma-cases works at the statistical level — it identifies problems, not diagnoses. Writing a premature "diagnosis" biases case-diagnosis to skip its own investigation and just confirm what you already wrote.

For existing problems that match (same case_ids), only update `case_ids` if new cases should be added — do NOT update `diagnosis` or other reserved fields.

**5c. Delegate each problem that needs delegation** as a new Chat UI task via `delegate_to_agent`. Each problem gets its own task/session so context doesn't overflow. Use `completion_mode="manual"` so the human can review.

Call: `delegate_to_agent(agent_id="feedback", prompt=<delegation prompt>, completion_mode="manual")`

**Include `Problem #<DB_ID>` in the delegation title** so future dedup can match it, e.g.: `Problem #6: NVLinkFailure → IB_NIC misclassification`

The prompt must include enough context from Steps 1-3 so the case-diagnosis agent can investigate without re-fetching everything.

**Prompt template per problem:**

```
Run /case-diagnosis on Problem #<N> (problem_id=<DB_ID>)

Facts:
- Fault type: <fault_type>
- Cases: <case_ids>
- For each case: id, hostname, verdict, vendor_component, vendor_repair_raw, our_reason, our_evidence
<NODE_HISTORY_BLOCK if repeating pattern>

Wrong outcome: <just the numbers, no interpretation>
e.g. "2 cases: 1 MISCLASSIFIED (NVLinkFailure→IB_NIC), 1 NFF"
e.g. "3 RMAs for same node across 3 fault types"
e.g. "15/22 cases returned NFF"
```

**Do NOT include:**
- What the gap is (e.g., "agent missed IB NIC error counters") — let case-diagnosis diagnose this independently
- What to investigate or focus on — this biases the diagnosis before it starts
- Your interpretation of why the cases went wrong — case-diagnosis must reach its own conclusions by re-investigating
- Whether this is a detection problem or skill problem — case-diagnosis determines `next_owner` via Q1-Q4 routing

**Why:** analyze-rma-cases works at the statistical level (patterns across many cases). case-diagnosis works at the individual case level (deep investigation of specific failures). Passing statistical-level conclusions down biases the diagnosis. The delegation should provide raw facts only; case-diagnosis is responsible for determining what actually went wrong, why, and whether the fix is a detection rule change or a skill patch.

**For repeating pattern problems (repeat offenders, rack clusters with same fault):** Before delegating, call `get_cases_by_hostname` for each affected hostname. Include the full history in the prompt — don't tell the diagnosis agent to look it up, provide it directly:

```
Node history (ALL fault types, not just this problem):
- h200-000795: 3× ModelPerformanceDegradation (ID:209 NFF, ID:202 MAINT_FIX, ID:135 REPAIR_CONFIRMED), 1× IBLinkFlapping (ID:xxx MAINT_FIX)
- h200-000270: 2× NodeCrash (ID:219 NFF, ID:226 CONFIG_TASK)

Wrong outcome: 3 RMAs for same node, NVSwitch only found on 3rd attempt
```

**Example:**
```
Run /case-diagnosis on Problem #3 (problem_id=3)

Facts:
- Fault type: ModelPerformanceDegradation
- Cases:
  - ID:209, h200-000795, verdict: NO_FAULT_FOUND, vendor: "checked NICs/GPUs/10min monitoring", our_reason: {"NodeId":"3406","FaultCode":"SuperBenchModelPerformanceDegradation"}
  - ID:202, h200-000795, verdict: MAINTENANCE_FIX, vendor: "OS reinstall", our_reason: {"NodeId":"3406","FaultCode":"SuperBenchModelPerformanceDegradation"}
  - ID:135, h200-000795, verdict: REPAIR_CONFIRMED, vendor_component: NVSwitch, vendor: "NVSwitch replaced", our_reason: SSH unreachable + NodeNotReady + GpuCountChanged

Node history (ALL fault types):
- h200-000795: 3× ModelPerformanceDegradation (ID:209 NFF, ID:202 MAINT_FIX, ID:135 REPAIR_CONFIRMED)

Wrong outcome: 3 RMAs for same node, NVSwitch only found on 3rd attempt
```

