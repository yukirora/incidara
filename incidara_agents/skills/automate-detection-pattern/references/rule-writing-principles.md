# Rule-Writing Principles

When writing `analyze(collected, state) -> RuleResult(observations, state)`, follow these rules.

For the meaning of Xid error codes used in examples below, see `../../../skills/triage-nodes/references/xid-reference.md`.

## 1. Use State, Not Static Thresholds

**Bad:** `if count > 23` — what if node reboots and count resets?
**Good:** `if count > state.get("prev_count", 0)` — detects delta between checks. State survives reboots naturally: count drops → delta is negative → no alert (reboot cleared dmesg).

## 2. Use Deltas, Not Absolutes

**Bad:** `if timestamp > 2595924` — breaks on reboot, timestamps reset
**Good:** `if count > prev_count` — always works. If count increased = new events since last check.

## 3. No Hardcoded Timestamps

**Bad:** `if dmesg_timestamp > 2595924` — dead after reboot
**Good:** Track last_seen_timestamp in state. Compare: `if dmesg_timestamp > state.get("last_seen", 0)`

## 4. Handle Reboot Gracefully

Node reboot → dmesg resets → all counters go to 0 or small values. This looks like "recovery" — not a problem. Your code should detect this:
```python
if count < prev_count:
    # Node rebooted or dmesg rotated. Reset baseline.
    new_state["prev_count"] = count
    new_state["prev_timestamp"] = 0
    return RuleResult(observations=[], state=new_state)
```

But also detect: **multiple Xid 31 on a fresh boot** — if count ≥ 3 on first check after reboot, that's still bad.

## 5. Bind to Existing Collectors — Carefully

Before creating a new collector, check `list_collectors(has_rules=True)` — is there already a collector probing the same targets?

**If yes, and the existing collector already collects the data you need** → bind your new rule to it directly. One collector, multiple rules. No changes needed.

**If you need additional SSH commands / data sources** → adding to an existing collector is risky. New commands may fail and mark the whole target `ok=False`, preventing existing rules from running. Safer: create a new collector.

**Rule of thumb:** bind to existing collector if data is sufficient. If new sources are needed, prefer a new collector over modifying one that feeds other rules.

## 6. Before Creating a Rule, Check for Existing Coverage

Before `create_rule`, always check: does an existing rule already cover this?

1. `list_rules` → read ALL rule descriptions. Which rules bind to the same collector?
2. `get_rule_detail(rule_id)` → read the actual analyze_code. What does it detect?
3. **If an existing rule already detects this pattern** → do NOT create a new rule. Instead:
   - Improve existing: `update_rule_code` to increase sensitivity or adjust observation lifecycle thresholds
   - Don't create per-node rules: detection rules should be general patterns over a class of targets, not hardcoded to a single hostname. If you need previous counter values or seen event IDs, store them under the target key in `RuleResult.state`.
4. **Only create a new rule if no existing rule covers this pattern.**

## 7. Rules Are General, Not Per-Node

A detection rule detects a pattern across a **class of targets** (all B300 nodes, all IB switches). It should NOT hardcode a single hostname or baseline timestamp. That's brittle and creates rule bloat.

**Bad:** `TARGET_HOST = "lg-cmc-demo-r01u01-b300-000001"` — breaks when node reboots, needs a new rule per node
**Good:** Use `RuleResult.state` to track private per-node memory within a general rule. `new_state[hostname] = {...}` scales to all nodes without per-node rules.

## 8. Evidence MUST Include Raw Collector Output — REQUIRED

Every bad `Observation.evidence` dict MUST include the raw collector output that triggered the detection. By the time someone investigates (hours or days later), the original source (dmesg buffer, log file, Prometheus query result) may have rotated. The evidence field copied into the persisted finding is the only permanent record.

**Required field:** `"raw_output"` — the complete or truncated raw data that triggered this finding.

