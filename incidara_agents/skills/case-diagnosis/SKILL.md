# Case Diagnosis

Diagnose WHY a case went wrong. Output: attribution + gap + root cause + next_owner. Does NOT design or write the fix.

## When to Use

- Triggered by `analyze-rma-cases` for each persisted problem
- Manual: "diagnose case 406" or "why was this node misclassified?"

## Input

From `analyze-rma-cases` delegation or manual invocation:
- `problem_id`, `fault_type`, `case_ids`, per-case details
- `mismatch summary`
- Optional patrol `finding_id`, `rule_id`, and `rma_finding_reconciliations` row

**⚠️ Ignore any existing diagnosis in `analysis_problems`.** Previous diagnoses may be wrong or biased. Reach your own conclusions from fresh investigation.

## ⛔ Hard Walls During Diagnosis

- **Do NOT read skill files** — no `categorization-rules.md`, no `investigation-methodology.md`, no `SKILL.md` from any skill. You are diagnosing WHAT went wrong, not designing the fix. Reading skills before diagnosis biases you.
- **Do NOT touch the repo** — no `git` commands, no file reads from `/tmp/repo-<problem_id>`. The repo doesn't exist yet.
- **Do NOT design or write any fix** — your output is gap + root cause only.

---

## 1. Investigate

### 1.1 Mine the Transcript

For each case, reconstruct what the original agent actually did.

```bash
python3 ${CLAUDE_SKILL_DIR}/sub-skills/transcript-mining/mine_transcript.py \
  --case-id <case_id>
```

If `--case-id` doesn't work (no DB access), use `--hostname` or `--session-file`. See `sub-skills/transcript-mining/SKILL.md` for full usage.

The script outputs a **trace table** (Who column, tool calls, inputs, results) and an **investigation summary** (diagnostic depth, user messages, tool categories).

**Key things to look for:**
- What tools did the agent call? What did each return?
- What did the agent conclude at each step?
- Where did investigation stop or dead-end?
- Did a human provide any finding that the agent adopted?

**Who column is critical.** If the correct conclusion came from a user message, the agent's own investigation was insufficient. The fix must make the agent find it without human help.

**⛔ If you skip transcript mining, you are GUESSING what the agent did.** You must show the actual trace — tool calls, tool results, agent conclusions — not summarize from the case_memory fields.

For bulk cases: mine 2-3 representative cases, not all.

### 1.2 Re-investigate the Case Yourself

**This is the most important step. Do NOT skip it. Do NOT abbreviate it.**

The transcript tells you what the agent did. But NOT what it would have found if it had investigated properly. You must find the actual root cause yourself.

**The principle:** Treat this as if YOU are the triage/repair agent investigating this node for the first time. Use every tool and data source available — alerts, job logs, BMC logs, SSH diagnostics, node history, similar cases. The goal is: **if the agent had done this investigation, it would have reached the correct conclusion without any human help.**

**You must run actual diagnostic tools and read actual outputs.** Minimum 3 tool calls on real data:
- `get_node_alerts` for the node's alert history in the triage window
- `get_validation_job` + read the actual job log output (the validation job that failed)
- At least one SSH diagnostic command on the node (IB counters, GPU state, dmesg, etc.)

**Do NOT guess or assume what the evidence would show:**
- Don't assume "IBLinkFlapping → IB port errors" without checking IB counters
- Don't assume "NVLinkFailure → NVLink issue" without reading the validation job log
- Don't assume "NodeCrash → hardware" without checking what caused the crash

**Mine every data source** — alerts detail, job logs' error messages, job status, BMC SEL logs, IB counters, GPU state, dmesg, similar cases on same host.

**User messages in the original transcript are clues, not evidence.** If a human told the agent "check the IB NIC" and the agent then found the issue, that means the agent couldn't have found it on its own. Your re-investigation must produce the same finding without any human hints.

**If a tool/script failed during the original investigation, test it yourself.** Run the same tool on the same data. If it fails the same way, note it — this is part of the gap (broken tool, not just missing step).

**⛔ Minimum evidence to proceed:** You must be able to answer: "What was the ACTUAL fault, and what specific evidence proves it?" If you can't answer with evidence you collected yourself, you haven't investigated deeply enough. Go back and run more tools.

### 1.3 Check the Detection Signal

Understand what triggered this case — the detection source determines which fix candidates apply.

**Three possible detection sources:**

