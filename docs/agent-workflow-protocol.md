# Design: Agent Workflow Protocol

## Reader And Goal

This document is for internal engineers building or reviewing multi-agent workflows in incidara.

After reading it, the reader should be able to decide whether to build the proposed DB-backed workflow protocol, understand the minimum runtime model, and plan the first implementation slice for feedback optimization.

## Problem

Current agent-to-agent work is mostly a prompt handoff:

```text
agent A investigates
agent A writes a prompt
agent A calls delegate_to_agent(agent B)
agent B interprets the prompt
```

This is too weak for workflows where the correct fix may require several layers, several attempts, or evidence-based tradeoffs.

The detection feedback loop exposed the issue. A bad RMA outcome may be caused by:

- detection rule logic
- triage or inspect judgment
- repair workflow
- broken tools or automation
- several of the above

Choosing one next agent is not enough. A detection rule update may reduce noise but fail to preserve positive examples. A triage guard may be safer but cost more work. Some cases require both. The system needs to record candidate fixes, evaluate them, reject bad candidates with evidence, and then select an implementation plan.

## Design Principle

Delegation should be an implementation detail of a durable workflow, not the workflow itself.

Agents should not pass responsibility through unstructured prose. They should operate on a shared workflow case with explicit state:

```text
workflow case
  -> evidence
  -> candidate fixes
  -> evaluations
  -> selected plan
  -> delegated tasks
  -> validation and monitoring outcome
```

The workflow runtime owns state transitions. Agents own bounded work units such as diagnosis, candidate evaluation, implementation, or validation.

## Non-Goals

This is not a full workflow engine.

The first version should not implement arbitrary DAG scheduling, retries, timers, permissions, or a visual workflow editor. It should provide enough durable structure for agents to coordinate safely while still using existing Chat UI tasks and MCP tools.

## Proposed Model

### Workflow Case

A workflow case is the durable coordination object.

Example:

```json
{
  "workflow_id": "wf_123",
  "workflow_type": "feedback_optimization",
  "problem_id": 1,
  "objective": "Prevent false RMA from transient XID while preserving hard failure detection.",
  "state": "diagnosing",
  "primary_entity": {
    "type": "rule",
    "id": "feedback_transient_xid_v1"
  }
}
```

Suggested states:

| State | Meaning |
|---|---|
| `open` | Case exists but no diagnosis has been accepted yet |
| `diagnosing` | Agent is gathering evidence and identifying failed layers |
| `evaluating` | Candidate fixes are being tested |
| `planning` | Candidate outcomes are known and a fix plan is being selected |
| `implementing` | Selected implementation task is running |
| `validating` | Implementation exists and must be checked |
| `monitoring` | Fix shipped or applied; waiting for production signal |
| `blocked` | Required evidence, tool, or approval is missing |
| `done` | Case is resolved |

### Candidate Fix

A candidate fix is a hypothesis about where and how to prevent the bad outcome.

Example:

```json
{
  "candidate_id": "cand_detection_1",
  "workflow_id": "wf_123",
  "layer": "detection",
  "owner_agent": "feedback-test",
  "hypothesis": "Suppress transient_xid-only findings at rule level.",
  "expected_effect": "Stops false RMA from transient-only XID.",
  "risk": "May hide early hard failures if transient XID is the only early signal.",
  "evaluation_type": "rule_replay",
  "status": "proposed"
}
```

Candidate status:

| Status | Meaning |
|---|---|
| `proposed` | Candidate has been identified but not tested |
| `evaluating` | An agent or tool is testing this candidate |
| `selected` | Candidate is part of the chosen plan |
| `rejected` | Candidate failed or is unsafe |
| `implemented` | Candidate implementation has been applied |
| `validated` | Candidate passed its validation gate |

Rejection is a first-class outcome. For example:

```json
{
  "status": "rejected",
  "rejection_reason": "Collector payload cannot separate negative transient XID from positive early hard-failure example."
}
```

### Selected Plan

The selected plan is an ordered list of chosen actions. It can contain one or more candidates.

Example:

```json
{
  "selected_plan": [
    {
      "candidate_id": "cand_triage_1",
      "owner_agent": "feedback",
      "action": "Patch triage skill to require recurrence or hard evidence before RMA."
    },
    {
      "candidate_id": "cand_detection_2",
      "owner_agent": "feedback-test",
      "action": "Demote transient-only detection to log_only until stronger signal exists."
    }
  ]
}
```

This lets a workflow represent layered fixes instead of forcing a single route.

### Workflow Task

