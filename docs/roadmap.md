# Incidara Roadmap

## Goal

Build a policy-bounded multi-agent system that can detect, triage, diagnose, reproduce, recover, and learn from both node and training-job incidents across a production GPU fleet.

The target loop is:

```text
signal
  → incident
  → evidence
  → diagnosis
  → approval or bounded action
  → recovery verification
  → outcome
  → memory
  → rule / skill / policy improvement
```

This roadmap starts from the capabilities already implemented in Incidara. It does not treat existing agents, skills, MCP servers, or operational infrastructure as future work.

## Status legend

| Status | Meaning |
|---|---|
| ✅ Complete | Implemented and used as an operational foundation |
| 🟡 Partial | Useful implementation exists, but the end-to-end contract or production gate is incomplete |
| ⬜ Planned | Design direction is known; implementation has not started |

## Milestone summary

| Milestone | Status | Outcome |
|---|---|---|
| M0 — Agent runtime and Console | ✅ Complete | Stateful agents with tasks, sessions, schedules, SSE, approvals, and recovery |
| M1 — Node incident lifecycle | ✅ Complete | Detection → triage → repair → recycler workflow |
| M2 — Evidence, permissions, and safety | ✅ Complete | Shared evidence, role-scoped tools, and human gates |
| M3 — Deployment and operations | ✅ Complete | Rendered Compose topology, incremental CI, health checks, and backup |
| M4 — Detection feedback foundation | 🟡 Partial | Findings, outcomes, attribution, replay, and skill/rule patch workflow |
| M5 — Unified orchestration workflow | 🟡 Partial · Next | Durable cross-agent incident state and deterministic handoffs |
| M6 — Layered agent memory | 🟡 Partial | Working, episodic, semantic, and policy memory with provenance |
| M7 — User-exception learning and self-evolution | 🟡 Partial | Convert corrections and exceptions into replay-gated improvements |
| M8 — Reproduction-driven diagnosis | 🟡 Partial | Correlate → confirm → reproduce with executable diagnostics |
| M9 — Training-job triage and diagnosis | 🟡 Partial | Operationalize the existing job-incident skill chain |
| M10 — Evaluation and autonomy graduation | ⬜ Planned | Measure quality and safely expand autonomous operation |

---

## Completed foundations

### M0 — Agent runtime and Incidara Console ✅

**Implemented**

- Stateful HTTP agent gateway around the Claude Agent SDK.
- Session states for running, waiting for input, interruption, completion, and error.
- JSONL persistence and session recovery.
- SSE for messages, thinking, tool calls, permission requests, and sub-agent activity.
- Persistent Console tasks, queues, schedules, reconciliation, authentication, groups, and permissions.
- Human approval and denial for gated tool calls.
- Usage, reliability, availability, job, utilization, and agent metrics views.

**Primary implementation**

- `incidara_agents/agents/claude-agent/`
- `console/client/`
- `console/server/`

### M1 — Production node incident lifecycle ✅

**Implemented**

```text
detection → triage → repair → recycler → feedback
```

- Proactive findings from patrol rules, switch inspection, and user reports.
- Evidence-driven classification into hardware, platform, transient, or unknown.
- Hardware repair with mandatory approval before RMA submission.
- Platform repair with verification and revalidation.
- Repair-ticket tracking and automated node reallocation.
- Persistent batch progress for interrupted triage sessions.
- Attention reporting for blocked, ambiguous, or approval-required work.

**Primary implementation**

- `incidara_agents/agents/{detection-agent,triage-agent,repair-agent,recycler-agent,attention-agent}/`
- `incidara_agents/skills/{scan-cluster,inspect-infra-issue,triage-nodes,repair-nodes,ticket-check,node-reallocation,agent-attention-queue}/`

### M2 — Evidence, permissions, and safety ✅

**Implemented**

