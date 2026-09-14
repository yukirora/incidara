# Incidara Roadmap

## North star

Incidara should operate two complete reliability loops—one for infrastructure nodes and one for training jobs—on a shared orchestration, evidence, memory, and safety foundation.

```text
NODE LOOP
signal → detect → triage → repair → verify → recycle → learn

JOB LOOP
signal → triage logs → diagnose system evidence → reproduce when needed
       → recover → verify progress/checkpoint → RCA → learn

SHARED LEARNING LOOP
human correction or operational outcome
       → attribution → memory/replay → rule/skill/workflow candidate
       → safety review → canary → measured outcome → keep or roll back
```

The project is complete when these loops are durable across restarts, evidence-grounded, safe under partial failure, measurable end to end, and able to improve from outcomes and human exceptions without uncontrolled behavior drift.

## How milestones are designed

A milestone is a **vertical operational capability**, not a directory or infrastructure component.

Each milestone must:

1. produce a user- or operator-visible outcome;
2. work end to end across the agents, tools, state, UI, and deployment path it needs;
3. define dependencies and non-goals;
4. include failure, retry, approval, and rollback behavior;
5. have measurable exit criteria;
6. be marked complete only when the operational gate is satisfied—not merely when code exists.

Evidence storage, permissions, deployment, CI, backup, and the Console are therefore grouped into the shared operating platform rather than presented as separate product milestones.

## Status

| Status | Meaning |
|---|---|
| ✅ Complete | End-to-end operational capability exists and its current acceptance gate is met |
| 🟡 In progress | Material implementation exists, but the end-to-end or measurement gate is incomplete |
| 🧭 Next | Highest-priority milestone needed to unlock later work |
| ⬜ Planned | Design direction exists; implementation has not reached an operational gate |

## Milestone map

| Milestone | Status | Operational outcome |
|---|---|---|
| M0 — Agent operating platform | ✅ Complete | Agents can run, use scoped tools, persist evidence, wait for approval, recover sessions, and be deployed/observed |
| M1 — Reactive node recovery | ✅ Complete | A reported or platform-cordoned node can be classified, repaired, verified, and returned to service |
| M2 — Proactive node reliability | ✅ Complete | Incidara can discover infrastructure problems before manual ticket-driven triage and route them safely |
| M3 — Outcome feedback and learning foundation | 🟡 In progress | Repair outcomes can identify bad detections/decisions and produce replay-gated improvements |
| M4 — Unified orchestration and incident lineage | 🧭 Next | One durable incident coordinates all agents, state transitions, evidence, actions, and outcomes |
| M5 — Memory and user-exception self-evolution | 🟡 In progress | Corrections and reusable experience improve future behavior without losing provenance or safety |
| M6 — Reproduction-assisted diagnosis | 🟡 In progress | Ambiguous diagnoses can be confirmed or refuted with bounded, controlled experiments |
| M7 — Node regression and preventive health | ⬜ Planned | Performance degradation is detected and corrected before it becomes a crash or job failure |
| M8 — Training-job incident lifecycle | 🟡 In progress | Training failures are triaged, diagnosed, recovered, explained, and converted into durable knowledge |
| M9 — Evaluation and safe autonomy | ⬜ Planned | Autonomy expands only when replay and production evidence demonstrate quality and safety |

---

## M0 — Agent operating platform ✅ Complete

### Operational outcome

Incidara has a deployable execution and control plane on which specialized SRE workflows can run safely and observably.

### Delivered capabilities

#### Agent runtime

- Stateful HTTP gateway around the Claude Agent SDK.
- Multiple concurrent sessions per agent.
- Session lifecycle covering start, run, tool activity, waiting for input, interruption, completion, and error.
- SSE event streaming for messages, thinking, tools, permission requests, and sub-agent activity.
- JSONL persistence, restart recovery, interrupt, and resume.
- Per-agent skill whitelists and MCP configuration.

#### Human control plane

