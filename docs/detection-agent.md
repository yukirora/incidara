# Detection Architecture

## 1. Where It Runs

Two containers sharing a DB. No k8s service. No ConfigMap.

```
┌──────────────────────────────┐   ┌──────────────────────────────────────┐
│  patrol_cron container       │   │  detection-agent container            │
│  (always running, no LLM)    │   │  (on-demand, Chat UI tasks)           │
│                              │   │                                       │
│  Runs collectors on schedule │   │  PATROL: survey → detect → validate  │
│  Each collector run feeds    │   │    → attribute → localize → act      │
│  all rules bound to it:      │   │    → synthesize → graduate            │
│    result = coll.collect()   │   │                                       │
│    for rule in bound_rules:  │   │  INVESTIGATE: picks up create_task   │
│      state = load_state()    │   │    findings → records verdict         │
│      findings, state =       │   │                                       │
│        analyze(result, state)│   │  CRUDs rules via MCP tools            │
│      save_state(state)       │   │  Manages graduation in DB            │
│      dedup + execute(findings│   │                                       │
│                              │   │  MCP tools: data access + rule CRUD  │
│                              │   │                                       │
│  Built-in collectors:        │   │                                       │
│    PrometheusCollector       │   │                                       │
│    SshCollector              │   │                                       │
│    JobLogsCollector          │   │                                       │
│    NodeLogsCollector         │   │                                       │
│    DbQueryCollector          │   │                                       │
│    ApiCollector              │   │                                       │
└────────────┬─────────────────┘   └──────────────────┬───────────────────┘
             │                                        │
             │       ┌────────────────────┐           │
             └───────┤  evidence DB       ├───────────┘
                     │                    │
                     │  collectors        │  collector config + schedule
                     │  patrol_rules      │  binds_to + analyze_code + stage
                     │  rule_state        │  per-rule state dict (JSONB, keyed by target_id)
                     │  patrol_findings   │  results + verdicts
                     │  switch_state      │  (existing, kept for switch data)
                     │  switch_check_log  │  (existing, kept for switch history)
                     └────────┬───────────┘
                              │
                     submit_triage_alert()
                              │
                              ▼
                    ┌──────────────────┐
                    │  alert-manager   │
                    │  cordon / alert  │
                    └──────────────────┘
```

## 2. Three Detection Modes

```
REACTIVE    "The phone rang, answer it"     Something already detected → respond
PROACTIVE   "Go patrol the neighborhood"    Nobody noticed → go find problems
DIAGNOSTIC  "Why did this job fail?"        User asks → agent investigates
```

## 3. Core Model: Collectors + Rules

Collectors and rules are separate. A collector runs on its schedule, produces raw data. Rules bind to a collector, receive the data, and produce findings. One expensive collection feeds multiple cheap rules.

**Collectors are stateless.** They pull data from sources (Prometheus, SSH, logs, DB, API) and return raw `CollectionResult`.

**Rules own their state.** `analyze()` receives old state from the engine, computes new state from `old_state + collected_data`, returns it. Engine persists blindly. No state logic in the engine.

### DB Schema

