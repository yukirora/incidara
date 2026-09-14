# Pipeline Implementation

After diagnosing WHERE the pipeline is broken (see `pipeline-diagnosis.md`), fix the blocked stage.

## Fix cron dead

The collector process is not running on schedule.

1. Check the cron process: is the patrol-cron container/service running?
2. Restart if needed
3. Verify: `get_collector_health(collector_name)` → no longer overdue

## Fix no targets

The collector resolves 0 targets.

**Diagnose why:**
- Is `target_filter` too restrictive? → check `hostname_pattern`, `category`, `schedulable`
- Are there actually matching hosts? → `query_prometheus` to check if hosts exist
- Is the Prometheus/inventory source down? → check `PROMETHEUS_SERVER_URI`

**Fix:**
```
update_collector(name, target_filter={"hostname_pattern": "*b300*", "category": "b300"})
```

**Verify:** `run_collector_once(collector_name, sample=3)` → total > 0

## Fix collector execution failures

All targets fail when collecting data.

### Fix by error type

| Error | Fix |
|-------|-----|
| `exit code 1` from grep | Append `\|\| true` to grep commands |
| `Permission denied` | Add `sudo` prefix to commands |
| `command not found` | Use `which <cmd> && <cmd> \|\| echo NOT_INSTALLED` |
| `exit code 255` (SSH) | Check `user_env`/`password_env`. Ensure `SSH_AUTH_SOCK` mounted |
| `connection closed` (switch) | Set `"interactive": true` in source config |
| `timeout` | Increase `timeout` in source config |
| `dmesg: read kernel buffer failed` | Use `sudo dmesg` |

### Apply the fix

```
update_collector(name, sources=[{"type": "ssh", "config": {"commands": {...fixed...}}}])
```

### Verify

```
run_collector_once(collector_name, sample=3)
```
Check: `total > 0` and `ok > 0`. If some targets still fail, those are genuinely unreachable — not a collector bug.

### Per-collector-type fixes

**Prometheus collector:**
- "Query returned None" → check `PROMETHEUS_SERVER_URI` and `PAI_TOKEN`
- 0 results → metric name wrong, or genuinely no data

**Job logs collector:**
- "Failed to fetch jobs" → check `REST_SERVER_URI`
- Jobs found but 0 logs → check `LOG_MANAGER_USERNAME/PASSWORD`

**Node logs collector:**
- "401" → check `LOG_MANAGER_USERNAME/PASSWORD`
- Connection refused on 9103 → log-manager not running on node, use SSH instead

## Fix analyze() crashes

The rule's analyze() function errors when running.

### Common fixes

| Error | Fix |
|-------|-----|
| `SyntaxError` | Fix Python syntax in analyze_code |
| `__import__ not found` | Remove `import` — sandbox provides `re`, `math` |
| `X is not defined` | Move ALL constants inside `def analyze()` |
| `Observation is not defined` / `RuleResult is not defined` | Live patrol-cron image is stale or rule ran in old sandbox. Rebuild/restart patrol-cron, then smoke-test the rule. |
| `'dict' object has no attribute` | Use `Observation(target_id=..., status=..., lifecycle=...)` inside `RuleResult`, not dicts |
| `KeyError` | Use `payload.get("field")` instead of `payload["field"]` |

### Apply the fix

```
update_rule_code(rule_id, fixed_code)
```

### Verify

```
test_rule_once(rule_id, sample=3)  → no errors, lifecycle observations produce/refresh findings with raw_output when bad
test_rule_once(rule_id)            → full dataset clean
```

## Fix analyze() no match

The analyze() runs without error but produces no findings — logic doesn't match payload shape.

### Diagnose the mismatch

1. `get_rule_detail(rule_id)` → read analyze_code
2. `run_collector_once(collector_name, sample=1)` → see actual payload shape
3. Compare: does the code access fields that exist in the payload?

### Common mismatches

| Code assumes | But payload is | Fix |
|---|---|---|
| `payload["ssh_ok"]` | Multi-source: `payload["source_name"]["ssh_ok"]` | Access by source name |
| `payload.get("outputs", {}).get("xid")` | Command key is `"dmesg_xid"` | Match the exact command key |
| `payload["entries"]` | SSH source: `payload["outputs"]` | Use the right field for source type |
| `payload["matched_nodes"]` | group_by="node": `payload["entries"]` | Match the group_by setting |

See `references/schema.md` for the full payload shape reference.

### Apply and verify

Same as analyze() crashes: `update_rule_code` → `test_rule_once`

## Fix DB errors

Findings can't be written to or read from the database.

1. Check `EVIDENCE_DB_URL` is set and reachable
2. Check credentials are valid
3. Check schema exists: tables `patrol_findings`, `collector_snapshots`, etc.
4. If WAL corruption: `pg_resetwal -f` (last resort)

## Fix missing config

Environment variables not set in the container.

| Var missing | Affects | Add to |
|---|---|---|
| `SSH_AUTH_SOCK` | SSH collectors | Container env + volume mount |
| `PROMETHEUS_SERVER_URI` | Prometheus collectors | `.env` |
| `PAI_TOKEN` | Prometheus + job APIs | `.env` |
| `EVIDENCE_DB_URL` | DB queries, evidence storage | `.env` |
| `ALERT_MANAGER_URL` | Alert submission | `.env` |
| `INTERNAL_SECRET` | Alert-manager auth | `.env` |
| `LOG_MANAGER_USERNAME/PASSWORD` | Node logs collector | `.env` |
| `REST_SERVER_URI` | Job APIs | `.env` |

After adding env vars, restart the container.

## Self-Review Checklist

Before finalizing any pipeline fix, verify:

- **Did I fix the right stage?** — use `pipeline-diagnosis.md` to confirm WHERE data is blocked before fixing.
- **Did I verify end-to-end?** — `test_rule_once(rule_id)` must show lifecycle observations can produce/refresh findings with `raw_output` when bad.
- **Is this a config issue or a code issue?** — env vars and credentials are config; command syntax and analyze() logic are code. Fix the right one.
