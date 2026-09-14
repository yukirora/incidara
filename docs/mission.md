# Incidara — Mission, Value, and Design Principles

> **Companion docs.** [`incidara-agent-sre.md`](incidara-agent-sre.md) describes the implemented architecture and production evaluation. [`detection-feedback-loop.md`](detection-feedback-loop.md) describes how operational outcomes improve rules and skills. This document explains the mission, value proposition, north-star metrics, and design principles.

---

## 1. Mission

Build an autonomous agent system that keeps a large GPU cluster healthy and its training jobs successful — by absorbing the operational load currently borne by on-call SREs, converting every incident into permanent institutional knowledge, and providing a safe, auditable, explainable path from raw signal to resolution.

**Success looks like:** a fleet where the majority of node and job failures are detected, classified, diagnosed, and resolved without waking anyone up; where every novel failure becomes a permanent regression test the next day; where ML users see the cluster as reliable infrastructure, not a source of debugging work; and where SREs spend their time on truly novel problems, not routine triage.

---

## 2. The Problem This Solves

Large GPU clusters live in an uncomfortable middle:

1. **Rules detect but cannot diagnose.** Xid alerts, ECC counters, NVLink flap thresholds — these fire, but the answer to "what actually happened and what should we do?" still requires a human to correlate across dmesg, switch counters, Prometheus, and job logs.
2. **Humans diagnose but do not scale.** A senior SRE can chase an NCCL hang across a 10-node job in an hour. That same SRE cannot chase 30 of them concurrently. Cluster capacity outstrips human capacity to reason.
3. **Knowledge dies at ticket close.** The correlation an SRE built in their head between "Xid 63 + loss spike + specific host" and "SDC" evaporates when the ticket closes. The next occurrence is diagnosed from scratch.
4. **Novel failures compound.** Every generation of hardware (H200 → B300) brings new failure modes. The rule set is always trailing the fleet.
5. **User productivity is silently taxed.** ML users blame the cluster for failures that are their code, and blame their code for failures that are the cluster — and burn days figuring out which.

The observable consequences: fleet capacity wasted on undiagnosed degraded nodes; on-call burnout; a growing backlog of "unknown-cause" incidents; users who lose trust in the platform.

Rules alone cannot close this gap. Humans alone cannot scale. **An agent solution is the connective tissue** between rules (which perceive), humans (who resolve novel cases), and knowledge (which must accumulate).

---

## 3. Why an Agent — And Not Just More Rules

A durable comparison, cell by cell:

| Capability | Rule-based system | LLM-agent system |
|---|---|---|
| Detect known signal | ✅ Fast, cheap, deterministic | ✅ (uses rules as tools) |
| Correlate across sources (dmesg × Prometheus × switch × job logs) | ❌ Combinatorial rule explosion | ✅ LLM reasoning + tool composition |
| Diagnose novel failure modes | ❌ Requires new rule + release | ✅ Reasons from runbooks + first principles |
| Actively reproduce (submit diagnostic job, parse result) | ❌ Not in rule scope | ✅ Tool use + iteration |
| Explain in on-call language | ❌ | ✅ |
| Hand off (right team, right severity) | ⚠️ Static routing | ✅ Context-aware routing |
| Learn from feedback | ❌ Requires human rule authoring | ✅ Trajectory + rubric → new scenarios |
| Compose with other skills / tools | ⚠️ Only pre-planned pipelines | ✅ Iterative composition |
| Absorb domain runbooks & postmortems | ❌ Not applicable | ✅ Retrieval + reasoning |
| Cost & latency | Very low | Higher — must be justified per-task |
| Auditable determinism | ✅ | ⚠️ Requires trajectory logging + policy layer |
| Safety of actions | Bound by rule scope | Requires explicit blast-radius policy |

**Design implication.** The agent does not replace rules; it *layers on top of them*. Rules remain the fast, cheap, first-line perception. The agent is invoked for correlation, diagnosis, reproduction, explanation, and — increasingly — action.

---

## 4. Value Dimensions (Product Axes)

The system is organized around five core capabilities. Each is a value dimension that can be measured, improved, and shipped independently. Every roadmap item slots into one of these.

### 4.1 Perceive & Correlate

*See everything the cluster is telling us — across time, source, and layer.*

- Rules and signals as **tools** the agent can call: patrol-cron rules, dmesg/Xid, switch counters, NVLink telemetry, IB fabric errors, Prometheus, OpenPAI events, job logs.
- Time-window correlation: given `(node, time_window)`, pull every relevant signal and reason about them jointly.
- Cross-source causal hypothesis: a switch error at T0 that precedes an NCCL timeout at T0+30s is a hypothesis worth pursuing.

**Value:** turn a flood of independent alerts into a small number of coherent hypotheses.

### 4.2 Diagnose & Reproduce

*Go beyond "something is wrong" to "here's what and why, and here's how you can see it too."*

