# Collector & Rule Schema

## Collector

```json
{
  "name": "collector_name",
  "target_type": "node | switch | job",
  "schedule_sec": 300,
  "target_filter": {
    // --- Node targets ---
    "hostname_pattern": "*b300*",    // SQL LIKE (use * not %)
    "category": "b300",             // h200 | b300 | storage
    "schedulable": true,            // only nodes currently schedulable (Prometheus)
    "sample": 1,                    // limit targets for testing

    // --- Switch targets ---
    "type": "ib",                   // ib | ruijie | ufm
    "from_inventory": true,         // query switch_inventory table

    // --- Job targets ---
    "lookback_days": 60,            // time window for job pre-filter
    "job_filter": "gpu_count > 32 and virtualCluster == 'h200'",
    //   Safe fields: totalTaskNumber, totalGpuNumber, gpu_count (alias),
    //                username, name, virtualCluster, tags, executionType, retries
    //   Auto-moved to attempt_filter: state, launchedTime, createdTime, completedTime, nodes, taskRoles
    "attempt_filter": "state == 'FAILED' and launchedTime and now_ms - launchedTime > d(14)",
    //   All fields available. Helpers: now_ms, d(n), h(n), m(n)
  },

  // Single source: one entry, payload = source output directly
  // Multi source: multiple entries, payload = {source_name: source_output, ...}
  "sources": [
    {
      "type": "ssh",
      "name": "gpu_check",
      "config": {
        "commands": {"dmesg_xid": "sudo dmesg | grep -i NVRM || true", "ecc": "nvidia-smi ..."},
        // OR list: "commands": ["show version", "show fan"]
        "concurrency": 16,
        "timeout": 15,
        "user_env": "SSH_USER",          // env var name for SSH user
        "password_env": "SSH_PASSWORD",   // env var name for SSH password
        "interactive": true,             // true for switches (pexpect + password), false for nodes (subprocess + agent)
        "paging_cmd": "no cli session paging enable",  // interactive only
        "ssh_options": ["HostKeyAlgorithms=ssh-rsa"]    // extra SSH -o options
      }
    },
    {
      "type": "prometheus",
      "name": "ecc_count",
      "config": {
        "query": "nvidiasmi_ecc_error_count{type=\"double\"} > 0",
        "step": "60s"
      }
    },
    {
      "type": "node_logs",
      "name": "xid_errors",
      "config": {
        "log_paths": ["kern.log"],
        "patterns": [{"regex": ".*NVRM: Xid.*"}],
        "max_entries": 100
        // Time filtering is AUTOMATIC — collector uses schedule_sec * 2 as lookback window.
        // No need to parse timestamps in analyze().
      }
    },
    {
      "type": "job_logs",
      "name": "error_logs",
      "config": {
        "patterns": [{"regex": ".*ERROR.*"}, {"regex": ".*CUDA.*"}],
        "tail": true,
        "max_entries": 50,
        "group_by": "node"   // "job" (default) or "node". Use "node" to get per-node targets that merge with SSH/Prometheus.
      }
    },
    {
      "type": "job_metadata",
      "name": "job_info",
      "config": {}
    }
  ]
}
```

## Source Type Selection

### When to use `node_logs` vs `ssh`

| Use `node_logs` | Use `ssh` |
|---|---|
| Reading system logs (syslog, kern.log, dmesg) | Running diagnostic commands (nvidia-smi, ibstat, smart-log, ibstatus) |
| Need time-bounded results (auto lookback = schedule_sec × 2) | Need current state snapshots |
| Pattern matching on log lines | One-shot commands with structured output |
| Replay-safe (frozen payload = time-filtered) | Replay-safe (command output at point-in-time) |

**Key difference**: `node_logs` queries log-manager with `start-time`/`end-time` server-side filtering. Only log entries within the lookback window are returned. This means:
- No stale events from boot cycles or reboots
- No need to parse timestamps or filter by time in `analyze()`
- Replay cases work because the frozen payload already contains only time-filtered entries

**Common mistake**: Using SSH `grep` to read syslog without time filtering:
```json
// BAD: Returns ALL matching lines regardless of age (stale events from boot cycles)
{"type": "ssh", "config": {"commands": {"carrier": "grep 'Lost carrier' /var/log/syslog | tail -100"}}}

// GOOD: Returns only lines from the lookback window (auto = schedule_sec × 2)
{"type": "node_logs", "config": {"log_paths": ["/var/log/syslog"], "patterns": [{"regex": ".*Lost carrier.*ib[0-9]+.*"}], "max_entries": 100}}
```

### `node_logs` payload shape

Single source:
```python
target.payload = {"log_entries": [{"message": "Jun  9 05:18:32 node ib8: Lost carrier", "fields": {}}]}
```

