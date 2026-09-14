# Refinement Diagnosis

When a rule is flagged 🟠 (Flooding, Accuracy bad, or Regression), use this guide to diagnose the root cause.

## Step 1: READ — Evidence Before Action

**⛔ No evidence = no diagnosis.** You must read findings before deciding what's wrong.

1. **Rejected findings** — WHY are they false positives?
   - `list_findings(rule_id, verdict="rejected", limit=10)`
   - `get_finding_raw_data(finding_id)` — for each rejected finding
   - What pattern do they share? What signal made the rule fire incorrectly?

2. **Confirmed findings** — What should you PROTECT?
   - `list_findings(rule_id, verdict="confirmed", limit=10)`
   - `get_finding_raw_data(finding_id)` — for each confirmed finding
   - What pattern do they share? What distinguishes them from rejected?

3. **Rejection reasons** — What did the human say?
   - `get_rule_rejection_reasons(rule_id)` — most direct signal available

4. **Replay suite** — structured evidence from labeled RMA cases
   - `create_rule_replay_cases_from_feedback(rule_id)` — generate from feedback
   - `run_rule_replay_suite(rule_id)` — failures ARE evidence

**Minimum evidence to diagnose:**

| Evidence available | Action |
|---|---|
| 0 judged + 0 replay cases | Cannot diagnose — delegate for verdicts |
| Replay suite with positive + negative cases | Run suite → failures indicate what's wrong |
| 1-2 live verdicts, all same verdict | Not enough signal — delegate for more |
| 3+ live verdicts with mix of confirmed/rejected | Sufficient — proceed with diagnosis |

## Step 2: IDENTIFY — Root Cause

Based on the evidence from Step 1, identify which root cause applies.

### Root cause catalog

| Root cause | Evidence pattern | Example |
|---|---|---|
| **Cascade contamination** | Rejected: all nodes in job fail together; Confirmed: only 1-2 nodes fail | Large job: 512 nodes fail, rule flags all of them |
| **Wrong signal** | Rejected: node hardware healthy (no ECC, IB up); Confirmed: node has ECC or IB down | Rule fires on "job failed on this node" but job failed due to user code |
| **Rule too broad** | Rejected: multiple distinct failure types mixed; Confirmed: one specific type | Rule fires on any Xid, but Xid 13 is transient while Xid 31 is hardware |
| **Missing filter** | Rejected findings share a condition that confirmed ones don't | All rejected have exit code -220 (cascade), confirmed have exit code 1 |
| **Accuracy deadlock** | Confirmed cluster around one signal; Rejected cluster around a different signal | Confirmed = critical_warning; Rejected = media_errors_increasing |
| **Non-predictive signal** | Confirmed and rejected have identical raw data — no distinguishable pattern | Same exit code, same failure count, but some are hardware, some are NFF |
| **Recall loss** | Confirmed cases exist that current code would NOT fire on (from old code or other sources) | Code added `AND ecc_error` condition, but some confirmed cases had no ECC |
| **Precision loss from rule change** | New detection branch added; new findings are being rejected | Code added `elif transient_xid` branch, all those findings rejected |
| **Collector change** | Same rule code, but different targets or raw data after collector update | Collector added job_logs source, rule now sees different data |
| **Sample composition** | No detection behavior change — same targets, same evidence, only verdicts differ | Code change was refactor only, but this batch happened to have more NFFs |

### Diagnosis by flag

**🟠 Flooding** — start here:
- Read 5 rejected findings' raw data → what pattern do they share?
- Most likely: cascade contamination (all nodes flagged) or rule too broad (no filtering)
- If all rejected findings show all nodes in a job failing → `cascade contamination`
- If rejected findings show different failure types → `rule too broad`

**🟠 Accuracy bad** — start here:
- Compare confirmed vs rejected findings' raw data → what distinguishes them?
- If confirmed and rejected cluster around different signals → `accuracy deadlock`
- If confirmed and rejected have identical raw data → `non-predictive signal`
- If rejected share a condition confirmed don't → `missing filter`
- If rejected findings show wrong detection trigger → `wrong signal`
- If rejected findings show cascade pattern → `cascade contamination`

**🟠 Regression** — start here:
1. `compare_rule_versions(rule_id)` → which version did accuracy drop?
2. Read the code change between versions → does it explain the drop?
3. `list_findings(rule_id, current_code_only=True, limit=10)` vs `list_findings(rule_id, rule_code_hash=<prev_hash>, limit=10)` → same targets or different?
4. Check `from_old_code` on findings → are you comparing current vs old code findings?

| What changed | Detection behavior | Root cause | Action |
|---|---|---|---|
| Rule broadened | Finds more targets (including bad ones) | `precision loss from rule change` | Refine or rollback rule |
| Rule narrowed | Finds fewer targets (missing confirmed) | `recall loss` | Rollback rule |
| Rule logic error | Same targets, wrong severity/fault type | `wrong signal` | Refine or rollback rule |
| Collector changed | Different targets or raw data | `collector change` | Rollback collector |
| Both changed | — | Analyze which explains the drop | Act on the one that explains it |
| Behavior identical | Same targets, same evidence — only verdicts differ | `sample composition` | Watch, don't modify |
| Neither changed | Different failure patterns in fleet | `sample composition` | Watch, update replay cases |

**Recall loss is most urgent** — did the code add a condition that old-code confirmed findings wouldn't pass? If yes → `recall loss` → `rollback_rule`.

### Decision: rule change, collector change, or structural change?

**⛔ The collector casts a wide net. The rule filters precisely.**

| Situation | Root cause suggests | Why |
|---|---|---|
| False positives from cascade/victims | Rule change | Collector is correct to collect all failing nodes; rule should filter |
| False positives from wrong signal | Rule change | Collector provides data, rule decides what's real |
| False positives from known-benign conditions | Rule change | e.g., skip exit code -220 |
| Rule needs data collector doesn't provide | Collector change (with evidence) | e.g., rule needs job_logs to distinguish cascade |
| Confirmed and rejected cluster differently | Structural change (split) | Accuracy deadlock |

**⛔ Never narrow `target_filter` to fix false positives.** That's the rule's job:
- ❌ `virtualCluster == 'h200'` in collector → excludes other clusters
- ❌ Increase `gpu_count` threshold → misses smaller jobs with real failures
- ✅ Add isolation check in `analyze()` → filters victims from root cause

## Output

One root cause label per flagged rule, from this controlled vocabulary:

| Root cause label | Flag(s) |
|---|---|
| `cascade contamination` | 🟠 Flooding, 🟠 Accuracy bad |
| `wrong signal` | 🟠 Accuracy bad, 🟠 Regression |
| `rule too broad` | 🟠 Flooding, 🟠 Accuracy bad |
| `missing filter` | 🟠 Accuracy bad |
| `accuracy deadlock` | 🟠 Accuracy bad |
| `non-predictive signal` | 🟠 Accuracy bad |
| `recall loss` | 🟠 Regression |
| `precision loss from rule change` | 🟠 Regression |
| `collector change` | 🟠 Regression |
| `sample composition` | 🟠 Regression |

Example output: `large_job_failure_v1: cascade contamination`
