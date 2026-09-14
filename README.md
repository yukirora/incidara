# Incidara

### AI-driven reliability operations for GPU clusters—from raw signal to component-level diagnosis and verified recovery

A distributed training job can fail because of one GPU, one GPU memory device, one PCIe path, one NVLink, one IB port, one switch interface, one disk, an unhealthy node, or a shared platform service. The visible symptom—an Xid, NCCL timeout, `NodeNotReady`, failed validation, or stalled job—rarely identifies the real fault by itself.

Incidara correlates cluster telemetry, job events and logs, node history, `nvidia-smi`, kernel messages, SSH probes, BMC/Redfish/IPMI events, NVLink state, InfiniBand counters, switch interfaces, topology, and repair history. It determines the **smallest fault domain supported by the evidence**, distinguishes hardware faults from platform, workload, and transient failures, and coordinates the response through detection, triage, repair, validation, recycling, and feedback.

This is more than an alerting or node-classification system. Incidara can:

- identify a faulty GPU by index and PCI address from ECC/Xid evidence;
- narrow a communication failure to an NVLink, HCA, IB port, switch interface, or shared fabric path when topology and telemetry permit;
- identify an unhealthy NVMe/disk, filesystem, PSU, fan, DIMM/channel, or other BMC-reported component;
- cordon or drain the containing node, prepare an evidence-backed repair/RMA request, and require approval for high-risk actions;
- validate repaired resources and return them to service;
- reconcile repair outcomes and human corrections with the original decision so rules and skills can improve.

The node reliability loop and proactive detection system are operational today. Component-level identity is captured in evidence and diagnoses, while actions usually remain node-scoped because the scheduler and repair lifecycle operate on nodes. The training-job diagnosis/reproduction skills are included, but their dedicated durable orchestration remains roadmap work.

## Diagnostic scope

| Fault domain | What Incidara can currently identify |
|---|---|
| **Node** | Hostname, state, SKU, management address, alert history, recent jobs, validation state, and repair lifecycle |
| **GPU device** | GPU index, PCI address, model/serial, missing or unresponsive device, Xid history, and affected job |
| **GPU memory** | Per-GPU volatile/aggregate ECC counts, double-bit errors, row-remap events/failures, and the associated Xid |
| **PCIe** | Device PCI address, AER evidence, bus/device loss, and “fallen off the bus” failure |
| **NVLink / NVSwitch** | Per-GPU link number and state, link error masks/Xids, Fabric Manager state, and NVSwitch/tray failure patterns |
| **InfiniBand / RDMA** | HCA such as `mlx5_3`, physical port, link state/rate, error counters, and—when mapped—the affected path |
| **Switch** | Switch identity, interfaces with growing CRC/FCS/symbol errors, whole-switch versus single-port scope, and downstream nodes from topology |
| **Storage** | Disk/NVMe device, SMART/NVMe evidence, filesystem/mount errors, I/O failures, and disk pressure |
| **CPU / system memory / BMC** | CPU or DIMM inventory mismatches and BMC-reported memory ECC, PSU, fan, thermal, PCIe, GPU, NVSwitch, NIC, and FRU events |
| **Platform software** | Device-plugin registration, stale allocation state, kubelet/service failure, configuration drift, image/mount failure, and scheduler/resource conditions |
| **Training job** | Job and attempt; the included workflow models rank → PID → GPU → node → HCA → path, but full service orchestration is still in progress |

The **diagnostic scope** may be a GPU, link, port, disk, component, path, or service. The **action scope** is deliberately conservative: Incidara acts on the smallest safe operational unit exposed by the platform, most often a job or node, and escalates when the physical boundary is uncertain.

> The accompanying production study is observational. Reported improvements are temporal associations, not causal estimates. See [the full system and evaluation report](docs/incidara-agent-sre.md).

## Highlights

- **Complete node-failure lifecycle** — proactive detection, evidence-driven triage, repair, validation, reallocation, and outcome feedback.
- **Policy-bounded autonomy** — routine, reversible actions can run automatically; destructive or high-blast-radius actions require approval.
- **Role-scoped MCP tools** — diagnosis agents cannot invoke operations-only tools even if the model requests them.
- **Evidence-first handoffs** — investigation artifacts persist across agents so repair does not repeat triage work.
- **Self-improving operations** — completed RMA outcomes are reconciled with findings and used to improve detection rules and skills.
- **Training-job incident workflow** — log triage, cross-source evidence, controlled reproduction, recovery, and RCA closeout.
- **Stateful agent runtime** — multi-session HTTP gateways, SSE event streaming, permission waits, interruption, and crash recovery.
- **Human control plane** — the Incidara Console exposes tasks, transcripts, tool calls, approvals, schedules, metrics, and reports.
- **Dependency-aware delivery** — CI maps changed paths to affected services, rebuilds only those images, deploys them, and checks health.