Multi source (keyed by source name):
```python
target.payload = {
    "ib_syslog": {"log_entries": [{"message": "...", "fields": {}}]},
    "ib_check": {"ssh_ok": True, "outputs": {"ibstat": "..."}, "ssh_error": ""},
}
```

### `node_logs` config options

| Field | Required | Description |
|-------|----------|-------------|
| `log_paths` | Yes | Log files to read: `["syslog"]`, `["kern.log"]`, `["syslog", "kern.log"]` |
| `patterns` | Yes | Regex patterns: `[{"regex": ".*Lost carrier.*"}]` |
| `max_entries` | No | Max matched entries per target (default 100) |

Time filtering is **automatic** — the lookback window is `schedule_sec × 2` (e.g., 600s schedule → 1200s lookback). No config needed.

## CollectionResult / Frozen Input

Collectors return a `CollectionResult` to rule `analyze(collected, state)`. Replay fixtures store the same shape as frozen JSON:

```json
{
  "collector_name": "collector_name",
  "collector_status": "success",
  "errors": [],
  "duration": 0.0,
  "targets": [
    {
      "id": "node-1",
      "type": "node",
      "payload": {},
      "meta": {}
    }
  ]
}
```

In `analyze_code`, `collected` is a `CollectionResult` object:

```python
collected.collector_name  # collector name
collected.targets         # list[TargetData]
collected.errors          # collector-run errors
collected.duration        # collector-run duration
collected.run_time        # unix timestamp when collector ran (for time-relative logic in analyze)
```

Each target is a `TargetData` object:

```python
target.id       # hostname, switch name, or job name
target.type     # node | switch | job
target.payload  # raw collector-specific target data
target.meta     # target metadata such as ip, category, sku
```

`collector_status`, `errors`, and `duration` describe the collection run. They are not target health. Rule code should normally inspect `target.payload`, using fields proven by the collector source config, replay fixture, or a real sample from `run_collector_once`.

## Payload Shape

### Single source
```python
target.payload = {"ssh_ok": True, "outputs": {"dmesg_xid": "..."}, "ssh_error": ""}  # ssh
target.payload = {"metric": {...}, "values": [...]}                      # prometheus
target.payload = {"matched_nodes": {"node-1": [...]}}                    # job_logs (group_by="job")
target.payload = {"entries": [...], "source_jobs": ["job1", "job2"]}    # job_logs (group_by="node")
target.payload = {"totalGpuNumber": 1024, "state": "FAILED", ...}       # job_metadata
target.payload = {"log_entries": [{"message": "...", "fields": {}}]}    # node_logs
```

### Multi source (payload keyed by source name)
```python
target.payload = {
    "gpu_check": {"ssh_ok": True, "outputs": {"dmesg_xid": "...", "ecc": "..."}, "ssh_error": ""},
    "ecc_count": {"metric": {...}, "values": [...]},
    "xid_errors": {"entries": [...]},
}
```

## Rule (analyze function)

Rules should return `RuleResult`, not raw `(findings, state)`. The rule reports current observations; the engine owns persisted finding lifecycle:

- `Observation(status="bad")` means this signal is present now.
- `Observation(status="healthy")` means this signal is absent now and may close an active finding after the healthy threshold.
- `Observation(status="unknown")` means the rule could not determine status; active findings stay active by default.
- `Observation.lifecycle` declares how the engine opens, refreshes, and closes persisted findings.
- `RuleResult.state` is private rule memory only, used for parsing/delta detection such as previous counters or seen event IDs. Do not use it to track active finding IDs, deactivation, or duplicate suppression.

### Lifecycle protocol

Use `kind="condition"` for persistent health states. Condition rules must emit an observation every cycle for every target they evaluate, including healthy observations. The engine stores `bad_count`, `healthy_count`, active finding ID, and expiry in `detection_lifecycle_state`.

```python
condition_lifecycle = {
    "kind": "condition",
    "open_after_consecutive": 3,  # open after 3 bad observations
    "close_after_healthy": 2,     # close after 2 healthy observations
    "unknown_keeps_active": True,
}
```

Use `kind="event"` for edge-triggered evidence: a new XID appeared, ECC count increased, a new failed job set was observed, or a reboot event was parsed. Event rules emit only when there is a new bad event. Existing active findings for the same rule/target/action are refreshed instead of duplicated. Event lifecycle does not auto-close from silence; if the issue should deactivate after healthy observations, model it as a `condition`.

```python
event_lifecycle = {"kind": "event"}
```

For event rules, set `event_id` when the rule can identify the specific new edge. Keep event IDs stable for the same source event. Do not include timestamps from the current run unless they are part of the source event; otherwise every run looks new.

