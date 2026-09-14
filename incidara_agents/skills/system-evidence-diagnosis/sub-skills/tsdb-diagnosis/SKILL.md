---
name: tsdb-diagnosis
description: "Read-only common TSDB diagnosis for live or finished training jobs. Verifies job identity, attempt, hosts, time coverage, and metric semantics; aligns training steps with wall clock; compares incident and healthy windows; finds the earliest supported system anomaly and reports evidence gaps without declaring root cause or taking action."
allowed-tools: Agent Bash Read Grep Glob
---

# tsdb-diagnosis

Provide trustworthy time-series evidence to the calling `/system-evidence-diagnosis` failure-mode sub-skill.

This skill answers:

```text
Are these metrics from the correct job attempt and hosts?
What is the wall-clock incident window?
Which existing metric changed first?
Did the change precede or follow the training symptom?
What evidence is missing or stale?
```

It does not select the symptom hypothesis, declare root cause, isolate resources, restart a job, or create new telemetry.

## Required input

Obtain before querying:

```text
job identifier and attempt when available
live or finished job state
node/host list and GPU identities when available
last healthy step and first abnormal step, or an explicit wall-clock window
observed symptom: Hang, NCCL/RCCL timeout, Slowdown, or throughput jitter
metrics requested by the selected system-evidence playbook
healthy comparison: same job phase, earlier window, or equivalent healthy job
```

If job identity, attempt, or incident window cannot be established, return an evidence gap instead of searching an arbitrary time range.

## 1. Select the existing data source

Use the TSDB/query tool already available in the target environment.

For a live job, prefer the live source. For a finished job, prefer persisted or historical storage. If both exist, compare their coverage before choosing. Never assume an endpoint, credential, port, dashboard, or metric name that has not been provided or discovered.

Record:

```text
source queried
query time
available history range
step or sample resolution
retention/compaction limit when known
```

Return `unsupported` if no safe read-only query capability exists.

## 2. Verify identity before interpreting values

Query labels/series metadata or a narrow known training metric first. Verify as many of these as exist:

```text
job name or identifier
attempt/restart identifier
namespace/tenant/user
expected hosts
GPU UUID or device index
model/run identifier
metric timestamps overlapping the job lifetime
```

Cross-check one training signal such as completed step, loss, or throughput against the authoritative worker log or raw TensorBoard event. A matching name with a different attempt is not the same job.

Stop and return `wrong-series` or `inconclusive` when:

```text
labels point to another job/attempt
hosts do not overlap the allocation
samples predate or postdate the job
training progress conflicts with authoritative logs
```

## 3. Verify metric existence, coverage, and freshness

For every requested metric, distinguish:

```text
present with usable samples
present but partly missing
present but stale/flat
absent from this environment
unknown because the query failed
```

An empty result does not mean zero or healthy.

Check per-host coverage. A cluster aggregate can hide one missing or slow node. Treat a host whose value and scrape/update behavior remain exactly flat as possible stale data, especially when neighboring metrics continue to change.

Record exporter restarts, counter resets, gaps, duplicated last values, unexpected label churn, and resolution changes.

## 4. Align training progress with wall clock

Use authoritative worker logs or raw TensorBoard events to map:

```text
last healthy step → timestamp
first abnormal/slow step → timestamp
final timeout/termination → timestamp
recovery step → timestamp when available
```

Create two comparable windows:

```text
healthy window: same phase before the symptom or an equivalent healthy job
incident window: includes a short lead-in before the first abnormal step and recovery/termination after it
```

For throughput jitter, create one incident window per slow step and use equal healthy windows. For a periodic symptom, the observation window must span multiple periods.

Do not align only on job start time when compile, warmup, checkpoint, evaluation, or restore duration differs.

## 5. Query metrics according to their semantics

### Gauges

Examples include utilization, power, clock, temperature, memory usage, queue depth, latency, and throughput.

Compare raw values and appropriate per-host summaries over identical windows. Preserve the host/device dimension until the symptom scope is understood.

### Counters

Examples include RDMA retries/errors, PCIe/RAS errors, packets, retransmits, storage errors, and process restarts.

Use windowed increase or rate and check for resets. A device-lifetime total cannot explain one incident without a matching time-window delta.

### Histograms and summaries

Use comparable quantiles and sample counts. Do not compare a sparse P99 with a dense P99 without checking observation count and bucket/schema consistency.

### Training progress

Use completed steps rather than log modification time. Compare throughput/step time only after excluding compile, warmup, checkpoint, evaluation, and profiler writeback unless one of those phases is the hypothesis.

## 6. Query the metric families requested by the evidence playbook

The selected system-evidence playbook decides which evidence is relevant. Typical existing families are:

```text
training: completed step, step time, throughput, loss, checkpoint progress
GPU: utilization, power, clock, temperature, memory, Xid/RAS when exported
host: runnable/blocked processes, CPU, memory pressure, OOM, I/O pressure
network: RDMA retry/ACK/CQ, port/link, TCP retransmit for applicable paths
storage: read/write throughput, latency, queue, dirty/writeback, errors
scheduler/change: allocation, eviction, placement, version/config events
```

Do not fabricate a finer per-rank or per-phase metric. If the requested evidence is unavailable, state the exact missing observation and continue only with a lower-confidence layer-level conclusion.

## 7. Find the earliest supported anomaly

For each metric family, record:

```text
first change timestamp
affected hosts/devices
direction and magnitude relative to the healthy window
whether it precedes, coincides with, or follows the training symptom
whether it recovers when training recovers
```

Prefer the earliest change that is specific enough to test a hypothesis. Do not choose the metric with the largest visual spike if it changed after the training stopped.

Examples of bounded interpretations:

```text
GPU utilization high while power approaches the same-SKU idle baseline
→ compatible with polling/busy-wait; not proof of NCCL root cause

RDMA retry increase precedes collective stall on a fixed host/path
→ supports entering the network branch; still requires path/pair validation

storage latency rises before all ranks enter file wait
→ supports the storage branch

GPU power falls only after completed steps stop
→ consequence, not the first anomaly

all metrics flat together or one host disappears
→ observability failure or evidence gap until verified
```

## 8. Return evidence, not a root-cause verdict

Use this natural-language output:

```text
Job/attempt and allocation verified
Data source and covered time range
Training-step to wall-clock mapping
Healthy and incident windows

Metric coverage and data-quality issues
Earliest observed system anomaly
Affected hosts/devices/path
Temporal relationship to the training symptom
Healthy-window comparison
Recovery relationship

Hypotheses supported at the layer level
Hypotheses weakened
Exact evidence gaps
Recommended next read-only check or symptom-runbook branch
```

Status must be one of:

```text
ready          identity, coverage, and relevant evidence are trustworthy
partial        useful evidence exists but a named metric/host/window is missing
inconclusive   data cannot distinguish the requested branches
wrong-series   queried data belongs to another job/attempt/allocation
unsupported    no safe query capability or retained data exists
```

Never turn temporal correlation alone into root cause. Return the time-series facts to `/system-evidence-diagnosis`, which combines them with logs, stacks, communicator, kernel, topology, and scheduler evidence and performs the next major-skill handoff.