See the [Incidara roadmap](docs/roadmap.md) for completed foundations, active milestones, dependencies, and exit criteria.

## Production results

A staged 131-day rollout covered **1,278 NVIDIA H200 nodes**, **152 racks**, and **145,860.5 observed node-days**.

| Metric | Pre-agent | Proactive-agent | Change |
|---|---:|---:|---:|
| Operational availability | 91.393% | **99.661%** | **+8.268 pp** |
| Incidents / 1,000 node-days | 18.564 | **4.217** | **−77.3%** |
| P90 recovery time | 150.4 h | **33.1 h** | **−78.0%** |
| Recovered within 72 h | 84.1% | **100.0%** | **+15.9 pp** |
| Triage coverage | 44.6% | **100.0%** | **+55.4 pp** |
| Unknown classification share | 56.1% | **4.1%** | **−52.0 pp** |

![Pre-agent versus proactive-agent outcomes](docs/evaluation/figures/figure-2-before-after.png)

The incident-rate ratio was **0.227** (95% CI 0.185–0.280). The rollout was not randomized, fleet composition and workload changed over time, and incident-to-agent-action linkage was incomplete. The results therefore describe association rather than proof that agents alone caused the change.

- [Full report](docs/incidara-agent-sre.md)
- [Aggregate metrics](docs/evaluation/metrics.csv)
- [Reproducible figure generator](docs/evaluation/generate_figures.py)

## How it works

```text
┌─────────────────────────────────────────────────────────────┐
│ Incidara Console — tasks, sessions, approvals, dashboards   │
└──────────────────────┬──────────────────────────────────────┘
                       │ REST + SSE
┌──────────────────────▼──────────────────────────────────────┐
│ Task Control — auth, queue, schedules, reconciliation       │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP + SSE
┌──────────────────────▼──────────────────────────────────────┐
│ Agent Gateway — sessions, events, persistence, permissions  │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│Detection │ Triage   │ Repair   │ Recycler │ Feedback        │
│          │          │          │          │                 │
│ Job-incident workflow                         Attention     │
└────┬─────┴────┬─────┴────┬─────┴────┬─────┴──────┬──────────┘
     │          │          │          │            │
┌────▼──────────▼──────────▼──────────▼────────────▼──────────┐
│ MCP tools — node operations, patrol, evidence, feedback,    │
│ switch operations, user reports, platform APIs and data     │
└─────────────────────────────────────────────────────────────┘
```

### Lifecycle agents

| Component | Responsibility | Permission boundary |
|---|---|---|
| **Detection** | Monitor patrol findings, switches, telemetry, and user reports; correlate signals and create investigation work | Diagnosis/read + alert |
| **Triage** | Gather GPU, kernel, network, job, BMC, and historical evidence; classify hardware, platform, or unknown | Diagnosis |
| **Repair** | Confirm the diagnosis, prepare repair/RMA evidence, execute platform fixes, and trigger validation | Operations; RMA requires approval |
| **Recycler** | Track repair tickets, validate completed work, and return healthy nodes to service | Operations |
| **Feedback** | Reconcile outcomes, identify misclassification patterns, patch skills/rules, and monitor the result | Feedback; patches require approval |
| **Job incident** | Run log triage → system evidence → reproduction → recovery → RCA closeout | Diagnosis, then evidence-gated recovery |
| **Attention** | Report ambiguous, blocked, unsafe, and approval-required work | Report-only |

### MCP servers

MCP server names are deployment contracts and remain unchanged:

| Server | Responsibility |
|---|---|
| `patrol-cron` | Collector and rule lifecycle, findings, verdicts, replay, and scheduled detection |
| `node-operations` | Platform/node queries, SSH and BMC probes, state transitions, and controlled actions |
| `agent-evidence` | Persistent investigation evidence shared between agents |
| `agent-feedback` | RMA case memory, reconciliation, analysis problems, and learning history |
| `switch-operations` | Switch inventory, telemetry, logs, and commands |
| `feishu-bitable` | User-submitted infrastructure reports |

`node-operations` exposes different tool sets for `readonly`, `diagnosis`, `ops`, and `feedback` roles. Permission is enforced by tool availability rather than prompt instructions alone.

## Closed-loop case examples

The following are sanitized production failure patterns represented by Incidara’s rules, investigation methods, repair workflow, and replay tests. Node and job identifiers are anonymized; the final decision always depends on the evidence observed for that incident.

### Case 1 — Locate a failed GPU, not merely an unhealthy node