- Incidara Console for agent discovery, tasks, sessions, and schedules.
- Persistent task queue and task/session reconciliation.
- Live transcript and tool-call visibility.
- Human approval/denial of gated operations.
- Authentication, groups, per-agent permissions, and administrative controls.
- Usage, agent, availability, reliability, job, and utilization views.

#### Tool and data plane

- Stable MCP services: `patrol-cron`, `node-operations`, `agent-evidence`, `agent-feedback`, `switch-operations`, and `feishu-bitable`.
- Role-scoped `readonly`, `diagnosis`, `ops`, and `feedback` tool sets.
- Shared investigation-evidence database.
- Platform storage/PostgreSQL SDK integration.
- Node, job, BMC, SSH, switch, ticket, alert, and user-report access paths.

#### Safety foundation

- Tool availability enforces permissions rather than relying only on prompts.
- Human gates for destructive or high-blast-radius operations.
- Evidence requirements before hardware repair/RMA actions.
- Blast-radius checks and restricted kubectl paths.
- Report-only Attention agent for unsafe, ambiguous, or blocked work.

#### Delivery and durability

- Configuration-driven 19-service Compose graph.
- Database, MCP, agent, Console, and backup service layers.
- Dependency-aware incremental CI deployment.
- Skill-only synchronization without unnecessary image rebuilds.
- Health checks, PostgreSQL pgBackRest support, and agent-state backup.

### Acceptance evidence

- Console and runtime tests/builds pass.
- MCP role and contract tests pass.
- Compose renderer produces a valid service graph.
- CI path mapping and skill-only synchronization are checked.
- Sensitive runtime state and configuration remain outside Git.

### Non-goal

M0 does not claim that every SRE workflow is complete. It establishes the reliable platform on which later milestones close operational loops.

---

## M1 — Reactive node recovery ✅ Complete

### Operational outcome

Once a node enters the unhealthy/triage workflow, Incidara can investigate it, classify it, route it through the correct repair path, and return it to service with human control over risky actions.

### End-to-end flow

```text
node alert or reported failure
  → Triage fetches node and alert context
  → evidence-based classification
       ├── hardware → Repair → approval → RMA
       ├── platform → Repair → bounded fix → validation
       └── unknown  → revalidation or Attention
  → Recycler tracks repair completion
  → node reset/configuration/reallocation
  → verified return to service
```

### Delivered capabilities

#### Triage

- Processes `triaged_unknown` and `triaged_hardware` nodes in bounded batches.
- Categorizes Xid, ECC, NVLink, IB, disk, memory, platform, and other alerts.
- Investigates using node history, alerts, jobs, SSH, kernel logs, GPU state, fabric state, and BMC evidence.
- Requires concrete evidence before hardware classification.
- Persists progress so interrupted batches can resume without repeating completed work.
- Delegates confirmed hardware/platform work to Repair.

#### Repair

- Separates hardware and platform workflows.
- Reuses evidence gathered by Triage.
- Produces vendor-safe ticket evidence without exposing workload identity.
- Requires explicit approval before reset and RMA submission.
- Executes platform fixes through scoped SSH/kubectl tools.
- Triggers validation after repair.

#### Recycler

- Polls vendor repair-ticket state.
- Moves completed repairs through `ua → ready_ua`.
- Returns rejected/cancelled/anomalous cases to investigation.
- Executes the bring-up pipeline for eligible repaired nodes.
- Reallocates only after reset, configuration, and validation steps succeed.

#### Human exception path

- Unknown evidence, failed steps, unsafe action, or expanded blast radius is surfaced through Attention/Console rather than guessed through.

### Acceptance evidence

- Triage coverage before recovery reached 100% in the proactive rollout phase.
- Unknown classification share fell from 56.1% to 4.1%.
- Hardware actions remain approval-gated.
- Node state transitions and evidence handoffs are persisted.

### Known boundary

The node lifecycle is operational, but it does not yet provide one stable incident identity across every task, finding, evidence record, action, ticket, and outcome. That is addressed by M4.

