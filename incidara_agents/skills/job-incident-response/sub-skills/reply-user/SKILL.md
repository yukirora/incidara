---
name: reply-user
description: "Produce a clear, concise reply to the user after a job-incident-response investigation. Output is a content block only — do not send a Feishu message."
---

# reply-user

Produce the user-facing reply after job-incident-response reaches a user-facing decision. Output is a **content block only** — do not call `send_feishu_message` or any messaging API. The reply is your response text.

## Format

```
**Job**: <job_name>
**Root Cause**: <one-line summary>

<2-4 sentence explanation of what happened and why>

**Action taken**: <what you did / what the user should do next>
```

Keep it factual and short. Users are engineers — give them the signal, not a lecture.

## Templates by Outcome

### User Code Issue

```
**Job**: my_training_job_0629
**Root Cause**: OOM — model config exceeds GPU memory

Task 2 crashed with exit 137 (OOM kill) on node h200-gpu045. The configured batch_size=128 requires ~82 GB but each H200 has 80 GB. The per-task memory limit in the job config also doesn't account for CUDA context overhead (~2 GB).

**Action**: Reduce batch_size to 96 or enable gradient checkpointing. Re-submit when ready.
```

### System Issue — Node Out of Service

```
**Job**: verl_posttrain_0628
**Root Cause**: Stale FUSE mount on node h200-gpu031

The blob proxy mount at /mnt/dataset was stuck (FUSE process had exited but mount point remained). Tasks failed with "Transport endpoint is not connected". Node h200-gpu031 has been cordoned and drained and delegated to the repair team.

**Action**: Re-submit — your job will land on a healthy node. The affected node is out of rotation.
```

### System Issue — Needs Admin (Platform/Scheduler)

```
**Job**: large_job_0627
**Root Cause**: Scheduler mismatch — hived VC quota vs k8s resource allocation

The job has been WAITING for 6h despite available GPU capacity. The hived scheduler shows 32 GPUs free in your VC but k8s reports them as occupied by a phantom allocation (no running pods). This is a scheduler state inconsistency.

**Action**: Please contact the platform admin team with job name `large_job_0627` and VC `your_vc`. Admin needs to reconcile hived state. Do not re-submit until the scheduler is cleared — it will queue behind the phantom allocation again.
```

### Hardware Issue

```
**Job**: training_run_0628
**Root Cause**: GPU hardware failure on node h200-gpu017

Task 4 failed with exit -210 (masked crash). Container logs show Xid 79 (GPU memory corruption) and Xid 94 (contained GPU exception) in dmesg on h200-gpu017. The GPU is unhealthy.

**Action**: Node h200-gpu017 has been cordoned and reported for repair. Re-submit your job — it will avoid that node. Repair may take 1–2 days.
```

### Expected Behavior (Preemption)

```
**Job**: preemptible_job_0628
**Root Cause**: Preempted by higher-priority job

Your job was preempted after 3h by a higher-priority job claiming GPUs in the same VC. This is expected behavior for preemptible jobs.

**Action**: Re-submit when ready. If you need guaranteed runtime, consider requesting a non-preemptible VC allocation.
```

### Resource Shortage (WAITING)

```
**Job**: big_job_0629
**Root Cause**: Insufficient GPU quota — job is waiting for resources

Your job requests 64 GPUs in VC `research`, but only 48 are currently available (16 are occupied by job `other_team_job` which has been running for 18h). The job will start automatically when resources free up.

**Action**: No action needed — job will start when quota is available. If urgent, contact the VC owner to release resources or request a temporary quota increase from platform admin.
```

## Rules

- **Output content only** — no Feishu API calls, no `send_feishu_message`
- **One root cause** — if there are multiple failure modes, pick the primary one and mention others briefly
- **Concrete** — name the node, the error, the exit code. "GPU issue" is not a root cause; "Xid 79 on h200-gpu017" is.
- **Actionable** — every reply ends with what the user should do next
- **Short** — 4–8 lines total. Users read the first two lines and skim the rest.