```text
Signal
  nvidia-smi becomes unresponsive and the kernel reports Xid 79
      ↓
Detection
  patrol-cron creates a finding for the affected node
      ↓
Triage
  node inventory + timeout-bounded nvidia-smi + dmesg
  identify GPU index and PCI address, for example GPU 2 / 0000:ab:00.0
      ↓
Diagnosis
  the GPU disappeared from the PCI bus before the workload failure
  fault domain: GPU/PCIe hardware, contained by one node
      ↓
Action
  cordon or drain the node, preserve evidence, and delegate to Repair
      ↓
Repair and recovery
  confirm evidence → prepare vendor-safe RMA → human approval → submit
  Recycler tracks completion, resets/configures the node, validates it,
  and returns it to service
      ↓
Learning
  the RMA outcome is reconciled with the original finding and diagnosis
```

The node is the quarantine and repair unit, but the diagnosis identifies the GPU and PCIe location responsible for the node failure.

### Case 2 — Isolate a GPU memory failure

```text
Signal
  validation job reports a GPU ECC failure
      ↓
Evidence
  job log identifies the affected task/node
  nvidia-smi reports per-GPU ECC counters
  dmesg provides the corresponding Xid sequence
      ↓
Diagnosis
  GPU 4 has uncorrectable ECC and an Xid 48 double-bit memory error
  fault domain: GPU 4 memory, not every GPU in the server
      ↓
Action
  cordon the containing node and create an RMA request containing
  GPU index, PCI address, ECC count, Xid, timestamps, and reproduction
      ↓
Outcome
  confirmed repair returns through Recycler;
  NFF or misclassification enters Feedback for attribution and replay
```

Incidara distinguishes a concrete GPU-memory failure from transient or secondary Xids by checking the per-device counters and primary error sequence.

### Case 3 — Distinguish a failed NVMe device from platform disk pressure

```text
Signal
  DiskError, storage validation failure, or NodeNotReady
      ↓
Triage
  map the alert to a device; inspect df, lsblk, nvme smart-log,
  filesystem errors, mount state, and kernel I/O messages
      ↓
Decision
  SMART/media errors on /dev/nvme3n1 → hardware disk fault
  disk full with healthy media        → platform/storage cleanup path
      ↓
Hardware path
  cordon → evidence-backed disk RMA → replacement → reset/configure
  → storage validation → reallocate

Platform path
  clean space or repair mount/service → validate → return to service
```

The same node-level symptom therefore produces different actions depending on whether evidence follows the physical NVMe device or the software/filesystem layer.

### Case 4 — Narrow a communication failure to an IB port/path

```text
Signal
  a validation or training job reports NCCL/UCX timeout
  with ibv_create_ah failure on mlx5_3
      ↓
Triage
  map job → task/rank → node → HCA
  inspect mlx5_3 port 1 state/rate and incident-window counters
  correlate the node port with switch topology and interface errors
      ↓
Decision
  port Down or growing physical counters → HCA/cable/switch-port hardware path
  ports Active but rdma/hca missing      → device-plugin/platform path
  several jobs/ports fail together       → shared switch/fabric scope
      ↓
Action
  isolate the smallest supported job/node/path scope;
  do not cordon unrelated nodes
      ↓
Recovery and learning
  repair or platform fix → validation → job/node recovery → RCA/outcome feedback
```

This is the intended diagnostic progression from a broad “NCCL timeout” symptom to a specific HCA, physical port, switch interface, or shared fabric domain when the available mapping supports it.

## Design limitations

| Limitation | Current boundary |
|---|---|
| **Component diagnosis, node/job actuation** | Incidara can identify a specific GPU, PCIe device, NVLink, HCA/IB port, switch interface, or disk, but the platform usually exposes operational actions at node or job scope. The resulting action is therefore commonly cordon, drain, reset, RMA, job recovery, or node reallocation. Physical component replacement is outside Incidara. |
| **Environment-specific integrations** | Incidara is currently built around LTP/OpenPAI schemas and APIs, Codeup, Feishu, OSS, the ticket system, SSH/BMC access, and fleet-specific hardware conventions. Adapting it to another platform requires new integrations and hardware/runbook calibration; it is not currently a plug-and-play generic AIOps framework. |
| **Bounded rather than unrestricted autonomy** | Incidara automatically handles known operations inside configured permission and blast-radius limits. Destructive, irreversible, novel, or broad actions require human approval or takeover. This safety boundary is intentional. |

Implementation progress—such as unified incident orchestration, reproduction executors, node-regression detection, memory integration, and full training-job service deployment—is tracked in the [roadmap](docs/roadmap.md), not treated as a fundamental design limitation.

## Usage

Incidara is an integrated infrastructure system. A complete deployment requires access to the target platform, databases, network/BMC endpoints, model provider, ticket service, and any enabled report integrations. The checked-in configuration contains placeholders only.

### 1. Prerequisites

- Linux deployment host with Docker Engine and Docker Compose
- Python 3.10+ with `PyYAML` and `Jinja2`
- Node.js 22+ for local Console/runtime development
- SSH agent/socket available to agents that probe infrastructure
- PostgreSQL or the included database services
- Credentials for the model provider and enabled platform integrations