---

## M2 — Proactive node reliability ✅ Complete

### Operational outcome

Incidara can discover and investigate infrastructure risks before they depend entirely on manual ticket-driven triage.

### End-to-end flow

```text
collectors + platform alerts + switch telemetry + user reports
  → deterministic rules
  → findings with evidence and action stage
  → Detection scans gaps and unjudged findings
  → infrastructure investigation
  → verdict + bounded action or rule refinement
```

### Delivered capabilities

#### Detection engine

- Scheduled collectors for SSH, Prometheus, database queries, node/job logs, job metadata, and switch sources.
- Sandboxed rule `analyze()` execution with persisted state.
- Finding creation, update, activation, resolution, and provenance hashes.
- Rule stages from observation to task creation, alerting, and bounded automatic action.
- Live `test_rule_once` and frozen replay support.

#### Detection agent

- Scans unjudged findings and collector anomalies.
- Avoids duplicate investigations by checking evidence and active tasks.
- Investigates infrastructure findings with `inspect-infra-issue`.
- Ingests Feishu node and job reports.
- Correlates switch events with affected nodes.
- Records confirmed/rejected verdicts for rule-quality measurement.

#### Rule lifecycle

- Creates and refines rules from observed gaps.
- Uses current rule/collector hashes to separate old-code findings from current behavior.
- Demotes or contains unsafe/noisy rules instead of silently disabling learning.
- Supports replay cases and healthy counterexamples.

### Acceptance evidence

- Proactive detection operated as a production rollout phase.
- The phase was associated with 99.661% operational availability, 4.217 qualifying incidents per 1,000 node-days, and 33.1-hour P90 recovery.
- Findings, evidence, rule provenance, and verdict paths exist end to end.

### Known boundary

Many historical findings remain unjudged and direct finding-to-repair outcome linkage is incomplete. M2 establishes proactive operation; M3 establishes trustworthy learning from its outcomes.

---

## M3 — Outcome feedback and learning foundation 🟡 In progress

### Operational outcome

When a repair, RMA, or human review proves an earlier decision right or wrong, Incidara should turn that outcome into a measured improvement rather than lose it at ticket closure.

### Implemented foundation

```text
completed RMA
  → collect/classify into case_memory
  → reconcile with patrol finding
  → diagnose the failure in the original agent trajectory
  → attribute to detection, triage, inspect, repair, automation, or uncertainty
  → propose rule/skill correction
  → replay + human approval
  → merge/deploy
  → monitor
```

- `case_memory` for completed RMA outcomes.
- `analysis_problems` lifecycle from open through monitoring and resolution.
- Finding/RMA reconciliation records.
- Attribution, confidence, fix route, and feedback label.
- Frozen replay cases and positive counterexamples.
- Dirty-rule state and autonomous repair-attempt depth.
- Safe/shadow containment for repeated rule failures.
- Human-approved skill patch workflow.
- Rejected-proposal memory for unsuccessful approaches.

### Remaining work

1. Add one stable incident identity across findings, tasks, evidence, actions, tickets, and outcomes.
2. Reconcile every eligible outcome or preserve an explicit unmatched reason.
3. Measure outcome-linkage, attribution, replay, deployment, and monitoring coverage.
4. Require canary, monitoring window, rollback, and final accept/reject state for every learned change.
5. Connect user corrections and approval overrides to the same attribution/replay path.
6. Verify that accepted changes reduce the target exception without harming healthy cases.

### Dependencies

- Uses M0 data/tool/control plane.
- Learns from M1 and M2 outcomes.
- Full closure depends on M4 incident lineage and M5 memory semantics.

### Exit criteria

- Every eligible operational outcome has a linked or explicitly unmatched state.
- Every correction has a failed case, healthy counterexample, owner, and target artifact.
- No rule/skill change graduates without replay, safety review, approval, canary, and monitored outcome.
- Failed changes roll back and enter rejected-proposal memory.
- Feedback quality is reported as coverage and outcome improvement, not number of patches.