- Structured root-cause writeup: what failed, on what node, at what time, with what evidence.
- Active **confirmation**: run a targeted DCGM diag / DGEMM / memtest / NVLink BW test / NCCL bench on the suspect component.
- Active **reproduction**: design a minimal workload that reliably triggers or measures the issue.
- Distinct methodology as first-class practice (see [the system report](incidara-agent-sre.md) §Methodology): `correlate → confirm → reproduce`.

**Value:** every diagnosis is defensible. No "the agent thinks it might be…" — instead, "the agent ran this diagnostic and observed this result."

### 4.3 Act & Contain

*Take safe, bounded, reversible operational actions — and cleanly escalate the rest.*

- Reversible actions autonomously (cordon, drain, restart-friendly containers).
- Irreversible actions gated on human approval or clear policy (RMA, firmware flash, cluster-wide config change).
- Every action has a declared **blast radius** and is logged with pre/post state.
- Explicit escalation policy: unknown class + high impact → page; known class + low impact → autonomous.

**Value:** the fleet is protected. Humans are not woken up for routine known-class actions. Novel/critical actions still get human sign-off.

### 4.4 Learn & Curate

*Every incident becomes durable knowledge.*

- Every resolved incident is a candidate for the validation suite (see [the system report](incidara-agent-sre.md) §3.3).
- Runbook / postmortem knowledge is retrievable by the agent, not just by humans.
- Failure modes get named references (e.g., `references/xid-reference.md`, planned `nccl-timeout.md`, `silent-data-corruption.md`, `nvlink-degradation.md`, `ib-fabric.md`).
- Suite grows monotonically; solved scenarios rotate to smoke tier.

**Value:** the second time a failure happens, resolution is faster than the first. The tenth time, it is automatic.

### 4.5 Explain & Hand Off

*Never leave a human confused about what the agent did or is asking for.*

- Every agent output is written for the human recipient — on-call SRE, ML user, platform lead — not for other agents.
- Explanations are grounded (see [the system report](incidara-agent-sre.md) §6.3 faithfulness facet). No unsupported claims.
- Handoffs carry full context: what was observed, what was tried, what the agent believes, what it needs.
- User-facing replies distinguish "your job was killed for X reason" (user-fault) from "your job hit a hardware issue — no action required from you" (platform-fault).

**Value:** trust. Human recipients treat the agent as a competent colleague, not an alert firehose.

---

## 5. Goals and North-Star Metrics

Each value dimension has a small set of measurable outcomes. These are what a leadership dashboard should show, distinct from the internal-quality metrics defined in the evaluation survey.

### 5.1 Fleet Health

- **Undetected degraded-node rate** — nodes running in production while degraded and unreported. Target: → 0.
- **Mean time bad-node in fleet** — from first symptom to cordon. Target: hours.
- **Bad-node backlog** — nodes cordoned but awaiting repair diagnosis. Target: bounded and shrinking.
- **Fleet-average per-SKU performance** — DGEMM / NVLink BW versus baseline. Target: within X% of spec.

### 5.2 Job Success

- **Job success rate (excluding user-fault)** — trend upward.
- **Hardware-caused job crash rate** — trend downward as detection + preemptive cordon improve.
- **User-fault misclassification rate** — jobs the agent incorrectly told the user were their fault, later found to be platform. Target: → 0.
- **MTTD from cluster event to job-triage completion.**

### 5.3 Human Load

- **On-call pages/week** — trend downward.
- **Toil hours displaced** — estimated from agent-executed actions × human-baseline time.
- **Novel-incident share** — % of incidents the agent could not handle end-to-end. This is expected to trend down, then stabilize as fleet evolution introduces new modes.
- **User self-serve rate** — % of user questions the agent resolves without SRE involvement.

### 5.4 Coverage and Autonomy

- **End-to-end handled rate** — incidents where the agent completed detect → classify → diagnose → (reproduce | fix suggestion) without human. Break down by module (per [the system report](incidara-agent-sre.md)).
- **Autonomy-level distribution** — what fraction of the fleet's ops volume is handled at L1 vs. L2 vs. L3.
- **Repro-and-confirm rate** — of diagnoses, what fraction was actively confirmed (not just correlated).

### 5.5 Trust and Safety

- **Silent-failure rate** — from online review (see eval survey §6.4). Target: near 0 and monotonically decreasing.
- **Unsafe-action rate** — must be 0. Any occurrence is a Sev1 on the agent.
- **Override rate** — of agent suggestions, how often the human took a different path. Contextual: high override on novel classes is fine; high on routine classes is a regression.
- **Rollback rate** — of agent-executed actions.
- **Faithfulness score** — audited rate of grounded claims.

### 5.6 Learning Velocity

- **Suite growth rate** — new scenarios curated / week.
- **Suite–prod distribution match** — how well the validation suite reflects current prod fault distribution.
- **Time from novel-incident to permanent regression test** — target: within one week.

---

## 6. Design Principles

Ordered by priority. Later principles yield to earlier ones under conflict.