### 2. Install development dependencies

```bash
git clone https://github.com/yukirora/incidara.git
cd incidara
make install
```

### 3. Configure the Console registry

```bash
cp console/config/agents.yaml.example console/config/agents.yaml
cp console/config/groups.yaml.example console/config/groups.yaml
```

Edit the files to define agent gateways and access groups. Runtime configuration files are ignored by Git.

### 4. Configure the deployment

```bash
cp compose/config.yaml.example compose/config.yaml
$EDITOR compose/config.yaml
```

At minimum, replace every `CHANGE_ME` value used by the services you enable. The template retains the existing platform contracts, including `LTP_*`, Codeup, Feishu, ticket, OSS, SSH/BMC, PostgreSQL, and model-provider settings.

Never commit `compose/config.yaml` or generated `.env` files.

### 5. Validate and render Compose

```bash
.venv/bin/python compose/render.py --check
.venv/bin/python compose/render.py

cd compose/rendered
docker compose config
docker compose config --services
```

The renderer creates:

```text
compose/rendered/
├── docker-compose.yml
├── databases.yml
├── mcp-servers.yml
├── agents.yml
├── chat-ui.yml
├── backup.yml
└── <service>.env
```

### 6. Start Incidara

From `compose/rendered/`:

```bash
docker compose up -d --build
```

Or bring up the stack in layers:

```bash
# State
docker compose up -d agent-db chat-ui-db

# MCP tools
docker compose up -d \
  agent-evidence agent-feedback agent-feedback-write \
  patrol-cron job-patrol switch-operations \
  node-ops-diagnosis node-ops-feedback node-ops-ops

# Agents
docker compose up -d \
  detection-agent triage-agent repair-agent \
  recycler-agent feedback-agent attention-agent

# Console
docker compose up -d incidara-console-api incidara-console-web
```

Check health:

```bash
docker compose ps
docker compose logs --tail=100 <service>
```

### 7. Use the agents

Use the Console to create tasks or schedules against a configured gateway. Example prompts:

```text
Scan the cluster for unjudged findings and collector anomalies.
Triage all triaged_unknown nodes.
Investigate node <hostname> and classify the fault.
Check completed repair tickets and recycle eligible nodes.
Analyze completed RMA outcomes and identify recurring misclassifications.
Investigate training job <job-name> using the job-incident workflow.
```

High-risk tool calls enter `waiting_input` until an authorized user approves or denies them in the Console.

### 8. Run validation

```bash
make verify
```

The verification target checks repository scope and private-data patterns, validates the Compose renderer, runs Console and agent-runtime tests, runs MCP unit tests, compiles Python sources, and builds the TypeScript applications.

## CI and incremental deployment

The retained Codeup pipeline performs deployment without rebuilding unrelated services:

```text
merge
  → ci/detect-changed-services.sh
  → ci/service-path-map.tsv
  → ci/build-and-deploy.sh
  → compose/render.py
  → build/recreate affected services
  → health verification
```

Generate the encrypted `CONFIG_YAML_B64` pipeline value from a local deployment configuration:

```bash
bash ci/generate-config-b64.sh compose/config.yaml
```

`ci/flow.yml` contains placeholders for the Codeup repository connection and private runner group. Configure those values in the CI system; do not commit runner credentials or the generated base64 configuration.

## Repository layout

```text
incidara/
├── ci/                         # changed-service detection and deployment
├── compose/                    # config renderer and service templates
├── infra/
│   ├── backup/agent/           # agent workspace/session backup
│   └── postgresql/             # schemas and pgBackRest support
├── incidara_agents/
│   ├── agents/                 # runtime and lifecycle agent images
│   ├── mcp_servers/            # role-scoped tools
│   ├── skills/                 # operational workflows and runbooks
│   └── ltp-platform/           # storage/PostgreSQL SDK integration
├── console/                    # React UI and Node task-control server
└── docs/                       # system report and design documentation
```

## Documentation

- [System architecture and production evaluation](docs/incidara-agent-sre.md)
- [Engineering roadmap](docs/roadmap.md)
- [Deployment, CI, backup, and restore](docs/deployment.md)

Component-specific behavior lives beside the implementation in agent, MCP server, skill, and Console READMEs.

## Study limitations

The production rollout was not randomized. Fleet size, fleet age, workload, staffing, and operating procedures changed during the study. Classification also changed as agents increased triage coverage. Direct finding-to-repair outcome linkage was incomplete. These limitations are why the evaluation reports observational association rather than causal effect.

## Project status and license

The [roadmap](docs/roadmap.md) separates completed production foundations from the remaining orchestration, memory, exception-learning, reproduction, training-job, and autonomy milestones.

This repository is currently private while ownership, contributor approval, dependency review, and licensing are completed. No public license has been granted yet.
