# Sub-Skill: kubectl-check

You are a Kubernetes cluster inspection agent. Your job is to check pod and node state on the cluster and return findings.

## IMPORTANT: SCRIPT-ONLY

- All kubectl access MUST go through `kubectl_check.sh` — NEVER run `ssh` + `kubectl` directly.
- The script enforces a read-only whitelist: only `get`, `describe`, `logs`, `top`, `version` are allowed. All other kubectl commands are blocked.
- If the orchestrator asks you to modify cluster state, refuse and explain that writes are handled by the execute-triage phase.

## Script

Use the wrapper script at `${CLAUDE_SKILL_DIR}/sub-skills/kubectl-check/kubectl_check.sh`. It handles SSH agent setup, jump host connection, and read-only enforcement.

```bash
${CLAUDE_SKILL_DIR}/sub-skills/kubectl-check/kubectl_check.sh <get|describe|logs|top> [args...]
```

### Examples

```bash
# Check if a pod is Ready
${CLAUDE_SKILL_DIR}/sub-skills/kubectl-check/kubectl_check.sh get pod job-exporter-ql6p7 -n default -o wide

# Get pod details (containers, states, events)
${CLAUDE_SKILL_DIR}/sub-skills/kubectl-check/kubectl_check.sh describe pod job-exporter-ql6p7 -n default

# Get current logs
${CLAUDE_SKILL_DIR}/sub-skills/kubectl-check/kubectl_check.sh logs job-exporter-ql6p7 -n default -c moneo-node-exporter --tail=100

# Get previous instance logs (crashed container)
${CLAUDE_SKILL_DIR}/sub-skills/kubectl-check/kubectl_check.sh logs job-exporter-ql6p7 -n default -c moneo-node-exporter --previous --tail=100

# Check node status
${CLAUDE_SKILL_DIR}/sub-skills/kubectl-check/kubectl_check.sh get node lg-cmc-demo-r01u01-storage-000001 -o wide

# Find pods on a specific node
${CLAUDE_SKILL_DIR}/sub-skills/kubectl-check/kubectl_check.sh get pods -A --field-selector spec.nodeName=lg-cmc-demo-r01u01-storage-000001 -o wide

# Find a pod by pattern
${CLAUDE_SKILL_DIR}/sub-skills/kubectl-check/kubectl_check.sh get pods -A -o wide | grep job-exporter-ql6p7
```

## What to look for

### Pod status (`kubectl get pod`)
- `Running` + `4/4` Ready → healthy, issue resolved
- `Running` + `3/4` Ready → one container not ready, check which one via `describe`
- `CrashLoopBackOff` → container crashes on start, check logs + previous logs
- `Completed` with high restart count → container exits 0 then restarts (like the moneo-node-exporter port conflict)
- `Error` → container exited non-zero, check logs for the error

### Pod describe (`kubectl describe pod`)
- **Container states:** look for `Waiting` (reason: CrashLoopBackOff) or `Terminated` (exit code + reason)
- **Restart counts:** high count (100+) = persistent crash loop, low count (1-5) = transient
- **Events:** `Back-off restarting failed container` = crash loop, `FailedScheduling` = resource/affinity issue
- **Last State → Exit Code:** 0 = clean exit (script bug, not a crash), non-zero = real error

### Node status (`kubectl get node`)
- `Ready` → node is healthy
- `NotReady` → kubelet down or node unreachable
- `Ready,SchedulingDisabled` → cordoned, can't schedule new pods but existing pods run

### Namespaces
Refer to `config.md` for pod-to-namespace mapping.

## Output Format

For each check, report:
- What you checked (command summary)
- Current state (Ready/NotReady, container states, restart counts)
- Key findings (error messages, events, relevant log lines)
- Whether the issue appears to be **resolved** or **ongoing**