1. **Safety first.** The cluster must never be worse off because the agent acted. Unsafe actions are hard-blocked, not soft-gated.
2. **Smallest blast radius that solves the task.** Reversible before irreversible; one node before many; drain before reboot; reboot before RMA.
3. **Faithfulness over fluency.** Every claim is grounded in an observed tool output. No unsupported numbers, no invented hosts, no imagined switch ports. If not observed, say so.
4. **Rules first, agent second.** Anything a rule can do reliably and cheaply, a rule should do. Reserve LLM reasoning for what needs reasoning.
5. **Correlate → Confirm → Reproduce.** Diagnoses are cheap without confirmation and near-worthless without reproduction. Follow the methodology; don't shortcut it for hard cases.
6. **Compose small skills.** Prefer many well-scoped skills that call each other over one large omnibus skill. The `skills/` directory is the vocabulary of the agent.
7. **Curate every failure.** No incident is truly closed until it exists as a replayable scenario in the validation suite (or has been consciously excluded with reasoning).
8. **Human-first explanations.** Outputs are for humans. Structured, defensible, and terse enough to be read on-call at 2 a.m.
9. **Instrument everything.** Every agent turn produces a persisted trajectory: prompts, tool calls, outputs, decisions, cost, latency, model & prompt version. No trajectory, no diagnosis.
10. **Data literacy.** Numbers must come from tools, not from the model's memory. If a value cannot be produced by a tool call, the agent does not report it.

---

## 7. Boundaries — What This Agent Is Not

Explicit non-goals prevent scope drift.

- **Not a scheduler / resource manager.** OpenPAI, Slurm, and the RM own placement decisions.
- **Not a capacity planner.** FinOps, fleet sizing, hardware refresh are out of scope.
- **Not a security / intrusion detection tool.** That is a separate domain with different data and different trust model.
- **Not a code-refactoring assistant for user jobs.** The agent tells users *what* failed and *whether* it is theirs; it does not rewrite user code.
- **Not autonomous for novel critical actions.** Firmware flash, cluster-wide config change, and RMA remain human-approved for the foreseeable future.
- **Not a general-purpose LLM assistant.** The agent is scoped to ops; free-form chat outside its skill set is out.

---

## 8. Autonomy Ladder — Target Trajectory

Per module (see [the system report](incidara-agent-sre.md) for coverage detail), a distinct target level and current state.

| Module | Today | 6-month target | 12-month target |
|---|---|---|---|
| Node crash — detect & classify | L2 (suggests + human executes) | L3 (auto-cordon + drain) | L3 |
| Node crash — diagnose | L1 (writes RCA) | L2 (RCA + reproducer proposed) | L2–L3 (RCA + auto-run reproducer) |
| Node regression — detect | L0 | L2 | L2 |
| Node regression — diagnose | L0 | L1 | L2 |
| Job crash — common (user/platform/hw classify) | L2 | L2 | L3 (auto-route + auto-reply user) |
| Job crash — common diagnose | L1–L2 | L2 (RCA + reproducer proposed) | L2–L3 |
| Job crash — complex (NCCL / SDC / hang) | L0–L1 | L1 (correlate) | L2 (correlate + confirm + reproduce) |
| Job regression | L0 | L0 | L1 |

**Rule:** no module moves beyond L2 without safety envelope + rollback semantics defined + review-layer coverage sampled at 100% for that module's L3+ actions.

---

## 9. Relationship to Related Efforts

- **[the system report](incidara-agent-sre.md)** — coverage: what we build, per module, per phase.
- **[the system report](incidara-agent-sre.md)** — measurement: offline gate + online dashboard + review layer.
- **AIOpsLab, ITBench** (external benchmarks) — reference points for cross-industry comparison. We consume their metric conventions where they fit; we do not conform coverage to their scenario catalog.
- **AgentSpace** (HKUDS) — a general human+agent collaboration platform. Different problem: we are a domain-specific agent for GPU-cluster ops, not a general workspace.
- **Traditional AIOps ML** — anomaly detection, log parsing, RCA benchmarks. We reuse metrics where relevant (see eval survey) but the primary value proposition here is *closed-loop action*, not just detection accuracy.

---

## 10. What This Doc Is *For*

Concrete uses:

- **Alignment** — every new skill, tool, or module should be justified as advancing one of the five value dimensions in §4.
- **Prioritization** — when two features compete, prefer the one that better serves the north-star metrics in §5.
- **Boundaries** — when scope creep is proposed, check §7 first.
- **Communication** — leadership summaries and cross-team reviews should draw from §1, §2, §5 rather than the coverage matrix.
- **Onboarding** — new contributors read this to understand *why*, then [the system report](incidara-agent-sre.md) for *what*, then the eval survey for *how measured*.

---

## 11. Living Document

This doc changes as the fleet, the workloads, and the failure landscape change. Every significant module release is a prompt to revisit §5 targets, §8 autonomy trajectory, and §7 boundaries. Fleet transitions (e.g., H200 → B300) will predictably invalidate baseline assumptions in §5.1 and §5.2.