**A. Patrol rules** (our custom detection):
- Call `list_findings(target_id="<hostname>", current_code_only=True)` (patrol-cron MCP)
- **Check `from_old_code`** — ignore findings from old code versions
- Call `get_rule(rule_id=...)` to see what the rule monitors and its analysis logic
- Call `get_collector(...)` to see what data sources feed this rule
- **Did it detect the right fault?** Or did it fire on a proxy symptom that led to wrong investigation?

**B. Built-in alert-manager rules** (platform-level, not editable by us):
- Call `get_node_alerts` (node-ops MCP) to see what alerts fired in the triage window
- These are the original alert-manager rules (e.g., IBPortDown, NodeCrash, NvidiaSmiLatencyTooLarge)
- We cannot modify these. The question is: **should we add a patrol rule to complement this alert?**
  - Does the alert provide enough signal for triage? Or does the agent need a more specific patrol finding?
  - Would a patrol rule with richer data sources (SSH, node_logs) produce better signal?

**C. User report** (human-triggered):
- Check **timing**: was the node cordoned or an alert raised BEFORE or AFTER the user report?
  - **Detection caught it first** (node already cordoned/alerted before user report) → not a recall gap. The issue is precision or repair.
  - **User noticed first** (node cordoned after user report, or no automated alert existed) → recall gap. Our system was too slow or missing entirely.
- The fix candidate for recall: add a new patrol rule to catch this fault type automatically in the future

**Answer these questions:**
- **What triggered the case?** Patrol finding, alert-manager alert, or user report?
- **Did a detection rule fire?** The practical way to check is **timing**: when was the node cordoned vs when was the issue reported? If the node was cordoned/alerted BEFORE the user report, detection caught it. If the node was cordoned AFTER the user report (user noticed first), detection missed it → recall gap. Check patrol findings and alert timestamps relative to the case timeline.
- **If alert-manager only:** is the alert signal sufficient for triage, or does the agent need a companion patrol rule with richer data?
- **If patrol finding:** did it detect the same fault you found in 1.2, or a different symptom? Are its data sources capable of detecting the actual fault?

**Example:** Rule `ib_link_flapping_repeat_v1` monitors IB port state changes via Prometheus + syslog `ib\d+` pattern. If the actual fault is Ethernet NIC flapping (mlx5_3/enp86s0np0), the rule fires on the IB symptom but misses the Ethernet root cause. This is "detection accuracy — wrong signal" (NFF candidate 1), not "agent investigated wrong component" (candidate 3).

## 2. Diagnose

### 2.1 Identify the Gap

Compare across all three investigation axes:
- **What the agent did** (1.1)
- **What the actual fault is** (1.2)
- **What the detection signal was** (1.3)

State the gap concisely in one sentence.

**Gap patterns (examples, not exhaustive):**
- **Detection missed it** — no finding existed for this fault type
- **Detection caught wrong signal** — rule fired on symptom A but the fault is B
- **Detection fired correctly, agent misidentified component** — finding pointed to the right area but agent diagnosed the wrong component
- **Detection fired correctly, evidence insufficient for vendor** — agent identified the right fault but ticket didn't convince vendor
- **Tool/script broken** — agent tried to use a tool but it failed, and agent gave up
- **Investigation stopped too early** — agent stopped after one data point instead of following all paths
- **Classify-then-prove** — agent decided classification first, then looked for supporting evidence instead of differential diagnosis

### 2.2 Diagnose Root Cause

**Why does the gap exist?** Think at the reasoning level, not just the step level.

"Missing IB check" is a step-level symptom. The deeper question: **why did the agent's reasoning lead it to stop investigating and file a wrong classification?**

**The most common root cause is classify-then-prove thinking:** agent decides classification first, then investigates to support it. When evidence is negative, it files anyway. The correct model is **differential diagnosis**: list possible explanations, investigate to eliminate, classify from what survives.

**Root causes must be things WE can fix.** "Vendor ran fixed checklist" is a symptom. The root cause is what WE failed to do.

