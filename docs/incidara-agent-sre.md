# Incidara: A Multi-Agent AI System for Production GPU Fleet Reliability

---

## Abstract

We present Incidara, a production multi-agent AI system for site reliability engineering (SRE) on a fleet of 1,278 NVIDIA H200 GPU nodes (152 racks). The system decomposes the node-failure lifecycle across five specialized agents—detection, triage, repair, recycler, and feedback—plus a dedicated job-incident agent for training-job failure diagnosis and recovery. Each agent is backed by a shared evidence database, role-scoped tool access, and a skill library that the system itself can evolve. A staged rollout over 145,860.5 observed node-days compared four operational phases: pre-agent operations, initial AI triage, multi-agent triage and repair, and proactive AI detection. Time-weighted operational availability increased from 91.393% (pre-agent) to 99.661% (proactive-agent). The qualifying cordon incident rate decreased from 18.564 to 4.217 incidents per 1,000 node-days—an incidence-rate ratio (IRR) of 0.227 (95% CI 0.185–0.280), a 77.3% rate reduction—and P90 recovery time decreased from 150.4 to 33.1 hours. Triage coverage before recovery reached 100%, while the unknown-classification share fell from 56.1% to 4.1%. Because rollout was not randomized and incident-to-agent-action attribution is incomplete, these changes are reported as observational associations rather than causal effects.

---

## 1. Introduction

### 1.1 Problem statement

Large-scale AI training infrastructure faces a fundamental operational scaling problem. As GPU fleets grow from hundreds to thousands of nodes, hardware failures become a daily statistical certainty rather than an exceptional event. On the studied H200 fleet, the pre-agent baseline recorded 18.564 qualifying incidents per 1,000 node-days—roughly one cordon event every 77 node-days per node, or over 16 events per day fleet-wide at the baseline rate. Synchronous, gang-scheduled training workloads amplify each incident: a single degraded node can stall or corrupt an entire multi-node job.

Traditional SRE practice addresses this with human-run playbooks, threshold alerts, and manual triage. Three structural limits emerge at production scale:

1. **Human throughput does not scale with fleet size.** Each diagnosis requires multi-source evidence gathering (GPU counters, kernel logs, InfiniBand state, job history, BMC telemetry), and expert time is the serializing resource.
2. **Knowledge evaporates at ticket closure.** Diagnosis insights live in tickets and engineers' heads; the next similar failure restarts from zero unless knowledge is captured as executable rules.
3. **The unknown tail grows with scale.** Known failure signatures can be automated with rules, but novel failures—cross-layer interactions, new hardware/software combinations—require exploratory diagnosis that fixed playbooks cannot cover.

### 1.2 Approach

Incidara replaces the human-per-ticket model with a pipeline of cooperating AI agents that cover the complete node-failure lifecycle:

```
detection → triage → repair → recycler → feedback
```

Each agent is a large-language-model (LLM) driven autonomous worker with:

- **scoped permissions** (read-only, diagnosis, or operations roles),
- **a curated skill library** encoding investigation methodology,
- **MCP (Model Context Protocol) tool access** to platform databases, SSH, BMC, and job systems,
- **evidence persistence** to a shared database so downstream agents reuse upstream findings,
- **human approval gates** for high-risk actions.

The design philosophy is *policy-bounded autonomy*: agents execute known, low-blast-radius actions automatically; high-risk or novel actions pause for human review; and every action-outcome pair feeds a learning loop that patches the skill library itself.

### 1.3 Contributions

1. A production architecture for lifecycle-decomposed multi-agent SRE with role-scoped tool access, evidence handoff, and skill self-evolution.
2. A staged 131-day rollout across 1,278 H200 nodes with pre-registered metric definitions and honest attribution boundaries.
3. Observational evidence that the proactive-agent phase was associated with 99.661% operational availability, a 77.3% lower incident rate, and a 78.0% reduction in P90 recovery time relative to the pre-agent baseline.

---

## 2. System Architecture

### 2.1 Overview