```python
observations.append(Observation(
    signal_key="xid_error",
    target_id=hostname,
    status="bad",
    severity="critical",
    action="cordon_node",
    ...
    evidence={
        "raw_output": dmesg_output.strip(),  # REQUIRED — ALL raw collector data
        "xid_codes": [94, 137],                      # optional parsed summary
        "unique_pci": 1,
    },
    lifecycle={"kind": "condition", "open_after_consecutive": 1, "close_after_healthy": 2},
))
```

Without `raw_output`, investigations become impossible when the original data source rotates. Every rule MUST include this field.

## Payload Shape Is Collector-Specific

`TargetData.payload` is NOT a global schema — it depends on the collector source type:

- **SSH collector**: `{"ssh_ok": bool, "outputs": {...}, "ssh_error": "..."}`
- **Prometheus collector**: query result with metric values and timestamps
- **DB query collector**: rows from SQL query
- **Job log collector**: `{"matched_nodes": {"node-1": [...]}}` — per-JOB target, with node entries nested inside
- **Multi-source collector**: `target.payload` is keyed by source name (e.g. `payload["ssh_dmesg"]`, `payload["prom_metrics"]`)

Only read `payload["ssh_ok"]` when the rule is bound to an SSH-source payload, or when the specific collector implementation/documentation defines that field. Do not treat collection-run metadata such as `collector_status` as target health.

## ⚠️ Multi-Source Merge Only Works When Target IDs Match

The engine merges multi-source payloads by `target.id`. If sources produce different target types, they WILL NOT merge:

- **SSH + Prometheus**: both return `target.id = hostname` → merges correctly
- **SSH + job_logs (group_by="node")**: both return `target.id = hostname` → **merges correctly** ✅
- **SSH + job_logs (group_by="job")**: job_logs returns `target.id = job_key` → **never merges**

If you need both SSH data and job log data in one rule, set `group_by: "node"` in the job_logs source config. See `references/schema.md` → "Multi-Source With Different Target Types".

## Common Fixes for False Positive Patterns

- **Nodes already in repair (cordoned/ua)**: add `"schedulable": true` to `target_filter`. This skips nodes being repaired — FM-down/GPU errors are expected there.
- **SSH collection failures mistaken for real issues**: `payload["ssh_ok"]` is valid for SSH-source payloads, but not for every collector. For multi-source collectors, SSH output may be nested under the source name. Check `get_rule_detail`, collector source config, and a sample collector output before adding a payload status guard. Collection-run metadata such as `collector_status` is not target health.
- **Transient single-occurrence**: use `Observation.lifecycle["open_after_consecutive"]` to require 2+ bad observations. The engine tracks consecutive open/close counters; rule state should not.
- **Stale log events from boot/reboot cycles**: Use `node_logs` source instead of SSH `grep` for reading system logs. `node_logs` automatically time-filters to `schedule_sec × 2` lookback window, so events from before the current run are excluded. SSH grep has no time filtering — it returns ALL matching lines regardless of age, causing false positives from reboot syslog entries. See `references/schema.md` → "Source Type Selection".

## Self-Review Checklist

Before finalizing any rule, verify:

- No hardcoded hostnames or timestamps? (survives reboots)
- Uses state-based deltas for counters, not static thresholds? (`count > prev_count`, not `count > 23`)
- Uses observation lifecycle for open/close thresholds? (`open_after_consecutive`, `close_after_healthy`, not manual finding lifecycle in rule state)
- Handles edge cases? (reboot resets dmesg, device disappears from `/dev`, variable device count)
- Dynamic device discovery? (`ls /dev/nvme*n1` not `for i in 1..16`)
- `evidence["raw_output"]` included? (REQUIRED — original data may rotate)
- **What failure modes am I missing?** — there are always two: the one you can see (SMART degradation) and the one you can't (controller death, device disappears). Did you catch both?
- **Is the data source correct?** — `job_logs` = application-level errors (CUDA error, DRAM ECC failure); `dmesg`/`node_logs` = kernel-level messages (Xid errors, driver failures). Don't confuse them. Follow the original pattern's source specification.