```sql
CREATE TABLE collectors (
    name            TEXT PRIMARY KEY,
    description     TEXT,
    schedule_sec    INT NOT NULL,
    enabled         BOOLEAN DEFAULT TRUE,
    created_by      TEXT NOT NULL,

    target_type     TEXT NOT NULL,        -- 'node' | 'switch' | 'job' | 'query_result'
    target_filter   JSONB,               -- {hostname_pattern, sample, schedulable, ...}

    sources         JSONB NOT NULL,       -- [{type, name, config}, ...]
    -- type: prometheus | ssh | job_logs | node_logs | db_query | api

    last_run        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE patrol_rules (
    rule_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    binds_to        TEXT NOT NULL REFERENCES collectors(name),
    analyze_code    TEXT NOT NULL,  -- def analyze(collected, state) -> (list[Finding], new_state)
    stage           TEXT NOT NULL DEFAULT 'create_task',
    enabled         BOOLEAN DEFAULT TRUE,
    created_by      TEXT NOT NULL,
    graduated_from  TEXT,
    graduated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
-- Track record is computed from patrol_findings, not cached here.
-- Example: windowed accuracy for graduation decisions:
--   SELECT count(*) AS total,
--          count(*) FILTER (WHERE verdict = 'confirmed') AS confirmed
--   FROM patrol_findings
--   WHERE rule_id = ? AND detected_at > NOW() - INTERVAL '2 weeks';

CREATE TABLE rule_state (
    rule_id         TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    state_data      JSONB NOT NULL DEFAULT '{}',
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (rule_id, target_id)
);

CREATE TABLE patrol_findings (
    finding_id      SERIAL PRIMARY KEY,
    rule_id         TEXT REFERENCES patrol_rules(rule_id),
    severity        TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    action          TEXT NOT NULL,
    action_params   JSONB,
    evidence        JSONB,
    confidence      FLOAT DEFAULT 1.0,
    verdict         TEXT,
    task_id         INT,
    resolved        BOOLEAN DEFAULT FALSE,
    detected_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_findings_rule_detected ON patrol_findings(rule_id, detected_at);
CREATE INDEX idx_findings_target_detected ON patrol_findings(target_id, detected_at);
CREATE INDEX idx_findings_open ON patrol_findings(resolved, detected_at) WHERE NOT resolved;
```

### Core Data Types

```python
@dataclass
class CollectionResult:
    collector_name: str
    targets: list[TargetData]
    errors: list[str]
    duration: float

@dataclass  
class TargetData:
    id: str           # hostname, switch_name, job_name
    type: str         # "node" | "switch" | "job"
    payload: Any      # collector-specific raw data
    meta: dict        # {ip, switch_type, ...}

@dataclass
class Finding:
    target_id: str
    severity: str
    action: str       # "cordon_node" | "cordon_switch_nodes" | "alert" | "create_task"
    action_params: dict
    evidence: dict
    confidence: float = 1.0
```

### Cron Loop

`analyze()` is called **once per rule** with the full `CollectionResult`. The rule iterates targets internally and manages its own state dict keyed by target ID.

```python
SAFE_GLOBALS = {"__builtins__": {}, "Finding": Finding, "math": math, "re": re}
ANALYZE_TIMEOUT_SEC = 300  # 5 min default; large fleets (1000+ targets) need headroom

while True:
    for collector in db.query("SELECT * FROM collectors WHERE enabled"):
        if now() - collector.last_run < timedelta(seconds=collector.schedule_sec):
            continue

        try:
            result = run_collector(collector)  # pure I/O, no state
        except Exception as e:
            log.error(f"Collector {collector.name} failed: {e}")
            # Still update last_run so we don't hammer a failing source
            db.execute("UPDATE collectors SET last_run = NOW() WHERE name = ?", collector.name)
            continue

        for rule in db.query("SELECT * FROM patrol_rules WHERE binds_to = ? AND enabled", collector.name):
            try:
                state = db.load_rule_state(rule.rule_id)  # full dict keyed by target_id
                findings, new_state = run_sandboxed(
                    rule.analyze_code,
                    {"collected": result, "state": state, **SAFE_GLOBALS},
                    timeout=ANALYZE_TIMEOUT_SEC
                )
                db.save_rule_state(rule.rule_id, new_state)
                for f in findings:
                    if db.finding_exists(rule.rule_id, f.target_id, f.action):
                        continue  # dedup: skip if unresolved finding already exists
                    execute_finding(f, rule.stage)
                    db.insert("patrol_findings", ...)
            except Exception as e:
                log.error(f"Rule {rule.rule_id} failed: {e}")
                continue  # one bad rule doesn't halt the loop

        db.execute("UPDATE collectors SET last_run = NOW() WHERE name = ?", collector.name)
    sleep(cycle_interval)
```

### analyze() Interface

Called once per rule per collection cycle. The rule owns the full state dict, keyed by target ID.

```python
def analyze(collected, state):
    """
    Args:
        collected: CollectionResult with all targets from the collector
        state: dict keyed by target_id — persisted across cycles ({} on first run)

    Returns:
        (list[Finding], new_state)
        new_state — JSON-serializable dict, persisted by engine as-is

    Constraints (enforced by sandbox):
        - No imports, no I/O, no network — pure computation over collected data
        - Must return within ANALYZE_TIMEOUT_SEC (default 300s)
        - Only Finding, math, re available in scope
    """
    findings = []
    for t in collected.targets:
        target_state = dict(state.get(t.id, {}))
        # ... analyze t.payload, update target_state, append findings ...
        state[t.id] = target_state
    return findings, state
```