The system comprises five layers:

| Layer | Responsibility |
|---|---|
| **Interaction** | Web UI for task submission, live session viewing, approvals, and fleet dashboards |
| **Task control** | Persistent task queue, session management, scheduling, reconciliation |
| **Agent runtime** | A stateful HTTP gateway wrapping the Claude Code SDK into multi-session, SSE-streaming services |
| **Specialist agents** | Five lifecycle agents plus auxiliary report/optimization agents, each with distinct skills and permissions |
| **Tools & data** | MCP servers (node operations, evidence, feedback knowledge base, switch monitoring), platform databases, SSH/BMC access |

```
┌─────────────────────────────────────────────────────────────┐
│  Web UI (React) — dashboards, task submission, approvals    │
└──────────────────────┬──────────────────────────────────────┘
                       │ REST + SSE
┌──────────────────────▼──────────────────────────────────────┐
│  Task Control Server — auth, queue, schedule, reconcile     │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP + SSE
┌──────────────────────▼──────────────────────────────────────┐
│  Agent Gateway (per agent) — sessions, events, permissions  │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│Detection │  Triage  │  Repair  │ Recycler │    Feedback     │
│ (probe)  │(diagnose)│  (fix)   │(re-add)  │   (learn)       │
└────┬─────┴────┬─────┴────┬─────┴────┬─────┴──────┬──────────┘
     │          │          │          │            │
┌────▼──────────▼──────────▼──────────▼────────────▼──────────┐
│  MCP Tool Layer — node-ops, evidence, feedback, monitoring  │
│  Data: node DB, evidence DB, case memory, platform APIs     │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 The six core lifecycle agents

The lifecycle decomposition follows the physical node-failure workflow and the parallel training-job incident workflow. Each agent owns a distinct segment with non-overlapping permissions:

| Agent | Input state | Core function | MCP role | Approval model |
|---|---|---|---|---|
| **Detection** | Fleet-wide telemetry | Proactive monitoring: switch health, user reports, correlation, early alerting | diagnosis | Automatic (read + alert) |
| **Triage** | `triaged_unknown`, `triaged_hardware` | Categorize alerts, investigate with multi-source evidence, classify hardware vs. platform, delegate | diagnosis | Dry-run default; `--execute` for auto |
| **Repair** | `triaged_hardware`, `triaged_platform` | Deep investigation, evidence collection, RMA ticket drafting, platform fixes | ops | **Hardware: mandatory human approval** before RMA; Platform: direct |
| **Recycler** | `ua` (RMA open), `ready_ua` | Track vendor RMA tickets, close completed ones, reallocate repaired nodes | ops | Automatic (routine lifecycle) |
| **Feedback** | Closed RMA outcomes | Mine outcomes, diagnose misclassifications, patch skills, deploy | feedback | **Skill patches require human approval** before deploy |
| **Job-incident** | Job anomaly (hang, NCCL timeout, OOM, Loss NaN, slowdown, jitter, checkpoint failure, crash) | Lifecycle-orchestrated training-job failure diagnosis: log triage → cross-source evidence → controlled reproduction → recovery → RCA closeout | diagnosis → ops (per-stage) | Read-only diagnosis; recovery mutations require evidence-backed isolation |

#### 2.2.1 Detection agent

Runs as a scheduled probe framework. Current probes include switch health monitoring (uptime, PSU, fans, port errors, topology correlation) and user-report ingestion. The detection agent correlates switch-level events to affected nodes (e.g., switch reboot → which nodes lost connectivity) and submits alerts or directly delegates to triage when confidence is high. It is read-only with respect to node state.

#### 2.2.2 Triage agent

Processes nodes in `triaged_unknown` and `triaged_hardware` states in batches of ten (to manage context window). The workflow:

1. **Fetch** nodes with their alert context and triage window timestamps.
2. **Categorize** by matching alert signatures against a curated rule file.
3. **Investigate** following a per-issue-type decision tree: GPU checks (nvidia-smi, ECC, Xid), InfiniBand state (ibstat, perfquery), kernel logs (dmesg), job history, BMC queries.
4. **Classify** as hardware (concrete physical evidence required), platform (software/config issue), or remain unknown.
5. **Decide**: delegate hardware to repair agent, transition platform nodes to repair, or send unclear cases to revalidation.

A persistent working file records per-batch progress, enabling crash recovery and session continuation without re-investigating completed nodes.

#### 2.2.3 Repair agent

Handles one node at a time with two flows:

- **Hardware flow**: deep investigation → evidence collection → RMA ticket drafting (with a vendor-safe summary methodology that strips workload identity) → **human approval gate** → ticket submission → node enters the RMA queue.
- **Platform flow**: diagnose → fix (via SSH/kubectl) → verify → trigger revalidation job.

The hardware approval gate is always active: the agent presents the draft ticket with evidence, and a human must explicitly approve before any destructive action (node reset, RMA submission).

#### 2.2.4 Recycler agent

Closes the physical repair loop:

1. **Ticket tracking**: polls vendor RMA tickets for nodes in `ua` state. Completed repairs → close ticket, move to `ready_ua`. Anomalous tickets (rejected, cancelled) → back to `triaged_unknown`.
2. **Node reallocation**: for `ready_ua` nodes, runs the full bring-up pipeline (reset, configuration, system-info collection, allocation) — approximately 20–30 minutes per node, fully automated.

#### 2.2.5 Feedback agent

The only agent that modifies skill files. Its closed loop:

```
Completed RMAs → collect & classify → case_memory DB
    → analyze patterns (NFF, misclassification)
    → case-diagnosis (what went wrong)
    → patch-and-validate (write skill fix + validate on real data)
    → HUMAN APPROVAL GATE
    → deploy (git PR → merge → CI auto-deploys)
    → monitor outcomes