A workflow task links the durable workflow state to an actual Chat UI task.

```json
{
  "workflow_id": "wf_123",
  "candidate_id": "cand_detection_1",
  "task_id": 5488,
  "agent_id": "feedback-test",
  "role": "evaluate_candidate",
  "status": "running"
}
```

The task prompt should be generated from structured workflow state. It should not be the source of truth.

## Protocol

The runtime should support this protocol:

```text
1. create workflow case
2. attach evidence
3. diagnose failed layers
4. propose candidate fixes
5. evaluate candidates
6. select plan
7. create implementation tasks
8. validate implementation
9. monitor outcome
10. close or reopen
```

Agents participate by updating workflow state through MCP tools.

### Diagnosis

Diagnosis identifies the bad outcome and possible failed layers. It should not commit to a single owner too early.

Output:

```json
{
  "bad_outcome": "false_hardware_rma",
  "failed_layers": [
    {
      "layer": "detection",
      "confidence": "high",
      "reason": "Rule fired on transient-only XID that returned NFF."
    },
    {
      "layer": "triage",
      "confidence": "medium",
      "reason": "Triage may have accepted the finding without recurrence or hard evidence."
    }
  ]
}
```

### Candidate Evaluation

Each candidate has an evaluation type appropriate to its layer:

| Layer | Evaluation |
|---|---|
| detection | Rule replay suite plus live dry run |
| triage or inspect | Scenario acceptance test with expected decision |
| repair | Ticket/revalidation scenario |
| automation | Tool contract test or workflow simulation |
| tooling | Unit/integration test for the tool capability |

The evaluation should answer:

- Does this candidate prevent the bad outcome?
- Does it preserve required positive behavior?
- What risk remains?
- Is another layer still needed?

### Plan Selection

Plan selection chooses the cheapest reliable fix, not simply the first plausible owner.

Recommended ordering:

1. Prefer detection when the bad behavior is separable from collector input and replay preserves positives.
2. Prefer triage or inspect when the correct decision needs history, logs, recurrence, or cross-source context.
3. Prefer repair when triage was correct but ticket content, action, or revalidation was wrong.
4. Prefer tooling or automation when a missing or broken capability blocked the right decision.

Multiple candidates may be selected when one layer reduces noise but another layer is needed to prevent high-impact mistakes.

## Feedback Optimization Example

Transient XID feedback should not be modeled as:

```text
case-diagnosis -> automate-detection-pattern
```

It should be modeled as:

```text
workflow case: false RMA from transient XID
  evidence:
    - negative RMA: transient_xid, NO_FAULT_FOUND
    - positive guard: hard_failure, REPAIR_CONFIRMED
  candidates:
    - detection: suppress transient_xid-only
    - triage: require recurrence/hard evidence before RMA
  evaluation:
    - detection replay must pass negative and positive cases
    - triage scenario must reject RMA while preserving visibility
  selected plan:
    - choose detection if replay is safe
    - otherwise choose triage
    - choose both if detection can reduce noise but not fully prevent bad RMA
```

This gives the system a memory of why a candidate was selected or rejected.

## MCP Surface

The first version can be small.

Suggested tools:

```text
create_workflow_case(workflow_type, objective, problem_id, primary_entity)
get_workflow_case(workflow_id)
append_workflow_evidence(workflow_id, evidence)
propose_candidate_fix(workflow_id, layer, owner_agent, hypothesis, risk, evaluation_type)
update_candidate_status(candidate_id, status, result, rejection_reason)
select_workflow_plan(workflow_id, candidate_ids, rationale)
create_workflow_task(workflow_id, candidate_id, agent_id, role, title, prompt)
complete_workflow_task(workflow_task_id, status, summary)
update_workflow_state(workflow_id, state, reason)
```

The tool that creates workflow tasks can call the existing delegation mechanism internally. The workflow task record should store the returned Chat UI session and task IDs.

## Storage

A minimal schema:

```sql
CREATE TABLE workflow_cases (
    workflow_id BIGSERIAL PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    problem_id INT NULL,
    objective TEXT NOT NULL,
    state TEXT NOT NULL,
    primary_entity JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE workflow_candidates (
    candidate_id BIGSERIAL PRIMARY KEY,
    workflow_id BIGINT NOT NULL REFERENCES workflow_cases(workflow_id),
    layer TEXT NOT NULL,
    owner_agent TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    risk TEXT NULL,
    evaluation_type TEXT NOT NULL,
    status TEXT NOT NULL,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    rejection_reason TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE workflow_evidence (
    evidence_id BIGSERIAL PRIMARY KEY,
    workflow_id BIGINT NOT NULL REFERENCES workflow_cases(workflow_id),
    source_type TEXT NOT NULL,
    source_id TEXT NULL,
    summary TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE workflow_tasks (
    workflow_task_id BIGSERIAL PRIMARY KEY,
    workflow_id BIGINT NOT NULL REFERENCES workflow_cases(workflow_id),
    candidate_id BIGINT NULL REFERENCES workflow_candidates(candidate_id),
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL,
    chat_session_id INT NULL,
    chat_task_id INT NULL,
    status TEXT NOT NULL,
    summary TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE workflow_events (
    event_id BIGSERIAL PRIMARY KEY,
    workflow_id BIGINT NOT NULL REFERENCES workflow_cases(workflow_id),
    actor TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Open question: this can live in the existing feedback database at first, but it is general enough to become a separate workflow MCP server later.

## Relationship To Existing Feedback Tables

The workflow protocol should not replace existing domain tables.

Existing tables keep their domain meaning:

- `analysis_problems` tracks problem discovery, status, and human-readable diagnosis history.
- `rma_finding_reconciliations` maps RMA outcomes to patrol findings and stores attribution labels.
- `rule_replay_cases` stores frozen rule regression fixtures.
- `rule_reconciliation_state` tracks whether a rule still has unresolved detection feedback.

Workflow tables coordinate cross-agent execution:

- which candidates were considered
- which candidate was rejected or selected
- which task implemented or evaluated a candidate
- whether the workflow is blocked, monitoring, or done

For feedback optimization, `analysis_problems.problem_id` should be linked from `workflow_cases.problem_id`. The workflow record becomes the execution state; `analysis_problems` remains the problem-facing history and reporting surface.

## Prompt Contract

Delegated prompts should be generated from workflow state and should include:

- workflow ID
- candidate ID, if applicable
- role for this task
- exact inputs
- allowed outputs
- required MCP updates

Example:

```text
Evaluate candidate cand_detection_1 for workflow wf_123.

Role: evaluate_candidate
Layer: detection
Hypothesis: suppress transient_xid-only findings at rule level.

Required:
- Run rule replay against negative and positive feedback cases.
- Do not update rule code.
- Update candidate status to selected or rejected with evidence.
```

The prompt should not contain pre-baked conclusions such as "this is detection-attributed with high confidence" unless that conclusion has already been recorded as evidence by an earlier workflow step.

## Why Not Skill-Only

Skill-only workflows are easy to start but hard to trust:

- prompts can accidentally include conclusions
- agents can skip positive cases
- no durable record of rejected candidates
- no standard way to resume after interruption
- no dependency tracking between tasks
- no way to compare outcomes across workflows

The runtime protocol does not replace skills. It gives skills a shared state machine.

## Rollout Plan

### Slice 1: Feedback Optimization Coordination

Implement workflow cases and candidates only for feedback optimization.

Minimum viable behavior:

1. analyze-rma-cases creates a workflow case.
2. case-diagnosis records failed layers and proposes candidates.
3. detection candidate evaluation records pass/fail from replay.
4. selected plan is persisted.
5. implementation task is delegated through the workflow task tool.

### Slice 2: Scenario Evaluation

Add non-detection candidate evaluation:

- triage scenario acceptance
- inspect scenario acceptance
- repair ticket/revalidation scenario
- tooling contract tests

### Slice 3: Outcome Monitoring

Add monitoring state:

- expected metric
- monitoring window
- new evidence after fix
- close or reopen decision

### Slice 4: Reuse Beyond Feedback

Use the same protocol for:

- attention queue investigations
- repeated tool failures
- recycler burst failures
- detection rollout and promotion
- repair automation improvements

## Success Criteria

The protocol is working when:

- a workflow can represent multiple candidate fix layers
- rejected candidates are recorded with evidence
- selected plans can contain more than one implementation step
- delegated tasks are linked to the workflow and candidate they serve
- validation can determine whether the workflow is done, blocked, or needs another candidate
- feedback optimization no longer depends on one agent writing a perfect handoff prompt

## Open Decisions

1. Should the workflow tables live in the existing feedback database first, or in a new workflow MCP server?
2. Should candidate evaluation be initiated by the coordinating agent, or by a lightweight scheduler that watches workflow state?
3. Should selected plans require human approval before implementation, or only for high-impact actions?
4. What is the minimum scenario test format for triage, inspect, and repair candidates?
5. How should workflow outcomes feed back into agent attention reports?