`signal_key` is the rule's internal signal identity. It separates lifecycle counters inside one rule, but findings dedupe at rule + target + action level. If two signals on the same node use the same action, they refresh one active finding rather than creating multiple tasks. Use different `action` only when the operational response is truly different.

```python
def analyze(collected, state):
    # All constants MUST be inside the function (sandbox constraint)
    PATTERN = re.compile(r'...')
    lifecycle = {
        "kind": "condition",
        "open_after_consecutive": 2,
        "close_after_healthy": 2,
        "unknown_keeps_active": True,
    }

    observations = []
    new_state = dict(state)

    for target in collected.targets:
        hostname = target.id
        meta = target.meta                       # {ip, category, sku}

        # Single source:
        if target.payload.get("ssh_ok") is False:
            continue
        outputs = target.payload.get("outputs", {})

        # Multi source:
        ssh_payload = target.payload.get("gpu_check", {})
        if ssh_payload.get("ssh_ok") is False:
            continue
        ssh_data = ssh_payload.get("outputs", {})
        prom_data = target.payload.get("ecc_count", {})

        # ... parse current target health ...
        has_issue = "Xid" in ssh_data.get("dmesg_xid", "")

        observations.append(Observation(
            signal_key="gpu_xid_condition",
            target_id=hostname,
            status="bad" if has_issue else "healthy",
            severity="critical",    # critical | warning | info
            action="stop_abnormal_job",   # cordon_node | drain_node | stop_abnormal_job | notify_abnormal_job | alert | cordon_switch_nodes | create_task
            evidence={
            "raw_output": ssh_data,   # REQUIRED: preserve the raw collector output that triggered this finding
            "summary": {...},         # optional: parsed summary
            ...},
            confidence=0.8,
            lifecycle=lifecycle,
        ))

    return RuleResult(observations=observations, state=new_state)
```

### Sandbox constraints
- Available: `re`, `math`, `Finding` (legacy), `Observation`, `RuleResult`, builtins (str, int, list, dict, set, sorted, len, min, max, sum, any, all, enumerate, range, zip, float, bool, True, False, None, round, abs)
- NOT available: `import`, `open`, `print`, `os`, `sys`, `__import__`, `eval`, `exec`
- Module-level variables invisible inside `analyze()` — define constants inside function

## Working Example: Node Health (single source SSH)

### Collector
```json
{
  "name": "b300_gpu_health",
  "target_type": "node",
  "schedule_sec": 300,
  "target_filter": {"hostname_pattern": "*b300*", "category": "b300"},
  "sources": [{"type": "ssh", "name": "gpu_check", "config": {
    "commands": {
      "dmesg_xid": "sudo dmesg | grep -i NVRM || true",
      "ecc_errors": "nvidia-smi --query-gpu=ecc.errors.uncorrected.volatile.total --format=csv,noheader"
    },
    "user_env": "SSH_USER", "password_env": "SSH_PASSWORD",
    "concurrency": 16, "timeout": 15
  }}]
}
```

### Rule
```python
def analyze(collected, state):
    lifecycle = {"kind": "condition", "open_after_consecutive": 1, "close_after_healthy": 2, "unknown_keeps_active": True}
    observations = []
    new_state = dict(state)

    for t in collected.targets:
        outputs = t.payload.get("outputs", {})
        dmesg = outputs.get("dmesg_xid", "")
        has_xid = "NVRM" in dmesg
        observations.append(Observation(
            signal_key="gpu_xid_health",
            target_id=t.id,
            status="bad" if has_xid else "healthy",
            severity="critical",
            action="cordon_node",
            evidence={"raw_output": dmesg} if has_xid else {"issue_clear": True},
            confidence=0.95 if has_xid else 1.0,
            lifecycle=lifecycle,
        ))

    return RuleResult(observations=observations, state=new_state)
```

See live: `get_collector_health("b300_gpu_health")`, `get_rule_detail("b300_gpu_xid_v1")`

## Working Example: Job Detection (single source job_metadata)

### Collector
```json
{
  "name": "job_waste_scan",
  "target_type": "job",
  "schedule_sec": 3600,
  "target_filter": {
    "lookback_days": 60,
    "job_filter": "gpu_count > 32 and virtualCluster == 'h200'",
    "attempt_filter": "state == 'RUNNING' and launchedTime and now_ms - launchedTime > d(14)"
  },
  "sources": [{"type": "job_metadata", "name": "running_jobs", "config": {}}]
}
```

### Rule
```python
def analyze(collected, state):
    lifecycle = {"kind": "condition", "open_after_consecutive": 2, "close_after_healthy": 1, "unknown_keeps_active": True}
    observations = []
    new_state = dict(state)

    for job in collected.targets:
        gpu = job.payload.get("totalGpuNumber", 0)
        vc = job.payload.get("virtualCluster", "")

        observations.append(Observation(
            signal_key="long_running_large_job",
            target_id=job.id,
            status="bad",
            severity="warning",
            action="create_task",
            evidence={"gpu_count": gpu, "vc": vc},
            confidence=0.8,
            lifecycle=lifecycle,
        ))

    return RuleResult(observations=observations, state=new_state)
```