**Key principles:**
- **Burden of proof is on us** — no hardware evidence = not a hardware problem
- **Negative evidence is evidence** — "BMC SEL clean" ≠ "nothing found"; it's positive evidence no hardware event triggered
- **Symptom ≠ diagnosis** — NodeCrash, ModelPerfDegradation are symptoms, not diagnoses
- **A failed validation benchmark IS hardware evidence** — find WHERE the fault is, not WHETHER it exists
- **Don't stop when infrastructure checks come back clean** — expand to workload logs, historical patterns
- **Every wrong case is a potential gap** — don't dismiss low-volume fault types
- **Don't assume investigation depth from summary fields** — `our_investigation` is always `{}`, `our_reason` format is NOT reliable. Only transcript mining reveals what the agent actually did.
- **`repair_status` is a strong signal** — `已拒绝` = rejected, `已撤回` = withdrawn
- **Bulk NFF → one systemic gap** — same fault type + same gap type = file one gap, not N
- **⛔ Diagnose from the agent's perspective, not hindsight.** Vendor verdicts (MAINTENANCE_FIX, NO_FAULT_FOUND, REPAIR_CONFIRMED, etc.) are feedback-time knowledge. The triage/repair/recycler agents do NOT see verdicts — they operate before the vendor returns results. The diagnosis must answer: "what should the agent have done differently with the information it had at the time?" NOT "what would the agent do if it knew the vendor verdict?" For example: if SSH is unreachable after RMA return, the agent should try BMC reboot before re-escalating — this is a workflow gap the agent can fix. The agent cannot know the verdict was MAINTENANCE_FIX, so don't frame the fix around verdict knowledge.

## 3. Route

Based on the **case outcome**, enumerate which **fix candidates** apply and pick the best one(s).

### Fix Candidates by Case Outcome

**NFF (case submitted → vendor says nothing wrong):**

| # | Candidate | When it applies | Fix target | `attribution` |
|---|-----------|----------------|------------|---------------|
| 1 | Detection accuracy — patrol rule detected wrong signal | Patrol rule fired on symptom A (e.g., IB flapping) but the real fault is B (e.g., Ethernet NIC). Rule detected a real symptom but not the root cause. | **Skill-optimize first**: repair should validate persistence, do differential diagnosis before RMA. Detection refinement (add companion rule) is secondary — narrowing risks recall. | `repair` (primary), `detection` (secondary) |
| 2 | Detection precision — false positive | Patrol rule or alert-manager alert fired on a non-issue. No real hardware fault exists (self-recovery, transient). | **Skill-optimize first**: repair should validate locally before escalating. Detection narrowing is secondary — narrowing risks missing real permanent faults. | `repair` (primary), `detection` (secondary) |
| 3 | Detection recall — automated detection missed it | No patrol finding and no alert-manager alert existed before the user report. The node was cordoned after the user reported it (or not at all). Our automated detection was too slow or missing entirely. | Add new patrol rule or broaden existing rule. **This is the primary route even if repair also has gaps.** | `detection` |
| 4 | Triage — agent could have triaged better | Detection found something, but agent didn't do differential diagnosis (only checked one component), mis-categorized (hardware vs platform vs user vs transient), or didn't validate locally before escalating. | Fix triage skill: add differential diagnosis, better categorization, local validation before RMA | `triage` |
| 5 | Repair — agent could have repaired better | Agent submitted RMA when local repair was possible (reboot, power cycle, driver reload, self-heal). Or agent collected weak evidence / poor wording for vendor when stronger case was possible. | Fix repair skill: add self-heal steps, local repair before RMA, stronger evidence/wording | `repair` |
| 6 | Vendor miss | Real issue, vendor just didn't catch it. Agent did everything right — strong evidence, right component, appropriate escalation. No agent code gap. | `blocked` — vendor process issue | — |

Note: If the case was triggered by alert-manager only (no patrol finding), consider whether a companion patrol rule would produce better signal for triage.

**REPAIR_CONFIRMED / MAINTENANCE_FIX / CONFIG_TASK (vendor found real issue):**

| # | Candidate | When it applies | Fix target | `attribution` |
|---|-----------|----------------|------------|---------------|
| 1 | Detection recall — no finding existed | Rule did not fire for this node. Real fault went undetected. | New rule or broaden existing rule | `detection` |
| 2 | Detection accuracy — finding rejected as false positive | Rule fired correctly but was reviewed and rejected. | Fix review criteria or rule pattern | `detection` |
| 3 | System worked — no fix needed | Finding existed, was acted on, issue got fixed. | Close the problem | — |

**MISCLASSIFIED:**

| # | Candidate | When it applies | Fix target | `attribution` |
|---|-----------|----------------|------------|---------------|
| 1 | Detection pattern — rule matched wrong fault type | Rule's pattern is too broad, matches different fault types. | Narrow rule pattern | `detection` |
| 2 | Triage classification — correct finding, wrong category | Finding was correct but triage put it in wrong bucket. Agent should have followed the finding signal, not the symptom. | Fix triage categorization skill — follow finding, not symptom | `triage` |