## 4. Action Engine

```python
# Minimum confidence required per stage (higher stages demand higher confidence)
STAGE_MIN_CONFIDENCE = {"create_task": 0.3, "submit_alert": 0.7, "auto_cordon": 0.9}

def execute_finding(finding: Finding, rule_stage: str):
    # Drop low-confidence findings that don't meet the stage threshold
    if finding.confidence < STAGE_MIN_CONFIDENCE.get(rule_stage, 0.5):
        return

    if rule_stage == "create_task":
        # Stage 1: demote cordon/alert actions to investigation
        if finding.action in ("cordon_node", "cordon_switch_nodes", "alert"):
            finding.action = "create_task"

    if finding.action == "cordon_node":
        submit_triage_alert(hostname=finding.target_id, action="cordon",
                            triaged_label=finding.action_params.get("triaged_label"))

    elif finding.action == "cordon_switch_nodes":
        nodes = lookup_node_switch(finding.target_id)
        for node in nodes:
            submit_triage_alert(hostname=node, action="cordon")

    elif finding.action == "alert":
        submit_triage_alert(hostname=finding.target_id, action="alert")

    elif finding.action == "create_task":
        create_chat_ui_task(title=..., prompt=...)
```

## 5. Graduation Pipeline

All promotion/demotion decisions are computed from `patrol_findings` using a rolling time window (default 2 weeks). No cached counters — one source of truth.

```
Stage 0: log_only
  Rule fires → finding logged to DB + stdout. No external action.
  Promote: manual review confirms findings look correct → Stage 1

Stage 1: create_task
  Rule fires → agent task → agent investigates → records verdict
  Promote: ≥5 findings in window, accuracy ≥80% → Stage 2
  Demote:  ≥5 findings in window, accuracy <50% → disable

Stage 2: submit_alert
  Rule fires → auto-alert (no cordon). Agent tracks outcomes.
  Promote: ≥10 findings in window, accuracy ≥90% → Stage 3
  Demote:  accuracy <70% in window → Stage 1

Stage 3: auto_cordon
  Rule fires → auto-cordon. Agent monitors validation results.
  Accuracy <90% in window → Stage 2
```

## 6. Agent Skills

Three skills, each with a clear job. All use the same MCP tools for data access.

### survey-cluster
Trigger: cron creates Chat UI task every 30 min. Or user invokes manually.

Finds what needs investigation. Never acts directly.

1. **Survey** — read cluster state via MCP tools:
   - Node status overview (count per status)
   - Recent triage events (nodes that changed status)
   - Job failure hotspots (nodes with most failures)
   - VC failure rates (per-VC vs cluster avg)
   - Unacted alerts (alerts on available nodes)
   - Switch health data (from collector DB output)
   - Feishu user reports (unprocessed)
   - Validation gaps (stuck validations)

2. **Detect** — find anomalies (temporal, spatial, correlation, signal-gap, absence)

3. **Delegate** — for each suspicious signal, create Chat UI task → triggers `investigate-finding`

### investigate-finding
Trigger: task from `survey-cluster`, or cron rule fires at stage 1, or user report.

Takes a specific signal and investigates it.

1. **Validate** — "If we do nothing, will this keep happening?" Rule out noise.
2. **Attribute** — hardware / platform / user code / transient
3. **Localize** — node → switch → rack → fleet
4. **Act** — cordon, fix, alert, close. Record verdict (confirmed/rejected).
5. **Learn** — if same pattern seen 3+ times → triggers `manage-rules`

### manage-rules
Trigger: called by `investigate-finding` after pattern recognized, or periodic review.

Creates rules from discovered patterns. Graduates existing rules.

1. **Synthesize** — write `analyze()` code + create collector if needed → insert into DB (stage 1)
2. **Review** — check rule accuracy from patrol_findings
3. **Graduate** — promote (stage 1→2→3), demote, disable, or refine
4. **Refine** — narrow target, adjust threshold, fix false positives

