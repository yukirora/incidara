# Structural Diagnosis

When a rule has a structural issue (duplicate collectors, wrong cadence, accuracy deadlock, or missing source), use this guide to diagnose what structural change is needed.

## Diagnosis Questions

### 1. Accuracy deadlock — does the rule mix confidence levels?

**When to check:** 🟠 Accuracy bad, and confirmed/rejected findings cluster around different signals.

**Check:**
- `list_findings(rule_id, verdict="confirmed", limit=10)` → what conditions triggered these?
- `list_findings(rule_id, verdict="rejected", limit=10)` → what conditions triggered these?
- `get_rule_detail(rule_id)` → read the analyze() code — does it have both deterministic and uncertain signals?

**Signal confidence levels:**

| Level | Description | Examples | Should promote? |
|-------|-------------|----------|-----------------|
| Deterministic | No benign explanation exists | `critical_warning != 0`, `mdstat degraded`, Xid 31/48/63, NVLink down | Yes — fast track |
| Correlated | Strongly associated with real failure, occasional NFF | `media_errors increasing`, Xid 79, dmesg controller errors (delta) | Maybe — needs verdicts |
| Uncertain | Often real but commonly software/transient/NFF | Xid 13/41 all GPUs, `percentage_used > 95%`, single lost carrier | No — stays `log_only` |

**Diagnosis:** If confirmed findings cluster around deterministic signals, and rejected around uncertain signals → `accuracy deadlock`. Action: split rule.

### 2. Duplicate collectors — same sources + same cadence?

**When to check:** Any flag. Part of routine review.

**Check:**
- `list_collectors` → compare sources and schedule_sec for each pair
- Two collectors with same sources AND same cadence AND same target type → `duplicate collectors`

**NOT a duplicate if:**
- Different sources (different data = different collectors)
- Different cadence that can't be unified
- Different target types (node vs switch)

### 3. Wrong cadence — rule needs different schedule than collector provides?

**When to check:** Rule accuracy is stuck, or rule needs more/less frequent data than the collector provides.

**Check:**
- `get_rule_detail(rule_id)` → what cadence does the rule need?
- `get_collector_health(collector_name)` → what schedule_sec is the collector on?
- If rule needs 5-min data but collector runs every 15 min → `wrong cadence`

**Exception:** A rule on a faster collector is fine (more data than needed). Only flag when rule needs FASTER data than collector provides.

### 4. Missing source — rule needs data collector doesn't have?

**When to check:** 🟠 Accuracy bad, and the diagnosis suggests a different data source would distinguish confirmed from rejected.

**Check:**
- `get_rule_detail(rule_id)` → read analyze() code — what data does it use?
- `list_findings(rule_id, verdict="rejected", limit=5)` + `get_finding_raw_data` → what data is missing that would have excluded these?
- If current source data is identical for confirmed and rejected, but another source would differ → `missing source`

**Evidence required:** You must show that current source can't distinguish confirmed from rejected. Can't add a source just "to see if it helps."

### 5. Collector running too many unused targets?

**When to check:** Collector efficiency — >50% targets that some bound rules ignore.

**Check:**
- `get_collector_health(collector_name)` → total targets
- For each bound rule: what subset of targets does it actually use?
- If >50% of collector targets are never used by any rule → `collector waste`

## Output

One structural diagnosis label per rule:

| Label | When |
|---|---|
| `accuracy deadlock` | Confirmed and rejected cluster around different signal confidence levels |
| `duplicate collectors` | Two collectors with same sources + same cadence |
| `wrong cadence` | Rule needs data more frequently than collector provides |
| `missing source` | Rule needs a data source the collector doesn't provide |
| `collector waste` | Collector runs >50% targets that no rule uses |