### Pick among candidates

1. **Multiple candidates can coexist.** Document ALL that apply.
2. **⛔ Filter out infeasible candidates.** Can the agent actually do this with the information it has at decision time? Examples of infeasible candidates:
   - "Add SSH diagnostic when node is SSH unreachable" — can't SSH to a down node
   - "Check vendor verdict before classifying" — agents don't see verdicts
   - "React to MAINTENANCE_FIX status" — agents don't know RMA outcomes
   If a candidate is infeasible, mark it as such in `secondary_gaps` and skip it for `primary_route`.
3. **Pick the one with highest impact as `primary_route`.** Defer others to `secondary_gaps`.
4. **⛔ Precision issue (rule fired but was wrong/premature) → skill-optimize first.** If the rule fired on a real symptom but the agent acted too early (transient signal that self-resolved, Xid 31 that was workload-induced), the safest fix is skill-optimize: train repair to validate persistence, try self-heal, run local diagnostics before escalating to RMA. **Narrowing the detection rule risks missing real permanent faults → recall loss from the fix itself.** Detection refinement (add persistence check, raise threshold) is a secondary gap, not primary.
5. **⛔ Recall issue (automated detection missed it) → detection first.** If no patrol finding or alert-manager alert existed before the user report (the node was cordoned after the user reported it, or not at all), detection is first priority. The `primary_route` MUST be `automate-detection-pattern` and `attribution` MUST be `detection`. Do NOT demote detection to secondary just because repair also has gaps. Repair gaps are secondary — address them after detection delegation. After delegating to detection, run `/skill-optimize` yourself for secondary gaps.
6. **No detection before user report (neither patrol nor alert-manager) → recall gap → detection first.** If neither a patrol rule nor an alert-manager Prometheus alert caught the fault before the user reported it — our system was too slow or missing entirely. `primary_route=automate-detection-pattern`, `attribution=detection`. Even if repair also made mistakes, the recall gap takes priority. Repair improvements are secondary — address after detection delegation.
7. **`tradeoff_rationale`** must explain why you chose this candidate over the others.
7. **Do NOT downgrade to blocked based on case count.** A single case with a clear, actionable gap is enough for `skill-optimize` or `detection`. Case-diagnosis identifies the gap; skill-optimize decides the timing.
8. **⛔ Explicit anti-patterns — do NOT use these as reasons to block or downgrade:**
   - **"Only 1 case, need more data"** — wrong if the gap is a missing skill section or broken tool
   - **"Vendor quality is MINIMAL"** — irrelevant to whether our agent has a gap
   - **"Mixed outcomes"** — route on the actionable gap, not the unactionable ones
   - **"Low-volume fault type"** — means fewer chances to get it right, not less reason to fix
   - **"No positive guard case"** — not a reason to block detection delegation; detection agent handles this
9. **Only set `blocked` when the agent truly did everything right AND there's no recall gap.** If there's a recall gap (no rule fired), route to detection regardless of repair improvements. If there's only a precision gap + repair improvements (rule fired but agent acted too early), route to skill-optimize. Blocked means: vendor missed a real issue, agent had strong evidence and right component, AND a rule existed for this fault type.

### Label feedback (for detection-attributed cases only)

If the case is linked to a patrol finding through `rma_finding_reconciliations`, label it so
detection automation knows whether the rule should change.

- **Candidate 2 (false positive)** → `feedback_label="negative"`, `expected_behavior={"should_fire": false}`
- **Candidate 1 (wrong signal, but rule should still fire on original symptom)** → `feedback_label="positive"`, include corrected expected fields like `{"should_fire": true, "expected_fault": "ethernet_nic_flapping"}`
- **Confirmed guard example** → `feedback_label="positive"`, `expected_behavior={"should_fire": true}`
- **Non-detection attribution** → leave `feedback_label="unknown"` so detection automation doesn't dirty the rule

Use `mcp__agent-feedback__update_rma_finding_attribution` for this. Do not create replay cases here;
replay fixtures are generated later by `automate-detection-pattern` from labeled feedback rows.

### Routing table

