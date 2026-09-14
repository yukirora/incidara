"""Sandboxed execution of analyze() code from patrol_rules.

Runs analyze_code in a subprocess with timeout and restricted globals.
"""

from __future__ import annotations

import logging
import math
import multiprocessing
import os
import re
import traceback
from typing import Any

from patrol_cron.models import CollectionResult, Finding, Observation, RuleResult

logger = logging.getLogger(__name__)

ANALYZE_TIMEOUT_SEC = int(__import__("os").environ.get("ANALYZE_TIMEOUT_SEC", "300"))

# Minimal builtins allowed inside analyze()
_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "isinstance": isinstance,
    "len": len, "list": list, "max": max, "min": min, "print": print,
    "range": range, "round": round, "set": set, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "zip": zip,
    "True": True, "False": False, "None": None,
}


def _legacy_findings_to_rule_result(findings: list[Finding], state: dict) -> RuleResult:
    observations = [
        Observation(
            signal_key="legacy",
            target_id=f.target_id,
            status="bad",
            action=f.action,
            severity=f.severity,
            action_params=f.action_params,
            evidence=f.evidence,
            confidence=f.confidence,
        )
        for f in findings
    ]
    return RuleResult(observations=observations, state=state, lifecycle_enabled=False)


def _normalize_analyze_result(result: object) -> RuleResult:
    if isinstance(result, RuleResult):
        return result
    if isinstance(result, tuple) and len(result) == 2:
        findings, state = result
        return _legacy_findings_to_rule_result(findings, state)
    raise RuntimeError("analyze() must return RuleResult or (findings, new_state)")


def _run_in_subprocess(analyze_code: str, collected_dict: dict,
                       state: dict, result_queue: multiprocessing.Queue):
    """Target function for the subprocess. Execs analyze_code and puts result in queue."""
    try:
        safe_globals = {
            "__builtins__": _SAFE_BUILTINS,
            "Finding": Finding,
            "Observation": Observation,
            "RuleResult": RuleResult,
            "math": math,
            "re": re,
        }
        local_ns: dict[str, Any] = {}

        # Compile and exec the analyze_code to define analyze()
        exec(compile(analyze_code, "<analyze>", "exec"), safe_globals, local_ns)

        analyze_fn = local_ns.get("analyze")
        if not callable(analyze_fn):
            result_queue.put(("error", "analyze_code does not define a callable analyze()"))
            return

        # Reconstruct CollectionResult from dict
        from patrol_cron.models import TargetData
        targets = [
            TargetData(id=t["id"], type=t["type"], payload=t["payload"], meta=t.get("meta", {}))
            for t in collected_dict.get("targets", [])
        ]
        collected = CollectionResult(
            collector_name=collected_dict["collector_name"],
            targets=targets,
            errors=collected_dict.get("errors", []),
            duration=collected_dict.get("duration", 0.0),
            run_time=collected_dict.get("run_time", 0.0),
        )

        rule_result = _normalize_analyze_result(analyze_fn(collected, state))

        # Serialize observations to dicts for queue transfer
        observations_dicts = []
        for obs in rule_result.observations:
            observations_dicts.append({
                "signal_key": obs.signal_key,
                "target_id": obs.target_id,
                "status": obs.status,
                "severity": obs.severity,
                "action": obs.action,
                "action_params": obs.action_params,
                "evidence": obs.evidence,
                "confidence": obs.confidence,
                "event_id": obs.event_id,
                "lifecycle": obs.lifecycle,
                "dedup_key": obs.dedup_key,
            })

        result_queue.put(("ok", observations_dicts, rule_result.state, rule_result.lifecycle_enabled))

    except Exception as e:
        tb = traceback.format_exc()
        result_queue.put(("error", f"{e}\n{tb}"))


def run_sandboxed(
    analyze_code: str,
    collected: CollectionResult,
    state: dict,
    timeout: int | None = None,
) -> RuleResult:
    """Execute analyze_code with restricted globals.

    Runs in-process with exec() for speed. The restricted globals prevent
    imports, file I/O, and network access. For truly untrusted code, a
    subprocess sandbox can be re-enabled via PATROL_SANDBOX_SUBPROCESS=1.

    Returns RuleResult. Legacy callers can still unpack it as
    (bad_observations_as_findings, state).
    Raises RuntimeError on execution failure.
    """
    use_subprocess = os.environ.get("PATROL_SANDBOX_SUBPROCESS", "") == "1"

    if use_subprocess:
        return _run_in_subprocess_mode(analyze_code, collected, state, timeout)

    # In-process exec with restricted globals — fast, no serialization overhead
    safe_globals = {
        "__builtins__": _SAFE_BUILTINS,
        "Finding": Finding,
        "Observation": Observation,
        "RuleResult": RuleResult,
        "math": math,
        "re": re,
    }
    local_ns: dict[str, Any] = {}

    try:
        exec(compile(analyze_code, "<analyze>", "exec"), safe_globals, local_ns)
    except Exception as e:
        raise RuntimeError(f"analyze() compilation failed: {e}")

    analyze_fn = local_ns.get("analyze")
    if not callable(analyze_fn):
        raise RuntimeError("analyze_code does not define a callable analyze()")

    try:
        return _normalize_analyze_result(analyze_fn(collected, state))
    except Exception as e:
        raise RuntimeError(f"analyze() failed: {e}")


def _run_in_subprocess_mode(
    analyze_code: str,
    collected: CollectionResult,
    state: dict,
    timeout: int | None = None,
) -> RuleResult:
    """Subprocess sandbox — safer but slower due to serialization."""
    timeout = timeout or ANALYZE_TIMEOUT_SEC

    collected_dict = {
        "collector_name": collected.collector_name,
        "targets": [
            {"id": t.id, "type": t.type, "payload": t.payload, "meta": t.meta}
            for t in collected.targets
        ],
        "errors": collected.errors,
        "duration": collected.duration,
    }

    result_queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=_run_in_subprocess,
        args=(analyze_code, collected_dict, state, result_queue),
    )
    proc.start()
    proc.join(timeout=timeout)

    if proc.is_alive():
        proc.kill()
        proc.join(timeout=5)
        raise RuntimeError(f"analyze() timed out after {timeout}s")

    if result_queue.empty():
        raise RuntimeError("analyze() returned no result (crashed?)")

    result = result_queue.get_nowait()

    if result[0] == "error":
        raise RuntimeError(f"analyze() failed: {result[1]}")

    _, observations_dicts, new_state, lifecycle_enabled = result
    observations = [
        Observation(
            signal_key=f.get("signal_key", "legacy"),
            target_id=f["target_id"],
            status=f.get("status", "bad"),
            severity=f["severity"],
            action=f["action"],
            action_params=f.get("action_params", {}),
            evidence=f.get("evidence", {}),
            confidence=f.get("confidence", 1.0),
            event_id=f.get("event_id"),
            lifecycle=f.get("lifecycle", {}),
            dedup_key=tuple(f.get("dedup_key", ("rule_id", "target_id", "signal_key", "action"))),
        )
        for f in observations_dicts
    ]
    return RuleResult(
        observations=observations,
        state=new_state,
        lifecycle_enabled=lifecycle_enabled,
    )