- Shared investigation evidence across Detection, Triage, and Repair.
- Automatic evidence persistence from MCP tools and explicit saving for ad-hoc output.
- Evidence checks before high-risk repair actions.
- Separate `readonly`, `diagnosis`, `ops`, and `feedback` MCP roles.
- Blast-radius checks and human approval for destructive operations.
- Vendor-safe repair summaries that remove workload identity.

**Primary implementation**

- `incidara_agents/mcp_servers/agent-evidence/`
- `incidara_agents/mcp_servers/node-operations/`
- `infra/postgresql/init.sql`

### M3 — Deployment and operations ✅

**Implemented**

- Configuration-driven Compose renderer.
- Separate database, MCP, agent, Console, and backup Compose files.
- Incremental CI mapping from changed paths to affected services.
- Skill-only synchronization without unnecessary image rebuilds.
- Service health checks and dependency ordering.
- PostgreSQL pgBackRest backup and restore support.
- Agent workspace and transcript backup to object storage.

**Primary implementation**

- `compose/`
- `ci/`
- `infra/`

---

## Active and planned milestones

### M4 — Complete the detection feedback foundation 🟡

Incidara already records findings, inspector verdicts, RMA outcomes, attribution, replay cases, analysis problems, and rejected proposals. The remaining work is to make the loop complete and measurable for every relevant incident.

**Already implemented**

- `case_memory` for completed RMA outcomes.
- `analysis_problems` lifecycle from open through monitoring and resolution.
- Finding-to-RMA reconciliation records.
- Detection attribution and feedback labels.
- Frozen rule replay cases and positive counterexamples.
- Dirty-rule state, repair attempts, safe/shadow containment, and replay-gated rule updates.
- Human-approved skill patch and deployment workflow.
- Rejected-proposal memory to prevent repeating failed fixes.

**Remaining**

- Add one stable incident identifier across finding, task, evidence, repair, RMA, and outcome records.
- Ensure every completed repair outcome is reconciled or explicitly recorded as unmatched.
- Measure feedback coverage, attribution confidence, replay coverage, and post-deployment outcomes.
- Close the gap between proposed skill patches and verified production improvement.
- Add canary, monitoring-window, rollback, and automatic rejection recording for unsuccessful changes.

**Exit criteria**

- Every eligible RMA outcome has a durable reconciliation state.
- Every detection-attributed correction has a failed replay case and a healthy counterexample.
- A rule or skill change cannot graduate without replay, live validation, approval, and a monitored outcome.
- Failed improvements are recorded and are not proposed again unchanged.

