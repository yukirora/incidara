---
name: job-check
description: "Check LTP job status, read job logs, find error patterns in failed jobs. Use when investigating whether infrastructure issues caused job failures."
---

# Job Check

Check LTP/OpenPAI training jobs: status, events, logs, error patterns. Focused on **investigation** — read-only, no submit/stop/delete.

## When to Use

- `inspect-infra-issue` needs to check if a job failed due to infrastructure
- "What happened to validation job on node X?"
- "Did the switch error cause any job failures?"

## Setup

```bash
export LTP_HOST="http://192.0.2.10"
# Token auto-loaded from LTP_TOKEN env var, or save once:
bash ${CLAUDE_SKILL_DIR}/scripts/ltp.sh save-token $LTP_TOKEN
```

## Commands

| Command | Description |
|---------|-------------|
| `list [--state STATE] [--vc VC] [--user USER] [--limit N]` | List jobs |
| `status <user>~<job>` | Check job status |
| `logs <user>~<job> [index] [--stream stderr]` | View container logs |
| `events <user>~<job>` | View job events |

## Reading Job Logs

**Never just grep for `return_code` or benchmark name in metrics** — those only show the test result, not the actual error.

### Smart approach: narrow to the failed benchmark, then read errors

1. The alert or `NotifyUnvalidatedNodes` tells you which benchmark failed (e.g., `nccl-bw:bw:allgather:nvlink-sharp/return_code:124`).
2. Extract the benchmark name prefix (e.g., `nccl-bw:bw:allgather:nvlink-sharp`).
3. Find that section and read until the next benchmark or the error summary.

```bash
# Extract the failed benchmark section
ltp.sh logs <user>~<job> 2>&1 | sed -n '/Runner is going to run <benchmark-prefix>/,/Runner is going to run/p' | head -200
```

### Error patterns

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

## Typical Investigation Flow

1. `get_node_recent_jobs(hostname, days=3)` — find recent jobs on the node
2. `get_job_events(job_name)` — see if job failed and when
3. `bash ${CLAUDE_SKILL_DIR}/scripts/ltp.sh logs <user>~<job>` — read the actual logs
4. Look for infrastructure error patterns (IB, GPU, NCCL) vs user code errors (AssertionError, OOM)
5. If infrastructure errors found → this supports the finding. Record as evidence.