**System reference:** [Feedback agent](incidara-agent-sre.md#225-feedback-agent)

---

## M4 — Unified orchestration and incident lineage 🧭 Next

### Operational outcome

One durable incident should coordinate work across agents and survive retries, restarts, partial failure, and human intervention without losing context or repeating actions.

### Why this is next

Today, Console tasks, gateway sessions, skill instructions, MCP delegation, and working files coordinate the workflows. They work, but no single record owns the full lifecycle. Memory, self-evolution, job incidents, and causal evaluation all need this lineage first.

### Deliverables

#### Unified incident envelope

- Stable `incident_id` for node and job incidents.
- Source, target, impact scope, failure stage, severity, current state, and current owner.
- Evidence, hypothesis, action, approval, rollback, outcome, memory, and improvement references.
- Links to alert, finding, task, gateway session, job, validation, and ticket identifiers.

#### State machine

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
- Idempotency key for every state-changing operation.
- Timeout, retry budget, cancellation, and compensation rules.
- Explicit terminal and reopen semantics.

#### Typed handoffs

- Required evidence and proof limit.
- Supported/refuted/inconclusive hypotheses.
- Next owner, requested action, safety boundary, and completion condition.
- Machine rejection when mandatory context is absent.

#### Orchestration visibility

- One Console timeline spanning multiple agents and tasks.
- Current owner, next action, pending approval, and blocked reason.
- Duplicate/stalled workflow detection.
- Operator takeover and return-to-agent semantics.

#### Recovery behavior

- Resume from the last committed transition after agent/host restart.
- Prevent duplicate repair, RMA, stop-job, and reallocation actions.
- Preserve partial results and compensating actions.
- Escalate expanding blast radius or unavailable evidence to Attention.

### Dependencies

- Builds on M0.
- Wraps the existing M1–M3 workflows without replacing their domain logic.

### Exit criteria

- A node or job incident is traceable through one identifier from signal to learning.
- Restarting any component does not lose ownership or repeat a destructive action.
- Every handoff is schema-valid and visible.
- Timeout, duplicate, invalid transition, and partial failure paths are tested.
- Human takeover preserves lineage and can safely return control.

---

## M5 — Memory and user-exception self-evolution 🟡 In progress

### Operational outcome

Incidara should remember relevant experience, learn from explicit corrections and exceptions, and improve future behavior without creating untraceable or unsafe drift.

### Existing memory assets

| Memory layer | Existing store | Current use |
|---|---|---|
| Working | Gateway events and agent working files | Current execution and resume |
| Evidence | `investigation_evidence` | Raw observations and artifacts |
| Episodic | `case_memory`, findings, tickets, incident history | What happened in one case |
| Improvement | `analysis_problems`, replay cases, rejected proposals | What was changed and whether it worked |
| Semantic | `agent_memory` | Reusable, human-corrected insights |
| Procedural | Versioned rules and skills | Future operational behavior |

### Exception sources to support

- User correction of classification, diagnosis, scope, or remediation.
- Approval denial, modification, or human override.
- RMA no-fault-found, misclassification, rejection, or withdrawal.
- Failed repair, rollback, validation, or reallocation.
- Repeated unresolved incident or false positive.
- Missing telemetry, unsupported hardware, policy conflict, or stale memory.

### Deliverables

1. Common provenance: incident, evidence links, author, confidence, model/skill/rule version, time, and validity.
2. Retrieval scoped by agent, target, fault class, SKU, software version, topology, and time window.
3. Promotion path: evidence → episode → validated insight → candidate rule/skill/workflow/policy change.
4. Correction, invalidation, supersession, contradiction, and retention rules.
5. Memory-use logging showing which retrieved items influenced a decision.
6. Structured exception capture containing the original decision, available evidence, human correction, stage attribution, and final outcome.
7. Routing to the owning rule, skill, tool, workflow, memory, or policy.
8. Replay-gated candidate generation, canary rollout, monitoring, rollback, and rejected-candidate recording.

### Dependencies

- M3 supplies outcome attribution and improvement workflow.
- M4 supplies stable incident lineage and ownership.

### Exit criteria

- Every retrieved memory is relevant, attributable, versioned, and visible in the incident record.
- Incorrect memory can be invalidated and stops affecting new decisions.
- A user correction becomes a durable replay case rather than disappearing in chat.
- No self-evolution reaches production without evidence, counterexample, approval, canary, and rollback.
- Repeat-exception rate falls after accepted changes, or the change is rejected and remembered.

---

## M6 — Reproduction-assisted diagnosis 🟡 In progress

### Operational outcome

When passive evidence cannot distinguish plausible causes, Incidara should run the smallest safe experiment that can support or refute the decision.

### Implemented foundation

- Failure-mode reproduction skills for job hang, NCCL/RCCL timeout, GPU OOM, NaN loss, slowdown, jitter, checkpoint failure, and process crash.
- Minimum complete-node planning for large/MoE experiments.
- Hypothesis-specific fixed variables, changed variable, expected signal, healthy guard, proof limit, and next action.
- Node diagnostic, validation-job, SSH, BMC, Prometheus, and log tools.
- Contract tests for cross-skill failure-mode IDs and handoffs.

### Target method

```text
CORRELATE
  reconstruct the incident and compare healthy controls
      ↓
CONFIRM
  run a bounded diagnostic against the suspected component
      ↓
REPRODUCE
  change one relevant variable under equal exposure
      ↓
VERIFY
  check healthy guards, recovery, recurrence, and cleanup
```

### Remaining deliverables

- `run_diagnostic_job(nodes, benchmark, parameters)` MCP operation.
- Executors for DCGM, DGEMM, GPU memory, NVLink bandwidth, NCCL, storage, and workload replay.
- Structured parsers and durable result/artifact storage.
- Per-SKU and per-software-version healthy baselines.
- Equal-exposure healthy control selection.
- Resource budget, timeout, cancellation, cleanup, and blast-radius enforcement.
- Incident-linked support/refute/inconclusive outcomes.
- Validation of proposed recovery, rule, skill, and automation changes.

### Dependencies

- M4 supplies incident state, experiment ownership, and idempotency.
- M0 supplies tools, approval, scheduling, and evidence storage.
- Can advance in parallel with M5 after the incident envelope stabilizes.

### Exit criteria

- Every experiment has a hypothesis, fixed/changed variables, expected observation, safety budget, and cleanup plan.
- Results are structured and linked to the incident.
- “Did not reproduce” remains inconclusive unless fidelity was sufficient.
- Reproduction can change diagnosis confidence but cannot silently expand impact.
- Diagnostic actions and cleanup are safe under timeout and partial failure.

---

## M7 — Node regression and preventive health ⬜ Planned

### Operational outcome

Incidara detects degraded GPU nodes and interconnects before they crash a training job.

### Deliverables

- Scheduled per-SKU health and performance diagnostics.
- Versioned baselines for GPU compute, HBM, PCIe, NVLink, IB, storage, CPU, and memory.
- Baselines conditioned on SKU, firmware, driver, topology, and workload mode.
- Detection rules for sustained and intermittent regression.
- Classification into hardware degradation, firmware/driver, configuration, contention, or measurement error.
- M6 confirmation/reproduction before destructive action where appropriate.
- Safe containment, repair routing, revalidation, and return-to-service criteria.
- Fleet/rack trend detection for shared degradation.

### Dependencies

- M2 collector/rule lifecycle.
- M4 incident lineage.
- M6 diagnostic executors, parsers, and baselines.
- M5 memory for version-aware known-good comparisons.

### Exit criteria

- Known injected degradation is detected at an agreed recall with bounded false-action rate.
- Baseline drift is versioned and reviewable.
- A detected regression can be confirmed, routed, repaired, and revalidated end to end.
- No node is removed solely because of a single noisy benchmark sample.

---

## M8 — Training-job incident lifecycle 🟡 In progress

### Operational outcome

A failed, hung, degraded, or incorrect training job is classified, diagnosed across ranks and infrastructure, safely recovered, explained to the user, and converted into reusable knowledge.

### Existing skill chain

```text
job-incident-response
  → job-log-triage
  → system-evidence-diagnosis
  → job-recovery
  → training-reproduction
  → rca-closeout
```

### Covered failure modes

- Startup/scheduling failure
- Process crash
- GPU out-of-memory
- Job hang
- NCCL/RCCL timeout
- NaN/non-finite loss
- Throughput slowdown
- Throughput jitter
- Checkpoint failure

Each complex incident is designed to establish `IMPACT_SCOPE` and `FAILURE_STAGE`, identify the first anomaly rather than propagated errors, and carry supported/refuted hypotheses between stages. Detection routes job-failure reports to `job-incident-response` and installs the complete skill chain.

### Remaining deliverables

#### Productize the workflow

- Bind the skill chain to a dedicated agent gateway profile rather than sharing Detection.
- Register that dedicated profile in Compose and the Console.

#### Incident ingestion

- Create job incidents from platform events, stalled progress, user reports, and patrol findings.
- Deduplicate repeated reports for the same job/attempt/failure boundary.

#### Unified job evidence

- Join job/attempt, task role, rank, PID, GPU, node, rack/rail, fabric path, checkpoint, image, and software version.
- Preserve the first failure and subsequent propagation across ranks.
- Compare other jobs sharing the same infrastructure and time window.

#### Evidence-gated recovery

- Isolate only the supported object.
- Protect or validate the last trusted checkpoint.
- Restore on allowed healthy resources.
- Verify forward progress and a newly committed checkpoint.
- Roll back or escalate when recovery does not progress.

#### Closeout and learning

- Grounded user response separating user, platform, hardware, and unresolved responsibility.
- Auditable RCA covering trigger, cause, propagation, isolation, recovery, and proof limit.
- Promote only validated findings to memory, runbooks, reproduction cases, rules, or automation candidates.

### Dependencies

- M4 for durable multi-stage orchestration.
- M5 for cross-incident memory and user corrections.
- M6 for ambiguous/complex diagnosis and validation.
- M2 for proactive job signals through `job-patrol`.

### Exit criteria

- Every incident records impact scope and failure stage before root-cause classification.
- Cross-rank first failure and propagation are distinguishable.
- Recovery verifies progress and checkpoint durability where applicable.
- User communication is grounded and ownership-specific.
- Every supported failure mode passes positive, negative, and ambiguous replay cases.
- The service reports diagnosis accuracy, recovery success, human intervention, time, and cost.

---

## M9 — Evaluation and safe autonomy ⬜ Planned

### Operational outcome

Incidara expands autonomy only for capabilities whose replay and production evidence demonstrate that the action is accurate, safe, reversible, and cheaper than human execution.

### Evaluation dimensions

- Detection precision, recall, and false-action rate.
- Fault-object/entity identification.
- Root-cause agreement with adjudicated ground truth.
- Remediation correctness, verification, and rollback.
- Evidence completeness and claim faithfulness.
- Time to detect, triage, contain, recover, and close.
- Human approval, denial, override, correction, and takeover rates.
- Repeat-exception rate after learning changes.
- Cost and latency per resolved incident.
- Finding-to-action-to-outcome linkage coverage.

### Capability-specific autonomy

| Level | Behavior |
|---|---|
| L0 — Observe | Collect and report evidence |
| L1 — Recommend | Diagnose and propose an action |
| L2 — Approve | Prepare the action and wait for human approval |
| L3 — Bounded autonomous | Execute known reversible actions inside a tested safety envelope |

### Deliverables

- Frozen scenario/replay suites for node and job incidents.
- Positive, negative, ambiguous, timeout, partial-failure, and rollback cases.
- Independent or blinded adjudication for critical labels.
- Stable incident linkage for causal attribution.
- Canary policies and automatic demotion when quality or telemetry degrades.
- Per-capability dashboards for safety, quality, intervention, outcome, and cost.
- Matched-control or staggered rollout designs where causal claims matter.

### Dependencies

- Uses the lineage, memory, exception, reproduction, node-regression, and job-incident outcomes from M3–M8.

### Exit criteria

- Every capability has its own measured autonomy level.
- Promotion and demotion are evidence-based, reviewable, and reversible.
- No unresolved unsafe-action regression exists at promotion time.
- Production reporting distinguishes agent activity from agent-caused outcome.
- Safety incidents automatically demote the affected capability and trigger review.

---

## Delivery logic

```text
                         ┌──────────────────────────┐
                         │ M0 Agent platform ✅     │
                         └────────────┬─────────────┘
                                      │
                         ┌────────────▼─────────────┐
                         │ M1 Reactive nodes ✅     │
                         └────────────┬─────────────┘
                                      │
                         ┌────────────▼─────────────┐
                         │ M2 Proactive nodes ✅    │
                         └───────┬──────────┬───────┘
                                 │          │
                    ┌────────────▼───┐  ┌───▼────────────────┐
                    │ M3 Feedback 🟡 │  │ M4 Orchestration 🧭│
                    └────────┬───────┘  └────────┬───────────┘
                             └──────────┬─────────┘
                                        │
                       ┌────────────────┼────────────────┐
                       │                │                │
              ┌────────▼──────┐ ┌──────▼────────┐ ┌────▼─────────────┐
              │ M5 Memory 🟡  │ │ M6 Repro 🟡   │ │ M7 Regression ⬜ │
              └────────┬──────┘ └──────┬────────┘ └────┬─────────────┘
                       └────────────┬───┴────────────────┘
                                    │
                         ┌──────────▼───────────┐
                         │ M8 Job incidents 🟡 │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │ M9 Safe autonomy ⬜  │
                         └──────────────────────┘
```

### Recommended execution order

1. **Finish M3 measurement while designing M4.** Outcome coverage and incident lineage should converge on the same identifiers.
2. **Implement M4 next.** Orchestration is the dependency shared by memory, reproduction, job incidents, and attribution.
3. **Advance M5 and M6 in parallel.** Both consume the incident envelope but solve different problems.
4. **Build M7 on M6.** Preventive regression detection requires trustworthy diagnostics and baselines.
5. **Operationalize M8 after M4, using M5/M6 incrementally.** The existing skills provide domain content; orchestration makes them a reliable service.
6. **Use M9 gates throughout, but graduate autonomy last.** Measurement begins early; permission expansion waits for evidence.

## Cross-cutting completion rules

These gates apply to every milestone:

1. **Evidence before claims.** Every diagnosis and action points to observed evidence.
2. **Safety before autonomy.** Unknown or high-impact actions wait for a human.
3. **Rules before agents.** Deterministic detection stays deterministic; agents handle correlation and novel reasoning.
4. **One incident, one lineage.** Signals, decisions, actions, outcomes, memories, and improvements share an identity.
5. **No learning without replay.** A correction becomes a regression case before future behavior changes.
6. **No fix without counterexamples.** Correcting one failure must preserve known-good behavior.
7. **Inconclusive is valid.** Missing fidelity or telemetry must not become false certainty.
8. **Human exceptions are first-class data.** Corrections and overrides are attributable, reviewable, and invalidatable.
9. **Every action has rollback—or explicit irreversible approval.**
10. **Measure outcomes, not activity.** Tasks, tools, and tokens are telemetry, not proof of reliability improvement.

## Scope boundary

This roadmap covers Incidara’s production SRE system: node reliability, training-job incidents, orchestration, evidence, memory, safe action, recovery, and learning.

It does not include the unrelated general analyzer/optimizer/reproducer pipeline, job-evaluation agent, inference evaluation, TCO tooling, benchmark datasets, or a replacement cluster scheduler.