## 7. Concrete Examples

### Switch Monitor

```yaml
# Collector
name: "switch_health"
schedule_sec: 120
target_type: "switch"
target_filter: {"from_inventory": true}
sources:
  - type: "ssh"
    name: "health_check"
    config:
      collector_variant: "switch"
      concurrency: 64
```

```python
# Rule: switch_health_v1
# binds_to: "switch_health"
# stage: log_only (start with logging, promote after review)
#
# Port error deltas are already computed by check_switch() in the collector
# and baked into the reason string — no need to track baselines here.
# Fail count resets after threshold is exceeded (matches existing cron behavior).

def analyze(collected, state):
    findings = []
    for t in collected.targets:
        ts = dict(state.get(t.id, {}))
        reason = t.payload.get("reason", "")

        # Track consecutive failures
        fc = ts.get("fail_count", 0)
        if t.payload.get("ok"):
            fc = 0
        else:
            fc += 1

        # Classification — only one finding per target per cycle
        if fc >= 5:
            findings.append(Finding(
                target_id=t.id, severity="critical",
                action="cordon_switch_nodes",
                action_params={"alertname": "SwitchUnreachable"},
                evidence={"fail_count": fc, "reason": reason}))
            fc = 0  # reset after firing (avoid re-firing every cycle)
        elif "psu" in reason.lower():
            findings.append(Finding(
                target_id=t.id, severity="critical",
                action="cordon_switch_nodes",
                action_params={"alertname": "SwitchPSUFailure"},
                evidence={"reason": reason}))
        elif "short uptime" in reason.lower():
            findings.append(Finding(
                target_id=t.id, severity="warning",
                action="alert",
                action_params={"alertname": "SwitchRebooted"},
                evidence={"reason": reason}))
        elif "port_err_delta" in reason:
            findings.append(Finding(
                target_id=t.id, severity="warning",
                action="create_task",
                evidence={"reason": reason}))

        ts["fail_count"] = fc
        state[t.id] = ts
    return findings, state
```

### ECC Error

```yaml
# Collector
name: "nvidia_ecc"
schedule_sec: 60
target_type: "node"
target_filter: {"schedulable": true}
sources:
  - type: "prometheus"
    name: "ecc_check"
    config:
      query: 'nvidiasmi_ecc_error_count{type="double"} > 0'
```

```python
def analyze(collected, state):
    findings = []
    for t in collected.targets:
        new_state = dict(state.get(t.id, {}))
        fc = new_state.get("fail_count", 0)
        
        if t.payload:  # ECC > 0 for this node
            fc += 1
        else:
            fc = 0
        new_state["fail_count"] = fc
        
        if fc >= 5:
            findings.append(Finding(
                target_id=t.id, severity="critical",
                action="cordon_node",
                action_params={"triaged_label": "triaged_hardware"},
                evidence={"ecc_fail_count": fc}))
        
        state[t.id] = new_state
    return findings, state
```

### FM Down

```yaml
# Collector
name: "ssh_fm_check"
schedule_sec: 1800
target_type: "node"
target_filter: {"hostname_pattern": "h200-*", "sample": 50}
sources:
  - type: "ssh"
    name: "fm_status"
    config:
      command: "systemctl is-active nv-fabricmanager"
```

```python
def analyze(collected, state):
    findings = []
    for t in collected.targets:
        new_state = dict(state.get(t.id, {}))
        fc = new_state.get("fail_count", 0)
        
        if t.payload.get("ok"):
            fc = 0
        else:
            fc += 1
        new_state["fail_count"] = fc
        
        if fc >= 3:  # FM is checked less frequently, lower threshold
            findings.append(Finding(
                target_id=t.id, severity="warning",
                action="create_task",
                evidence={"fm_fail_count": fc, "output": t.payload.get("output")}))
        
        state[t.id] = new_state
    return findings, state
```

## 8. From NFD to patrol_cron

