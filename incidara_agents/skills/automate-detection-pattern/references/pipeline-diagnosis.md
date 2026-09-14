# Pipeline Diagnosis

When a rule is flagged 🔴 Pipeline broken, data is not flowing through the pipeline. Find WHERE it's blocked.

```
Cron → Collector (targets) → Collector (execution) → Analyze() → Lifecycle → Findings → DB
  ↑           ↑                        ↑                    ↑           ↑
cron       no targets           all targets fail       crashes    db error
dead       wrong filter         SSH/auth/timeout      wrong field  missing config
```

## Step 1: Which stage is broken?

Run these checks in order. Stop at the first failure.

### Check 1: Is the collector running?

```
get_collector_health(collector_name)
```

- **Overdue** → cron process stuck or crashed → `pipeline: cron dead`
- **Running on schedule** → continue to Check 2

### Check 2: Are targets resolved?

```
get_collector_health(collector_name)  → total targets
```

- **0 targets** → `target_filter` excludes everything, or no matching hosts in Prometheus/inventory → `pipeline: no targets`
- **Targets exist** → continue to Check 3

### Check 3: Are targets collecting data successfully?

```
get_collector_health(collector_name)  → ok vs failed count
```

- **All failed** → collector bug → `pipeline: collector execution` → continue to Step 2
- **Some ok, some failed** → normal (unreachable targets) → pipeline works, continue to Check 4
- **All ok** → continue to Check 4

### Check 4: Does analyze() produce observations or findings?

```
test_rule_once(rule_id)
```

- **Error** (SyntaxError, AttributeError) → `pipeline: analyze crashes` → continue to Step 3
- **No observations/findings, no error** → analyze() runs but logic doesn't match payload, or fleet is clean. Use a known-bad replay/sample to distinguish → `pipeline: analyze no match` if known-bad data is missed.
- **Bad observations/findings produced** → analyze stage works; continue to lifecycle/DB checks.
- **Only healthy observations produced** → pipeline works and this is likely 🟡 No signal (fleet clean), not 🔴.

### Check 5: Does lifecycle write or refresh findings in DB?

```
list_findings(rule_id, limit=1)
```

- **Error** (connection refused, auth failed) → `pipeline: db error`
- **Empty after `test_rule_once(dry_run=False)` on known-bad data** → lifecycle/DB write issue
- **Findings exist** → pipeline works end-to-end

### Check 6: Are env vars / config present?

If collector fails with auth errors or missing endpoints:

- SSH failures → `SSH_AUTH_SOCK` set? SSH agent forwarded?
- Prometheus failures → `PROMETHEUS_SERVER_URI` and `PAI_TOKEN` set?
- DB failures → `EVIDENCE_DB_URL` set and reachable?
- Alert-manager failures → `ALERT_MANAGER_URL` and `INTERNAL_SECRET` set?

→ `pipeline: missing config`

## Step 2: Diagnose collector execution failures

When all targets fail, find the specific error.

### Read the log

```
read_collector_log(collector_name="<name>", lines=50)
read_collector_log(collector_name="<name>", grep="FAIL")
```

Each FAIL line shows the target hostname and error. Below it, `[command_name]: output` lines show what each command returned.

### Reproduce on a failing target

Pick a failing target from the log. Run the same commands directly:

```
ssh_run(hostname="<failing_target>", commands=["<command_1>", "<command_2>"])
```

### Compare with a working target

Pick a target from the OK lines in the log. Run the same commands:

```
ssh_run(hostname="<working_target>", commands=["<command_1>", "<command_2>"])
```

What's different?

### Match against common patterns

| Error | Root cause | Fix |
|-------|-----------|-----|
| `exit code 1` from `grep` | grep found no matches — normal | Append `\|\| true` to the grep command |
| `Permission denied` / `Operation not permitted` | Command needs elevated privileges | Add `sudo` prefix |
| `command not found` | Binary not installed on that node type | Use `which <cmd> && <cmd> \|\| echo NOT_INSTALLED` |
| `exit code 255` | SSH connection failed (wrong user, no key, agent not forwarded) | Check `user_env`/`password_env` in config. Check SSH_AUTH_SOCK |
| `connection closed` / `connection refused` | Target unreachable, or SSH config mismatch | Check network. For switches: set `"interactive": true` |
| `timeout` | Command took too long | Increase `timeout` in config, or simplify the command |
| `dmesg: read kernel buffer failed` | Needs `sudo dmesg` not `dmesg` | Add `sudo` prefix |
| All targets fail with same error | Collector config bug | Fix via `update_collector()` |
| Only some targets fail | Targets genuinely different (offline, different OS) | Create findings for investigation |

### SSH mode matters

- **Linux nodes** use `subprocess` SSH — relies on SSH agent. Requires `SSH_AUTH_SOCK` mounted.
- **Switches** use `pexpect` interactive SSH — requires `"interactive": true` in config. Uses password auth.

## Step 3: Diagnose analyze() failures

When analyze() crashes or produces no findings.

### Analyze() crashes (error from test_rule_once)

Common causes:

| Error | Cause |
|-------|-------|
| `SyntaxError` / `invalid syntax` | Python syntax error in analyze_code |
| `__import__ not found` | `import` statement in analyze_code — sandbox doesn't allow it |
| `X is not defined` | Constants defined outside `def analyze()` — move inside function |
| `'dict' object has no attribute 'target_id'` | Accessing `target.target_id` instead of `target.id` |
| `KeyError` | Accessing payload field that doesn't exist — use `.get()` |

### Analyze() runs but no findings (no match)

The code runs without error, but the logic doesn't match the actual payload shape.

**Check:**
1. `get_rule_detail(rule_id)` → read the analyze_code
2. `run_collector_once(collector_name, sample=1)` → see what the actual payload looks like
3. Does the code access the right fields? Does the condition match real data?

Common mismatches:
- Code checks `payload["ssh_ok"]` but collector is multi-source → need `payload["source_name"]["ssh_ok"]`
- Code checks `payload.get("outputs", {}).get("xid")` but command key is `"dmesg_xid"`
- Code checks `payload["entries"]` (node_logs) but collector is SSH → need `payload["outputs"]`

## Output

One pipeline root cause label:

| Label | Stage blocked |
|---|---|
| `pipeline: cron dead` | Scheduler |
| `pipeline: no targets` | Target resolution |
| `pipeline: collector execution` | Collector SSH/commands |
| `pipeline: analyze crashes` | Rule analyze() |
| `pipeline: analyze no match` | Rule analyze() (logic mismatch) |
| `pipeline: db error` | Database |
| `pipeline: missing config` | Environment/config |
