# Structural Implementation

After diagnosing a structural issue (see `structural-diagnosis.md`), follow these procedures to implement the change.

## Split Rule — Accuracy Deadlock

When a rule mixes deterministic and uncertain signals, split into two rules on the same collector.

### When to split

- Confirmed findings cluster around deterministic signals (e.g., `critical_warning`, Xid 31/48/63)
- Rejected findings cluster around uncertain signals (e.g., `media_errors_increasing`, Xid 13/41)
- The uncertain signals drag overall accuracy below the promotion threshold

### Split pattern

```
Original rule:  X & (C or A)        ← mixed confidence, accuracy deadlock

Split:
  Hard rule:    X & C               → deterministic → cordon_node → promotes fast
  Soft rule:    X & A & extra       → uncertain + extra filter → alert → stays log_only
```

The soft rule **MUST** include an extra narrowing filter. Without it, you reproduce the same false positive problem.

### Extra filter options

| Filter type | Hard rule | Soft rule (with extra) | Example |
|-------------|-----------|------------------------|---------|
| Rate | delta > 0 | delta > 5 | media_errors growing slowly vs fast |
| Recurrence | `open_after_consecutive: 1` | `open_after_consecutive: 2` | transient Xid vs persistent Xid |
| Corroboration | signal_A alone | signal_A AND signal_B | job_logs alone vs job_logs + SSH ECC |
| Scope | all nodes | category == "h200" | if NFF only on certain SKUs |
| Temporal | count > 0 | count_in_1h > 3 | single event vs burst |

### Procedure

1. `create_rule` hard rule + soft rule (same `binds_to`)
2. **Re-link findings** from old rule to new rules:
   - `relink_findings(source_rule_id, target_rule_id, evidence_filter)` for each new rule
   - Hard rule: `evidence_filter` matches the deterministic signal fields
     (e.g., `{"has_field": "critical_warning_device"}`)
   - Soft rule: `evidence_filter` matches the uncertain signal fields
     (e.g., `{"has_field": "media_errors_device"}`)
   - **This preserves verdicts** — the hard rule may immediately qualify for promotion
3. `toggle_rule(old_rule_id, enabled=False)`
4. `test_rule_once` both new rules

### If no good extra filter exists for soft rule

Don't create the soft rule yet. Keep only the hard rule. Gather more data to find a good extra filter later.

## Merge Collectors — Same Sources + Same Cadence

### When to merge

Two collectors with identical sources and identical cadence. Different `target_filter` is NOT a blocker — rules filter in `analyze()` instead.

### Procedure

1. Pick collector to keep (prefer one with more rules, or right schedule)
2. `update_collector(name, merged sources, remove target_filter)`
3. Update ALL bound rules: payload changes from flat to keyed-by-source-name
   - `target.payload.get("ssh_ok")` → `target.payload.get("source_name", {}).get("ssh_ok")`
   - `target.payload.get("outputs", {})` → `target.payload.get("source_name", {}).get("outputs", {})`
4. Rebind rules from old collector → merged collector
5. Disable old collector
6. Clean up: remove stale lock/log files
7. Validate ALL affected rules with `test_rule_once`

### When NOT to merge

| Condition | Reason |
|-----------|--------|
| Different sources | Different data = different collectors |
| Different cadence that can't be unified | Can't run at two speeds |
| Merged collector too slow (>5 min cycle) | Too many targets for one cycle |
| Adding job_logs to SSH collector that feeds multiple rules | 429 risk for all bound rules |

## Split Collector — Different Scope/Cadence Needed

### When to split

| Condition | Example |
|-----------|---------|
| Rules need different cadence | Critical ECC = 5 min, NVMe SMART = 15 min |
| Rules need different sources | SSH-only vs SSH + job_logs (adding job_logs = 429 risk) |
| Collector runs >50% targets that some rules ignore | 4530 targets but rule only needs 1275 |

### Procedure

1. `create_collector` with right sources/filter/schedule
2. Move rule to new collector: `update_rule_code(rule_id, analyze_code)` — payload may change shape
   - If old collector was single-source → new collector may be single or multi-source
   - If payload shape changes: flat → keyed, update `analyze_code` accordingly
3. Disable old collector if orphaned (no remaining rules)
4. Validate moved rule with `test_rule_once`

## Add Source to Collector

### When to add

Rule needs data the collector doesn't provide, AND you have evidence the new source discriminates confirmed from rejected findings.

### ⛔ Evidence required

- Current source data is identical for confirmed and rejected (not discriminative)
- New source data differs between confirmed and rejected (discriminative)
- Example: "Rejected findings have no ECC errors in SSH data; confirmed findings all have ECC errors"

### Procedure

1. `update_collector(name, sources=[...existing + new_source])`
2. Update ALL bound rules for payload shape change (flat → keyed)
3. `test_rule_once` ALL affected rules

### ⚠️ Target ID compatibility

Multi-source merge groups targets by `target.id`. If two sources produce different target IDs, their payloads will NEVER merge:

| Source | target.id | Merges with SSH? |
|--------|-----------|------------------|
| SSH | hostname | ✅ Yes |
| Prometheus | hostname | ✅ Yes |
| job_logs (group_by="node") | hostname | ✅ Yes |
| job_logs (default, group_by="job") | job_key | ❌ Never |
| job_metadata | job_key | ❌ Never |

If you need both SSH and job_logs data → set `group_by: "node"` in the job_logs source config.

## Self-Review Checklist

Before finalizing any structural change, verify:

- **Is this collector redundant?** — does an existing collector already probe these same targets? Merge if possible.
- **Is this rule a duplicate?** — does an existing rule already detect this pattern? Read `get_rule_detail` for ALL rules on the same collector.
- **After split/merge, did I update payload access in ALL affected rules?** — flat → keyed payload is the #1 source of bugs after merge/split.
- **Did I re-link findings after splitting a rule?** — without re-linking, new rules start at 0 verdicts and can't promote even though verdicts exist on the old rule.
- **Did I clean up orphaned collectors?** — disable collectors with no rules, remove stale lock/log files.
- **Does multi-source merge actually work?** — SSH + job_logs needs `group_by: "node"` or target IDs won't match and payloads won't merge.
