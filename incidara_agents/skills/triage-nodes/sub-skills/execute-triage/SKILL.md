# Reference: execute-triage

This is a reference document for the orchestrator. Execution is handled directly by the orchestrator (NOT dispatched as a sub-agent) and requires explicit user confirmation before any write operation.

All actions are performed through the **LTP Alert Manager API** via the `execute_triage.py` script — no direct DB writes, no direct kubectl commands.

## SAFETY

- **ALWAYS dry-run first** — use `--dry-run` flag
- Log every action taken

## Script

Use the Python script at `${CLAUDE_SKILL_DIR}/sub-skills/execute-triage/execute_triage.py`:

```bash
python3 ${CLAUDE_SKILL_DIR}/sub-skills/execute-triage/execute_triage.py [--dry-run] <command> [args...]
```

### Commands

#### Triage transition

```bash
python3 ${CLAUDE_SKILL_DIR}/sub-skills/execute-triage/execute_triage.py --dry-run triage \
    --nodes <hostname1>,<hostname2>,... \
    --status <triaged_hardware|triaged_platform> \
    --reason <ReasonLabel> \
    --summary "Reason: <reason>. Summary: <what happened>. Reproducer: <how to verify>"
```

#### Trigger revalidation

```bash
python3 ${CLAUDE_SKILL_DIR}/sub-skills/execute-triage/execute_triage.py --dry-run validate \
    --nodes <hostname1>,<hostname2>,...
```

#### From JSON file (batch)

```bash
python3 ${CLAUDE_SKILL_DIR}/sub-skills/execute-triage/execute_triage.py --dry-run from-file actions.json
```

## Summary Annotation Format

The `--summary` field must include three parts:
- **Reason**: the triage reason label (same as `--reason`)
- **Summary**: concise description of what was observed
- **Reproducer**: how to reproduce or verify the issue

Format: `"Reason: <reason>. Summary: <what happened>. Reproducer: <how to verify>"`

## Examples

### Transition 8 nodes to triaged_hardware / PCIeBandwidthDegradation

```bash
# Dry-run first
python3 ${CLAUDE_SKILL_DIR}/sub-skills/execute-triage/execute_triage.py --dry-run triage \
    --nodes lg-cmc-demo-r01u01-h200-000001,lg-cmc-demo-r01u01-h200-000001,lg-cmc-demo-r01u01-h200-000001,lg-cmc-demo-r01u01-h200-000001,lg-cmc-demo-r01u01-h200-000001,lg-cmc-demo-r01u01-h200-000001,lg-cmc-demo-r01u01-h200-000001,lg-cmc-demo-r01u01-h200-000001 \
    --status triaged_hardware \
    --reason PCIeBandwidthDegradation \
    --summary "Reason: PCIeBandwidthDegradation. Summary: gpu-copy-bw cpu_to_gpu*_by_dma underperformance, variance -51% to -75%. Reproducer: superbench validation, benchmark gpu-copy-bw:perf/cpu_to_gpu*_by_dma_under_numa*_bw"

# After user confirms, remove --dry-run to execute
```

### Transition 15 storage nodes to triaged_platform / JobExporterPortConflict

```bash
python3 ${CLAUDE_SKILL_DIR}/sub-skills/execute-triage/execute_triage.py --dry-run triage \
    --nodes lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001,lg-cmc-demo-r01u01-storage-000001 \
    --status triaged_platform \
    --reason JobExporterPortConflict \
    --summary "Reason: JobExporterPortConflict. Summary: moneo-node-exporter CrashLoopBackOff, port 8002 held by host storage_main. Reproducer: kubectl describe pod job-exporter-*; ssh to node and run ss -tlnp | grep :8002"
```

### Trigger revalidation

```bash
python3 ${CLAUDE_SKILL_DIR}/sub-skills/execute-triage/execute_triage.py --dry-run validate \
    --nodes lg-cmc-demo-r01u01-b300-000001
```

## Two Alert Types (API reference)

### 1. `admin-abnormal-node` — Triage transitions

Sent by the `triage` command. Labels:
- `report_type`: `admin-abnormal-node`
- `action`: `cordon`
- `severity`: `error`
- `node_name`: hostname
- `triaged_label`: `triaged_hardware` or `triaged_platform`
- `alertname`: reason label

### 2. `admin-validate-node` — Revalidation

Sent by the `validate` command. Labels:
- `alertname`: `admin-validate-node`
- `node_name`: hostname

Flow: Alert → alert-handler `/validate-nodes` → node-recycler → gets node SKU → renders validation.yaml → submits superbench job → updates status to `validating`

Eligible states: `triaged_user`, `triaged_platform`, `triaged_unknown`, `triaged_hardware`

## Execution Flow

1. **Dry-run:** Run with `--dry-run` — shows all API payloads without sending
2. **Present:** Show dry-run output to user
3. **Wait for explicit user confirmation**
4. **Execute:** Run without `--dry-run`
5. **Verify:** Use `db-query` sub-skill to query `node_status` and confirm transitions
