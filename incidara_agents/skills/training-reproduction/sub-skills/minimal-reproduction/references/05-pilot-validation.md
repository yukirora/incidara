# Pilot Validation Runbook

## Validate with a staged pilot

Do not run a broad setting sweep.

```text
1. Framework config/group build.
2. Verify generated required submesh and experts/rank.
3. Initialize model/state; measure max rank/stage memory.
4. Run one/few warmup steps.
5. Verify expert/tensor/GEMM shapes.
6. Verify tokens/expert and collective bytes/order/frequency.
7. Verify actual GPU/HCA/fabric path.
8. Verify load/exposure setup.
9. Only then run the trigger/measurement window.
```

If a derived target differs, invalidate the plan and return to the one setting responsible for that property. If initialization reveals a memory estimate error, update the limiting component and recalculate; do not start a combinatorial sweep.
