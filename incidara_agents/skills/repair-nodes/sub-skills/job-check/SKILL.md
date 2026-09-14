---
name: ltp-jobs
description: "Manage LTP (OpenPAI) training jobs: submit, list, check status, stop/start, get SSH info, wait for completion, and delete jobs. Use this skill whenever the user mentions LTP jobs, submitting a training job, checking job status, stopping a job, getting SSH access to a container, or any operation involving the LTP platform or OpenPAI cluster. Trigger on phrases like 'submit job', 'check job status', 'list running jobs', 'stop ltp job', 'ssh into container', 'wait for job', 'ltp 作业', '提交作业', '查看作业状态'."
---

# LTP Job Management

Use this skill for LTP/OpenPAI cluster operations: submit jobs, check status, list jobs, stop/start, SSH into containers, wait for completion, view logs, view events, and delete jobs.

**Trigger phrases:** "submit job", "check job status", "list running jobs", "stop ltp job", "ssh into container", "wait for job", "ltp 作业", "提交作业", "查看作业状态"

---

## Quick Start

### Option 1: Save token once (recommended)

```bash
# Set host and save token for future use
export LTP_HOST="http://<ltp-server>"  # e.g., http://192.0.2.10
bash ${CLAUDE_SKILL_DIR}/job-check/ltp.sh save-token <your-ltp-token>
```

Then use without setting token again:
```bash
bash ${CLAUDE_SKILL_DIR}/job-check/ltp.sh list --state WAITING
```

### Option 2: Login with username/password

```bash
export LTP_HOST="http://<ltp-server>"
export LTP_USER="your-username"
export LTP_PASS="your-password"
bash ${CLAUDE_SKILL_DIR}/job-check/ltp.sh login
```

### Option 3: Use token directly (one-time)

```bash
export LTP_HOST="http://<ltp-server>"
export LTP_TOKEN="<your-ltp-token>"
bash ${CLAUDE_SKILL_DIR}/job-check/ltp.sh list --state WAITING
# Token will be automatically saved for future use
```

---

## Commands

| Command | Description |
|---------|-------------|
| `login` | Login with username/password and save token |
| `save-token <token>` | Save a token for current LTP_HOST |
| `list [--state STATE] [--vc VC] [--user USER] [--limit N]` | List jobs |
| `submit <job.yaml>` | Submit a job |
| `status <user>~<job>` | Check job status |
| `stop <user>~<job>` | Stop a job |
| `ssh <user>~<job>` | Get SSH connection info |
| `logs <user>~<job> [index] [--stream stderr]` | View container logs |
| `events <user>~<job>` | View job events (scheduling, state transitions, retries, errors) |

Examples:
```bash
bash ${CLAUDE_SKILL_DIR}/job-check/ltp.sh save-token eyJhbGc...
bash ${CLAUDE_SKILL_DIR}/job-check/ltp.sh list --state RUNNING
bash ${CLAUDE_SKILL_DIR}/job-check/ltp.sh status alice~my-job
bash ${CLAUDE_SKILL_DIR}/job-check/ltp.sh stop alice~my-job
bash ${CLAUDE_SKILL_DIR}/job-check/ltp.sh ssh alice~my-job
bash ${CLAUDE_SKILL_DIR}/job-check/ltp.sh events alice~my-job
```

---

## Token Management

Tokens are saved to `~/.ltp_tokens/` with host association:
- `~/.ltp_tokens/<host>_token` - stores the token
- `~/.ltp_tokens/<host>_user` - stores the username

**Token loading priority:**
1. `LTP_TOKEN` environment variable
2. Saved token for current `LTP_HOST`
3. Interactive login prompt

**Auto-save:** When `LTP_TOKEN` is used and succeeds, it's automatically saved for future use.

---

## Viewing Job Logs

```bash
bash ${CLAUDE_SKILL_DIR}/job-check/ltp.sh logs <user>~<job> [idx] [--stream stdout|stderr|all]
```

The log is a long stream of benchmark runs. **Never just grep for `return_code` or benchmark name in metrics** — those only show the test result, not the actual error.

### How to read validation job logs

Each benchmark starts with `Runner is going to run <benchmark-name>` and ends with either a result summary or `Microbenchmark execution failed`.

**Smart approach: narrow to the failed benchmark first, then read errors.**

1. The alert or `NotifyUnvalidatedNodes` tells you which benchmark failed (e.g., `nccl-bw:bw:allgather:nvlink-sharp/return_code:124`).
2. Extract the benchmark name prefix (e.g., `nccl-bw:bw:allgather:nvlink-sharp`).
3. Find that section and read until the next benchmark or the error summary.

```bash
# Step 1: Find the line where the failed benchmark starts
ltp.sh logs <user>~<job> 2>&1 | grep -n "Runner is going to run <benchmark-prefix>"

# Step 2: Read from that line through ~150 lines to see errors
ltp.sh logs <user>~<job> 2>&1 | sed -n '37465,37650p'

# Alternative: extract the failed benchmark section in one shot
ltp.sh logs <user>~<job> 2>&1 | sed -n '/Runner is going to run <benchmark-prefix>/,/Runner is going to run/p' | head -200
```

**Error patterns to look for in the benchmark section:**

| Pattern | Meaning |
|---------|---------|
| `UCX  ERROR ibv_create_ah` | IB address handle creation failed → IB link/port issue |
| `TL_UCP ERROR` | Transport-level error |
| `Signal: Segmentation fault` | Crash during collective operation |
| `NCCL error` / `NCCL WARN` | NCCL-level issue |
| `Xid` | GPU hardware error |
| `ECC` | GPU memory error |
| `CUDA error` | CUDA runtime issue |

**If you don't know which benchmark failed**, find errors first:
```bash
ltp.sh logs <user>~<job> 2>&1 | grep -iE 'UCX  ERROR|TL_UCP ERROR|Signal:|Segmentation fault|Xid |ECC|CUDA error|ibv_create_ah|NCCL error' | head -30
```
Then go back and read the full section around those lines.

---

## Troubleshooting

| Error | Solution |
|-------|----------|
| 401 Unauthorized | Token expired. Run `login` or `save-token <new-token>` |
| 409 Conflict | Job name exists. Change `name:` in YAML |
| 403 Forbidden | No permission for virtual cluster. Check `virtualCluster` and `skuType` |
| SSH not available | Job must be RUNNING with ssh plugin enabled in YAML |
| No token found | Run `save-token <token>` or `login` first |