```

The analysis identifies recurring problem classes: triage misclassification, repair submitting RMA when local fix was possible, detection gaps (no alert fired before user report). Each problem becomes a tracked entry with status lifecycle: `open → patch_created → monitoring → resolved/unresolved`.

#### 2.2.6 Job-incident agent

Handles training-job failures as a parallel lifecycle to node failures. Where the node agents operate on physical infrastructure states, the job-incident agent operates on job incidents: a hang, NCCL timeout, GPU OOM, Loss NaN, throughput slowdown or jitter, checkpoint failure, or process crash. A single incident may implicate zero nodes (data error), one node (GPU fault), or many nodes (fabric degradation).

The agent runs a staged skill chain with direct handoffs:

```
job-incident-response (lifecycle init)
  → job-log-triage (first anomaly + candidate hypotheses)
  → system-evidence-diagnosis (cross-source TSDB/GPU/network/storage evidence)
  → job-recovery (evidence-backed isolation + checkpoint restore)
  → training-reproduction (controlled experiments when passive evidence is insufficient)
  → rca-closeout (knowledge patches → rule/runbook/skill updates)
```

Each failure mode (hang, NCCL, OOM, NaN, slowdown, jitter, checkpoint, crash) has three parallel hypothesis tables—one for log patterns, one for system-evidence queries, and one for reproduction experiments—covering 62 candidate hypotheses with explicit support/refute/inconclusive gates. The chain enforces two mandatory pre-classification fields on every incident: `IMPACT_SCOPE` (single rank/job, same node, same rack/rail, or multiple independent jobs) and `FAILURE_STAGE` (admission, initialization, steady-state, checkpoint, or teardown), which determine hypothesis priority before any string matching begins.

### 2.3 Agent gateway

All agents extend a common gateway runtime that wraps the Claude Code SDK into a stateful HTTP service:

- **Session lifecycle**: `starting → running → busy → waiting_input → running → … → completed`, with interrupt and error states. Sessions persist events as JSONL for crash recovery.
- **SSE event streaming**: every tool call, permission request, and message delta streams to connected clients in real time.
- **Permission manager**: tool calls matching "ask" rules block the session in `waiting_input` until a human approves or denies via HTTP. This is the technical mechanism behind hardware-repair approval gates.
- **Scheduling**: recurring prompts (e.g., detection scans, ticket checks) can be scheduled via cron/interval.

Each specialist agent image extends the base gateway with its own MCP server configuration, skill whitelist, and permission rules.

### 2.4 MCP tool layer with role-based access

Tools are provided via MCP (Model Context Protocol) servers. The primary server, `node-operations`, enforces role-scoped access:

| Role | Read | Write | Execute |
|---|---|---|---|
| `readonly` | All queries + probes | — | — |
| `diagnosis` | All queries + probes | Status transitions, validation submission | — |
| `ops` | All queries + probes | All writes + node reset + RMA submission | SSH commands, kubectl |
| `feedback` | Node data + knowledge DB | Knowledge DB only | — |

This ensures, for example, that the triage agent (role: `diagnosis`) cannot reset a node or submit an RMA even if its LLM output suggested doing so—the tool simply is not available.

### 2.5 Evidence database

A dedicated PostgreSQL database persists investigation artifacts, shared across agents:

| Field | Content |
|---|---|
| `node_name` | Hostname |
| `collected_by` | Agent identity (auto-set, not user-supplied) |
| `source` | `probe_ssh`, `nvidia_smi`, `dmesg`, `ib_stat`, `job_log`, `bmc`, … |
| `category` | `gpu`, `ib`, `nvlink`, `pcie`, `cpu`, `memory`, `platform` |
| `summary` | One-line interpretation |
| `content` | Full raw output |
| `metadata` | Structured fields (JSONB) |

Three-layer enforcement guarantees completeness:

1. **Auto-save**: MCP tools automatically persist their output.
2. **Proactive save**: agents save ad-hoc SSH output explicitly.
3. **Evidence gate**: RMA ticket submission checks that mandatory evidence for the fault type exists; missing evidence blocks submission.

Cross-agent handoff: the repair agent reads evidence saved by triage via `get_node_evidence(hostname, collected_by="triage")`, avoiding duplicate probing.

### 2.6 Skill system

Skills are versioned Markdown-plus-scripts packages that define *how* an agent works. Each skill directory contains:

- `SKILL.md` — workflow orchestration with YAML frontmatter (name, description, allowed tools)
- Domain sub-documents — categorization rules, investigation methodology, configuration
- `sub-skills/` — executable procedures (health checks, status transitions, DB queries)

Skills load via progressive disclosure: only `name + description` enter the system prompt at session start; the full body loads when the agent matches the task; sub-documents load only when the referenced step executes. This keeps context costs bounded while supporting a growing library (33+ skills at time of writing).

Deployment uses per-agent whitelists: each agent's build configuration specifies which skills it receives. Skills are volume-mounted from the repository, so a merged patch takes effect on new sessions without image rebuild.

### 2.7 Human-in-the-loop governance

Automation autonomy follows a graduated trust model with three human-intervention categories:

| Category | When | Examples |
|---|---|---|
| **Risk approval** (pre-action) | Agent proposes a high-impact action | Node reset, RMA submission, drain running jobs, batch cordon, firmware/network changes |
| **Exception takeover** (mid-execution) | Automation cannot safely continue | Partial failure, blast-radius expansion, rollback failure, telemetry unavailable |
| **Policy governance** (system-level) | Changing future behavior | Skill patches, detection rules, permission changes, autonomy-level promotion |

The design principle: **agents handle known, repeatable, verifiable work; humans handle trade-offs, unknowns, high risk, exceptions, and governance.**

---

## 3. Deployment and Staged Rollout

### 3.1 Fleet

The evaluated fleet comprises 1,278 NVIDIA H200 GPU nodes across 152 racks, observed for 131 days (February 15 – June 25, 2026, UTC), totaling 145,860.5 node-days (3,500,652.4 node-hours). Inventory changed during the window; all rates are normalized by observed node-time.

### 3.2 Rollout phases

| Phase | Period | Capability introduced |
|---|---|---|
| **Pre-agent** | Feb 15 – Apr 16 | Existing platform workflow; no AI agents |
| **Initial triage** | Apr 17 – May 6 | AI-assisted node triage and structured evidence collection |
| **Multi-agent** | May 7 – Jun 7 | Triage delegates to specialized repair agents |
| **Proactive detection** | Jun 8 – Jun 25 | Scheduled detection agents, proactive scanning, switch monitoring |

---

## 4. Evaluation

### 4.1 Headline before/after results

The primary comparison uses the pre-agent phase as baseline and the proactive-agent phase as the after period.

| Metric | Pre-agent | Proactive | Change |
|---|---:|---:|---:|
| Operational availability | 91.393% | **99.661%** | **+8.268 pp** |
| Incidents / 1,000 node-days | 18.564 | **4.217** | **−77.3%** |
| P90 recovery time | 150.4 h | **33.1 h** | **−78.0%** |
| Recovered ≤24 h | 56.1% | **70.1%** | +14.0 pp |
| Recovered ≤72 h | 84.1% | **100.0%** | +15.9 pp |
| Triage coverage | 44.6% | **100.0%** | +55.4 pp |
| Unknown classification share | 56.1% | **4.1%** | −52.0 pp |

Incidence-rate ratio:

$$IRR = \frac{97 / 22{,}999.5}{1{,}050 / 56{,}561.0} = 0.227 \quad (95\% \text{ CI } 0.185\text{–}0.280)$$

### 4.2 Full staged series

| Phase | Node-days | Availability | Incidents | Rate | P90 recovery |
|---|---:|---:|---:|---:|---:|
| Pre-agent | 56,561.0 | 91.393% | 1,050 | 18.564 | 150.4 h |
| Initial triage | 25,500.0 | 96.293% | 116 | 4.549 | 143.4 h |
| Multi-agent | 40,800.0 | 98.366% | 267 | 6.544 | 105.3 h |
| Proactive | 22,999.5 | **99.661%** | 97 | **4.217** | **33.1 h** |

The series is not monotonic: incident rate rose from 4.549 to 6.544 during multi-agent before falling to 4.217. All phases are reported to avoid endpoint-selection bias.

### 4.3 Recovery distribution

| Phase | Median | P90 | ≤24 h | ≤72 h |
|---|---:|---:|---:|---:|
| Pre-agent | 17.3 h | 150.4 h | 56.1% | 84.1% |
| Initial triage | 65.0 h | 143.4 h | 20.7% | 64.7% |
| Multi-agent | 26.9 h | 105.3 h | 47.2% | 79.8% |
| Proactive | **15.1 h** | **33.1 h** | **70.1%** | **100.0%** |

The strongest improvement is in the tail: P90 fell 78.0% while the median fell 13.0%. The later workflow was associated with fewer prolonged incidents, not that every incident became proportionally faster.

### 4.4 Triage speed

In the proactive phase, median time from cordon to a bounded triage transition was **0.21 hours (≈13 minutes)**; P90 was **0.27 hours (≈16 minutes)**.

### 4.5 Failure distribution

Full-window incident taxonomy (1,530 qualifying incidents, 936 distinct nodes):

| Category | Incidents | Share |
|---|---:|---:|
| Unknown | 683 | 44.6% |
| Hardware | 503 | 32.9% |
| Platform | 344 | 22.5% |

By the proactive phase: 91.8% hardware, 4.1% platform, 4.1% unknown.

Leading recorded triggers included `NvidiaSmiLatencyTooLarge` (34.1%), OS auto-upgrade network restarts (8.4%), and `RecallForUpgrade` (7.8%).

### 4.6 Agent activity records

| Record type | Count | Notes |
|---|---:|---|
| Investigation cases | 327 | Coverage May 18 – Jun 11 |
| Evidence records | 18,304 | Across 617 distinct nodes |
| Proactive findings | 3,865 | 128 confirmed, 771 rejected, 2,966 unjudged |
| Analysis/learning problems | 73 | Tracked through patch lifecycle |
| Recorded learning memories | 43 | Human-corrected insights |

The 2,966 unjudged findings and zero populated repair-outcome links establish that agents were active but do not isolate agent-caused improvement from fleet maturation, staffing, or workload change.

### 4.7 Supporting workload metrics

**Job outcomes (May 29 – Jun 24):** 8,325 H200 jobs, 6.204M GPU-hours; 45.18% success, 0.781% hardware-failure, 6.34% software-failure, 31.14% user-stop, 16.52% unknown.

**GPU utilization (May 30 – Jun 25):** 90.71% allocated; 75.58% of allocated active; 63.48% utilized-equivalent per capacity GPU-hour; 85.57% average utilization among active GPUs.

These are late-window supporting metrics only and do not support a February–June trend.

---

## 5. Metric Definitions

### 5.1 Operational availability

$$A_{op} = \frac{H_{available} + H_{allocated}}{H_{all} - H_{deallocated}}$$

Allocated nodes count as operational (serving work). Deallocated capacity is excluded from the denominator. Full-window value: **95.501%**.

### 5.2 Qualifying incidents

An `available → cordoned` state transition, excluding onboarding transitions followed by `cordoned → new` within one hour.

### 5.3 Recovery time

From qualifying cordon timestamp to the next transition ending in `available`. Eventual (not strictly in-window) recovery.

---

## 6. Threats to Validity

1. **Non-randomized rollout.** Time, fleet age, workload, and process all co-vary with rollout phase.
2. **Changing cohort.** Fleet expanded during the window; node-time normalization reduces but does not eliminate cohort effects.
3. **Incomplete causal linkage.** No proactive finding has a populated repair outcome; only one RMA finding has direct reconciliation.
4. **Classification drift.** Agent rollout changes both the process and the probability of receiving a label.
5. **Event dependence.** Repeated node failures and rack correlation violate independent-Poisson assumptions; the IRR interval is descriptive.
6. **Right-censored recovery.** Two incidents recover after period end.
7. **Late supporting telemetry.** Jobs and utilization data cover only late May onward.

---

## 7. Recommended Interpretation

> In an observational staged-rollout study covering 145,860.5 H200 node-days, the proactive AI-agent phase was associated with 99.661% operational availability. Relative to the pre-agent phase, the exposure-normalized cordon incident rate decreased from 18.564 to 4.217 per 1,000 node-days (77.3% lower; IRR 0.227, 95% CI 0.185–0.280), and P90 recovery time decreased from 150.4 to 33.1 hours. Triage coverage increased from 44.6% to 100%, and unknown classifications fell from 56.1% to 4.1%. Because rollout was not randomized and action-to-outcome linkage is incomplete, these results indicate temporal association rather than causal effect.

---

## 8. Future Work

To support causal claims:

- Randomize or stagger rollout across comparable nodes/racks
- Persist a stable incident ID linking detection → decision → action → outcome
- Define holdout or matched-control cohorts
- Use node/rack fixed effects and block-bootstrap uncertainty
- Apply censored survival models for recovery time
- Independently adjudicate a frozen failure taxonomy
- Report agent precision, recall, false-action rate, human-intervention rate, and cost per resolved incident

---

## 9. Conclusion

Incidara demonstrates that a lifecycle-decomposed multi-agent AI system can operate production GPU-fleet reliability at a scale where human-per-ticket workflows saturate. The architecture combines role-scoped autonomy, evidence persistence, human approval gates for high-risk actions, and a self-evolving skill library. The 131-day observational study associates the full system with substantially higher availability, lower incident rates, and dramatically improved recovery tails. The system's honest attribution boundary—observational association, not causal proof—defines the evaluation roadmap for the next iteration.

---

*Reproducibility: aggregate metric values are maintained in [`evaluation/metrics.csv`](evaluation/metrics.csv); figures are generated by script using only the Python standard library.*