## ⚠️ Multi-Source With Different Target Types — READ THIS

**Multi-source merge groups targets by `target.id`.** If two sources produce different target IDs, their payloads will NEVER merge.

- SSH source → `target.id = hostname` (e.g. `h200-000001`)
- Prometheus source → `target.id = hostname` (e.g. `h200-000001`) ← **merges with SSH**
- job_logs (group_by="job") → `target.id = job_key` ← **NEVER merges with SSH**
- job_logs (group_by="node") → `target.id = hostname` ← **merges with SSH** ✅
- job_metadata source → `target.id = job_key` ← **NEVER merges with SSH**

### When you need both SSH data AND job log data for the same rule

**Use `group_by: "node"` in the job_logs source.** This makes job_logs return per-node targets that merge naturally with SSH/Prometheus:

```json
{
  "name": "nvidia_ecc_health",
  "target_type": "node",
  "sources": [
    {"type": "ssh", "name": "ecc_check", "config": {
      "commands": {"ecc_counts": "nvidia-smi --query-gpu=..."},
      "concurrency": 32
    }},
    {"type": "job_logs", "name": "ecc_job_logs", "config": {
      "patterns": [{"regex": ".*DRAM ECC failure.*"}],
      "group_by": "node",
      "max_entries": 100
    }}
  ]
}
```

Rule code — just access both sources by name (they're merged by hostname):
```python
def analyze(collected, state):
    lifecycle = {"kind": "event"}
    observations = []
    new_state = dict(state)

    for t in collected.targets:
        ssh = t.payload.get("ecc_check", {})
        job = t.payload.get("ecc_job_logs", {})

        outputs = ssh.get("outputs", {})
        ecc_raw = outputs.get("ecc_counts", "")
        job_entries = job.get("entries", [])

        has_ecc = any(int(v.strip()) > 0 for v in ecc_raw.split() if v.strip().isdigit())
        has_job_error = len(job_entries) > 0

        if has_ecc or has_job_error:
            observations.append(Observation(
                signal_key="nvidia_ecc_error",
                target_id=t.id,
                status="bad",
                severity="critical",
                action="cordon_node",
                evidence={"raw_output": ecc_raw, "job_errors": job_entries[:10]},
                lifecycle=lifecycle,
            ))
    return RuleResult(observations=observations, state=new_state)
```

### When to use group_by="job" vs group_by="node"

| Use case | group_by | Why |
|----------|----------|-----|
| Rule needs per-node findings (most detection rules) | `"node"` | Merges with SSH/Prometheus, rule iterates nodes |
| Rule needs per-job findings (wasteful job detection) | `"job"` | Target is the job itself, not a node |
| Combining SSH + job_logs in one collector | `"node"` | Required for merge to work |
| Standalone job_logs collector, no SSH | Either | `"node"` is usually simpler |

## Working Example: Multi-Source (SSH + Prometheus)

### Collector
```json
{
  "name": "gpu_full_check",
  "target_type": "node",
  "schedule_sec": 300,
  "target_filter": {"hostname_pattern": "*h200*"},
  "sources": [
    {"type": "ssh", "name": "hw_check", "config": {
      "commands": {"xid": "sudo dmesg | grep -i 'NVRM: Xid' || true"},
      "concurrency": 32
    }},
    {"type": "prometheus", "name": "ecc", "config": {
      "query": "nvidiasmi_ecc_error_count{type=\"double\"} > 0"
    }}
  ]
}
```

### Rule (multi-source payload)
```python
def analyze(collected, state):
    lifecycle = {"kind": "condition", "open_after_consecutive": 1, "close_after_healthy": 2, "unknown_keeps_active": True}
    observations = []
    new_state = dict(state)

    for t in collected.targets:
        # Access each source by name
        ssh = t.payload.get("hw_check", {})
        prom = t.payload.get("ecc", {})

        xid_output = ssh.get("outputs", {}).get("xid", "")
        has_ecc = bool(prom.get("values"))
        has_xid = "Xid" in xid_output

        observations.append(Observation(
            signal_key="gpu_ecc_and_xid",
            target_id=t.id,
            status="bad" if has_ecc and has_xid else "healthy",
            severity="critical",
            action="cordon_node",
            evidence={"ecc": has_ecc, "xid": has_xid, "raw_output": xid_output},
            lifecycle=lifecycle,
        ))

    return RuleResult(observations=observations, state=new_state)
```