| next_owner | When | What happens |
|---|---|---|
| `skill-optimize` | `attribution` is `triage` or `repair`, **OR precision issue** (rule fired but agent acted too early) | Run `/skill-optimize` yourself. Do NOT delegate. Add self-heal steps, local validation, better evidence collection, differential diagnosis. **Precision issues go here first** — narrowing detection rules risks recall loss; train the agent to validate before escalating instead. |
| `automate-detection-pattern` | `attribution="detection"` — **recall issue** (no patrol finding or alert-manager alert before user report) | Delegate to detection agent. **Recall first, improve accuracy as possible** — never sacrifice recall for precision. After delegating, also run `/skill-optimize` yourself for any triage/repair secondary gaps. |
| `blocked` | No fixable code gap — vendor missed a real issue, agent did everything right | Do not delegate. Document what happened. |

### Document and delegate

**Document in diagnosis:**
- `gap`: what went wrong
- `attribution`: which system is responsible
- `fix_route`: what kind of fix (`rule_code`, `triage_skill`, `repair_skill`, etc.)
- `primary_route`: chosen `next_owner`
- `secondary_gaps`: deferred candidates
- `tradeoff_rationale`: why this candidate over alternatives

**Persist** with `update_problem_tool(problem_id, diagnosis=...)` when `problem_id` is provided.
Include `attribution`, `fix_route`, gap, root cause, evidence summary, and `next_owner`.
Do not write `patch_summary` or `patch_commit`.

**⛔ Do NOT automatically record agent_memory for every diagnosis.** Only call `record_memory` when the case reveals a **reusable, repeatable insight corrected by a human** — something a human explicitly told you that you got wrong, or a pattern that applies broadly. Examples of insights worth recording:
- Human corrected your diagnosis: "NVSwitch SXid 12028 is ALWAYS hardware, not firmware"
- Repeatable pattern from vendor feedback: "When vendor says 'swapped GPUs and test passed' but marks NFF, the collector data was right"
- User-reported correction: "Xid 31 FAULT_PDE is workload-induced, not hardware"

Examples of things NOT worth recording:
- Problem-specific facts ("case 481 had 52 SSH commands")
- Your own diagnosis conclusions or routing decisions
- Gap descriptions only meaningful for this specific problem
- Anything you derived yourself without human correction

**Rule: no human correction = no memory.** Wrong memories are worse than missing memories.

**⛔ Do NOT delegate or proceed without human approval.** After diagnosis is complete, STOP and present the diagnosis summary to the human. Wait for explicit approval before:
- Calling `delegate_to_agent` for detection delegation
- Running `/skill-optimize` yourself
- Calling `update_problem_tool` to change problem status
- Recording any `agent_memory`

Human review catches routing mistakes, missing context, and premature actions. The diagnosis is your recommendation; the human decides whether to act on it.

**When human approves, delegate only when the next owner is actionable:**
- `next_owner="automate-detection-pattern"` → `delegate_to_agent(agent_id="detection", completion_mode="manual", ...)`.
  The prompt should say to use `/automate-detection-pattern`, call
  `create_rule_replay_cases_from_feedback`, run the full replay suite, and only
  then call `update_rule_code(..., mode="finalize")`.
  **CRITICAL**: Only reference finding IDs where `from_old_code=false`. Old-code
  findings are from a previous rule version and misleading. Use
  `list_findings(rule_id=..., current_code_only=True)` to verify.
  **Recall first**: Emphasize that narrowing a rule must not sacrifice recall.
  A rule that fires too often (precision cost) is acceptable; a rule that misses
  real faults (recall cost) is not. The detection agent must preserve or improve
  recall while increasing accuracy.
- `next_owner="skill-optimize"` → Run `/skill-optimize` yourself. Do NOT delegate to
  another agent. The gap is in triage or repair — agent could have done differential
  diagnosis, better categorization, local validation, self-heal, local repair, or
  collected stronger evidence/wording for vendor. You own the skill fix.
  **Precision issues (rule fired but was premature) also go here** — narrowing the
  detection rule risks recall loss; training the agent to validate before escalating
  is safer.

If `next_owner` is `blocked`, do not delegate a patch task.
Update the problem status and reply with the exact blocker details.

---

## Sub-skills

- **`sub-skills/transcript-mining`** — extract structured investigation trace from Claude SDK session transcripts

---

## Analysis Depth

- **Full diagnosis** (all steps): MISCLASSIFIED; NFF with MODERATE/DETAILED vendor quality; repeat offenders; `repair_status=已拒绝`
- **Profile-check first**: NFF with MINIMAL/NONE vendor quality — check symptom profile. If matches known hardware pattern → full diagnosis. If uninformative → still look for triage/repair improvements (stronger evidence, self-heal) before defaulting to `blocked`.
- **Skip**: Workflow mismatches; rack/infrastructure (SRE action)