| NFD Component | Fate | Why |
|---|---|---|
| **PrometheusClient** | → `patrol_cron/collectors/prometheus.py` | Keep AS-IS |
| **JobLogsClient** | → `patrol_cron/collectors/job_logs.py` | Keep AS-IS |
| **NodeLogsClient** | → `patrol_cron/collectors/node_logs.py` | Keep AS-IS |
| **JobMetadataClient** | → `patrol_cron/collectors/api.py` | Keep AS-IS |
| **Executor** | → `patrol_cron/engine.py` | Refactor: dispatch to collectors |
| **Resolver** | → `patrol_cron/resolver.py` | Keep, generalize for any entity |
| **Analysis Executor** | → `patrol_cron/analyzer.py` | exec analyze_code, manage state |
| **Alert submission** | → `patrol_cron/actions.py` | Keep format, add new action types |
| **Scheduler** | **Replace** — cron loop over collectors table | |
| **Pattern Registry** | **Replace** — patrol_rules + collectors DB tables | |
| **Redis** | **Remove** — single process | |

### patrol_cron directory

```
patrol_cron/
  collectors/
    prometheus.py        → PrometheusCollector (copied from NFD)
    job_logs.py          → JobLogsCollector (copied from NFD)
    node_logs.py         → NodeLogsCollector (copied from NFD)
    ssh.py               → SshCollector (NEW — node + switch variants)
    db_query.py          → DbQueryCollector (NEW)
    api.py               → ApiCollector (copied from NFD + Feishu)
  resolver.py            → TargetResolver (copied + generalized)
  engine.py              → CollectionEngine (dispatch to collectors)
  analyzer.py            → AnalysisEngine (exec analyze_code, manage state)
  actions.py             → ActionEngine (submit alerts, create tasks)
  db.py                  → DB access (rules, state, findings)
  main.py                → Cron loop
```

## 9. Implementation Plan

### Phase 1: Data Access + Engine (5-7 days)

Give the agent cluster data access and build the cron engine.

**Step 1 — MCP tools from NFD clients (2 days)**
- Copy PrometheusClient, JobLogsClient, NodeLogsClient, JobMetadataClient into MCP tools
- Agent can now query Prometheus metrics, read job logs, read node logs, query job metadata on-demand

**Step 2 — DB tables + engine scaffold (1 day)**
- `collectors`, `patrol_rules`, `rule_state`, `patrol_findings` tables
- `patrol_cron/` directory structure
- Rule CRUD MCP tools: create_collector, create_rule, update_rule_stage, etc.

**Step 3 — Switch health collector (2 days)**
- Build SSH collector: reads switches from DB, runs health check commands (concurrency=64)
- Writes raw results to DB on schedule (every 2 min)
- No classification logic — pure data collection
- Agent reads results from DB, never touches SSH directly

**Step 4 — Cron engine (2 days)**
- `main.py` cron loop: for each due collector → collect → for each bound rule → load state → exec analyze() → save state → execute findings
- Action engine: submit_triage_alert, create_chat_ui_task
- Chat UI API client for investigation tasks

### Phase 2: Agent Skills + Proactive Detection (3-5 days)

**Build three skills:**

- `survey-cluster` — reads all 8 data sources, detects anomalies, delegates to investigate-finding
- `investigate-finding` — validates, attributes, localizes, acts, records verdict, triggers manage-rules
- `manage-rules` — synthesizes rules from patterns, graduates existing rules

**First proactive case: switch health**

Agent surveys → reads switch collector data from DB → detects port errors growing → delegates to investigate-finding → agent correlates with node failures via MCP tools → attributes root cause → acts. After 3+ occurrences of the same pattern → manage-rules creates a rule in DB. Engine picks it up next cycle.

**Agent → rule → automation loop**
- Rule fires at stage 1 → engine creates task → investigate-finding picks it up → records verdict
- Accuracy ≥80% → manage-rules promotes to stage 2
- Accuracy ≥90% over 2 weeks → manage-rules promotes to stage 3
- Rule auto-runs. Agent freed for next discovery.

### Phase 3: More Collectors + Rules (2-3 days each)

SSH node checks (FM down, GPU throttle), DB queries (node cycling), API (Feishu reports), NFD data sources (ECC, NVMe, job failures).

### Phase 4: NFD Migration (weeks, gradual)

Copy NFD's proven patterns into collectors + rules. Run parallel. Disable NFD one pattern at a time.