**System reference:** [Feedback agent and learning loop](incidara-agent-sre.md#225-feedback-agent)

### M5 — Unified orchestration workflow 🟡 · Next

Today, workflows are coordinated through skills, Console tasks, MCP delegation, and persisted sessions. This milestone makes the cross-agent lifecycle explicit and durable rather than relying on prompts and local working files to carry the entire protocol.

**Already implemented**

- Task queue, scheduler, gateway sessions, interruption, resume, and reconciliation.
- Agent-to-agent delegation with active-task deduplication.
- Skill-level workflow steps and human approval waits.
- Working files for batch progress and crash recovery.

**Remaining deliverables**

1. **Unified incident envelope**
   - Stable `incident_id` for node and job incidents.
   - Target, impact scope, severity, state, owner, evidence references, hypotheses, action history, and outcome.
   - Links to Console task/session IDs and external alert, ticket, and job IDs.

2. **Lifecycle state machine**

   ```text
   detected
     → triaging
     → diagnosed
     → awaiting_approval | acting | monitoring
     → recovered | unresolved | blocked
     → learning
     → closed
   ```

   - Valid transitions and owning agent for each state.
   - Idempotent transition commands.
   - Timeout, retry, cancellation, and compensating-action rules.

3. **Typed handoff contract**
   - Required evidence and proof limits for every agent-to-agent handoff.
   - Explicit next owner, next action, and completion condition.
   - Rejection when mandatory context is missing.

4. **Orchestration visibility**
   - One incident timeline in the Console across multiple tasks and agents.
   - Current owner, blocked reason, pending approval, and next action.
   - Detection of stalled or duplicated workflows.

5. **Failure handling**
   - Resume from the last committed transition after an agent or host restart.
   - Prevent duplicate repair, RMA, and reallocation actions.
   - Route ambiguity and expanding blast radius to Attention.

**Exit criteria**

- One incident is traceable from detection through feedback using one ID.
- Restarting any agent does not lose progress or repeat a destructive action.
- Every handoff is machine-validatable and visible in the Console.
- Stalled, duplicate, and invalid transitions create actionable attention items.

**System reference:** [Agent gateway](incidara-agent-sre.md#23-agent-gateway)

### M6 — Layered agent memory 🟡

Incidara has evidence records, case memory, agent memory, session transcripts, analysis problems, and rejected proposals. These stores need a single memory model with explicit promotion and retrieval rules.

**Existing memory stores**

| Layer | Existing implementation | Purpose |
|---|---|---|
| Working memory | Gateway session events and agent working files | Current task execution and resume |
| Evidence memory | `investigation_evidence` | Raw observations and tool output |
| Episodic memory | `case_memory` and incident/RMA history | What happened in one case |
| Improvement memory | `analysis_problems`, replay cases, rejected proposals | What was tried and whether it worked |
| Semantic memory | `agent_memory` | Reusable human-corrected insight |
| Procedural memory | Versioned skills and rules | How the system behaves next time |

**Remaining deliverables**

- Common provenance: source incident, author, confidence, evidence links, model/skill version, creation time, and validity state.
- Retrieval scoped by agent, target, fault class, hardware SKU, software version, and time window.
- Promotion from raw evidence → episode → validated insight → rule/skill candidate.
- Human correction and invalidation without deleting historical evidence.
- Deduplication, supersession, and contradiction handling.
- Retention rules for large artifacts while preserving hashes and pointers.
- Memory-use logging showing which retrieved memories influenced a decision.
- Evaluation for relevance, faithfulness, contamination, and stale-memory regressions.

**Exit criteria**

- Agents retrieve only relevant, attributable memory for the current incident.
- Every reusable insight points to evidence and human or replay validation.
- Incorrect memory can be invalidated and stops influencing new decisions.
- A decision records which memories it used, enabling replay and audit.

### M7 — Learn from user exceptions and safely self-evolve 🟡

The current feedback loop primarily learns from RMA outcomes and agent transcripts. This milestone expands learning to explicit user corrections and operational exceptions without allowing unreviewed behavior drift.

**Exception sources**

- User correction of diagnosis, classification, scope, or remediation.
- Approval denial or modification.
- Human override of an agent action.
- RMA `NO_FAULT_FOUND`, misclassification, rejection, or withdrawal.
- Failed repair, rollback, or reallocation.
- Repeated unresolved incident or recurring false positive.
- Missing evidence, unavailable telemetry, unsupported hardware, or policy conflict.

**Remaining deliverables**

1. Persist a structured exception containing the original decision, user correction, evidence available at the time, affected workflow stage, and final outcome.
2. Attribute it to detection, triage, diagnosis, repair, orchestration, memory, tooling, policy, or external uncertainty.
3. Route it to the correct target: rule, skill, memory, tool, workflow, or policy.
4. Generate a minimal candidate change with a regression case and healthy counterexample.
5. Require replay, safety checks, approval, canary rollout, monitoring, and rollback.
6. Record failed candidates in rejected-proposal memory.
7. Measure repeat-exception rate after deployment.

**Exit criteria**

- A user correction becomes a durable replay case instead of disappearing in chat history.
- Changes are proposed only when evidence and ownership are clear.
- No skill, rule, memory, workflow, or policy change reaches production without its required gate.
- The validated exception occurs less often after the change, or the change is rolled back and remembered as rejected.

### M8 — Reproduction-driven diagnosis 🟡

The repository contains reproduction skills for hangs, NCCL/RCCL timeouts, GPU OOM, NaN loss, slowdown, jitter, checkpoint failures, process crashes, and minimum-resource MoE experiments. The missing piece is a reliable execution and measurement layer connecting those plans to diagnostic jobs.

**Already implemented**

- Failure-mode log triage tables.
- Cross-source system-evidence diagnosis skills.
- Controlled reproduction decision tables.
- Minimum complete-node resource planning.
- Safety rules, proof limits, healthy guards, and validation handoffs.
- Node investigation and validation-job tools.

**Remaining deliverables**

- `run_diagnostic_job(nodes, benchmark, parameters)` MCP tool.
- Executors for DCGM, DGEMM, memory tests, NVLink bandwidth, NCCL tests, and targeted workload replay.
- Structured result parsers and durable artifact storage.
- Per-SKU and per-software-version healthy baselines.
- Equal-exposure control selection and safe resource reservation.
- Correlation of results with the original incident timeline.
- Reproduction budget, timeout, cancellation, cleanup, and blast-radius enforcement.
- Diagnosis-confidence updates from support, refute, or inconclusive outcomes.

**Target methodology**

```text
CORRELATE
  historical and cross-source evidence
      ↓
CONFIRM
  bounded diagnostic on the suspected component
      ↓
REPRODUCE
  controlled workload changing one relevant variable
      ↓
VERIFY
  healthy guard, recovery progress, and recurrence condition
```

**Exit criteria**

- Every run has a hypothesis, fixed variables, changed variable, expected observation, safety budget, and cleanup plan.
- Results are machine-readable and linked to the incident and diagnosis.
- “Did not reproduce” remains inconclusive unless the experiment had sufficient fidelity.
- Reproduction can strengthen or refute diagnosis without silently expanding production impact.

### M9 — Training-job triage and diagnosis 🟡

The skill architecture exists, but it is not yet a fully deployed and measured job-incident service with durable orchestration and complete data coverage.

**Implemented skill chain**

```text
job-incident-response
  → job-log-triage
  → system-evidence-diagnosis
  → job-recovery
  → training-reproduction
  → rca-closeout
```

**Covered failure modes**

- Job hang
- NCCL/RCCL timeout
- GPU out-of-memory
- NaN/non-finite loss
- Sustained throughput slowdown
- Throughput jitter
- Checkpoint failure
- Process crash
- Startup and scheduling failure

**Remaining deliverables**

1. **Deployment profile** — bind the skill chain to a dedicated or explicitly configured agent gateway and expose it in the Console.
2. **Incident ingestion** — create job incidents from platform events, stalled progress, user reports, and patrol findings.
3. **Unified job timeline** — job/attempt, rank, process, GPU, node, fabric path, checkpoint, and relevant changes.
4. **Cross-rank diagnosis** — distinguish the first failing rank from propagated failures and hangs.
5. **Cross-job correlation** — identify shared node, rack, rail, storage, scheduler, image, or software-version incidents.
6. **Evidence-gated recovery** — isolate only the supported object, protect checkpoints, restore on healthy resources, and verify progress.
7. **User communication** — separate user, platform, hardware, and unresolved responsibility.
8. **RCA promotion** — promote only validated findings into patterns, reproduction cases, runbooks, or detection rules.
9. **Scenario coverage** — positive, negative, and ambiguous cases for every supported failure mode.

**Exit criteria**

- Every job incident records `IMPACT_SCOPE` and `FAILURE_STAGE` before classification.
- The first anomaly and propagation chain are identified across ranks and nodes.
- Recovery verifies forward progress and a newly committed checkpoint where applicable.
- A closed incident produces a grounded user response and auditable RCA.
- Every supported failure mode passes positive, negative, and ambiguous replay cases.

### M10 — Evaluation and autonomy graduation ⬜

Incidara should expand autonomy only when measured quality and safety justify it.

**Evaluation dimensions**

- Detection precision/recall and false-action rate.
- Fault-object/entity F1.
- Root-cause agreement with adjudicated ground truth.
- Correct remediation and rollback behavior.
- Evidence completeness and claim faithfulness.
- Time to detect, triage, contain, recover, and close.
- Human approval, override, denial, and takeover rates.
- Repeat-exception rate after learning changes.
- Cost and latency per resolved incident.

**Autonomy levels**

| Level | Behavior |
|---|---|
| L0 — Observe | Collect evidence and report |
| L1 — Recommend | Diagnose and propose an action |
| L2 — Approve | Prepare the action and wait for human approval |
| L3 — Bounded autonomous | Execute known reversible actions inside a tested safety envelope |

**Graduation requirements**

- Frozen replay suite with representative positive, negative, and ambiguous cases.
- No unresolved unsafe-action regression.
- Defined blast radius, timeout, rollback, and evidence requirements.
- Canary deployment and monitored outcome window.
- Human override and exception rates below an agreed threshold.
- Immediate demotion when safety, evidence, or telemetry degrades.

**Exit criteria**

- Every capability has an explicit autonomy level instead of one system-wide label.
- Promotions and demotions are evidence-based, reviewable, and reversible.
- Production metrics distinguish agent activity from agent-caused outcomes.

---

## Delivery sequence and dependencies

```text
M0–M3 completed foundations
        │
        ├── M4 complete feedback coverage
        │
        └── M5 unified orchestration ───────────────┐
                 │                                  │
                 ▼                                  ▼
           M6 layered memory                M8 reproduction infrastructure
                 │                                  │
                 ▼                                  │
           M7 exception learning                    │
                 └──────────────────┬───────────────┘
                                    ▼
                         M9 training-job operations
                                    │
                                    ▼
                         M10 autonomy graduation
```

### Recommended execution order

1. **Orchestration first:** introduce the stable incident envelope and machine-validatable handoffs.
2. **Memory second:** unify provenance, retrieval, promotion, correction, and invalidation around the incident ID.
3. **Reproduction infrastructure in parallel:** build executors, parsers, baselines, and safety budgets while orchestration and memory mature.
4. **Exception learning next:** connect user corrections and operational exceptions to memory and replay-gated changes.
5. **Operationalize job incidents:** deploy the existing skill chain on the shared orchestration, memory, and reproduction foundations.
6. **Graduate autonomy last:** use measured replay and production outcomes to expand or reduce autonomous action.

## Cross-cutting rules

1. **Safety before autonomy.** Unknown, destructive, or expanding-blast-radius actions require a human gate.
2. **Evidence before claims.** Every diagnosis and action points to observed evidence.
3. **Rules before agents.** Deterministic, reliable, cheap detection remains rule-based; agents handle correlation and novel reasoning.
4. **One incident, one lineage.** Detection, decisions, actions, outcomes, memories, and improvements share a stable identifier.
5. **No learning without replay.** A correction becomes a regression case before changing future behavior.
6. **No promotion without counterexamples.** Fixing one case must not break known-good behavior.
7. **Inconclusive is valid.** Missing fidelity or telemetry must not become false certainty.
8. **Human corrections are first-class data.** They must be attributable, reviewable, and invalidatable.
9. **Every action has rollback or explicit irreversible approval.**
10. **Measure outcomes, not activity.** Tool calls, tasks, and tokens are operational telemetry—not proof of reliability improvement.

## Scope boundaries

The roadmap covers Incidara’s production SRE system: node reliability, training-job incidents, evidence, orchestration, memory, safe recovery, and learning.

It does not add the unrelated general analyzer/optimizer/reproducer pipeline, job-evaluation agent, inference evaluation, TCO tooling, benchmark datasets, or a replacement cluster scheduler.
