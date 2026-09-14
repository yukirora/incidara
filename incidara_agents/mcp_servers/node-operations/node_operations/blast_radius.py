"""
Safety Guardrails – Feature 1 (Autonomy Graduation)

Two independent safety mechanisms, both enforced by execute_node_action:

1. Blast-Radius (C1): per-action scope limit + cooldown.
   Prevents "cordoned the entire fleet" from a single misdiagnosis.

2. Circuit Breaker (D1): temporal failure-rate limit.
   Prevents cascading damage from a systematic bad diagnosis pattern.
   3 failures within 1h → disable auto-execution for that action type.
   Half-open after cooldown: allow 1 test. Reset on success.

Env vars:
  BLAST_RADIUS_MAX_NODES             – max nodes per action (default: 20)
  BLAST_RADIUS_COOLDOWN_S            – cooldown between actions on same node (default: 300)
  CIRCUIT_BREAKER_MAX_FAILURES       – tool failures before trip (default: 5)
  CIRCUIT_BREAKER_WINDOW_S           – failure counting window (default: 3600)
  CIRCUIT_BREAKER_COOLDOWN_S         – trip → half-open wait (default: 3600)
"""

import json
import logging
import os
import time
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Safety event persistence ─────────────────────────────────────

def _log_safety_event(
    event_type: str,
    action: str,
    reason: str,
    hostname: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Persist a safety event to the DB (if EVIDENCE_DB_URL is set)."""
    db_url = os.environ.get("EVIDENCE_DB_URL", "")
    if not db_url:
        logger.debug("No EVIDENCE_DB_URL — safety event not persisted: %s", event_type)
        return
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO safety_events (event_type, agent_id, action, hostname, reason, metadata)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                event_type,
                os.environ.get("AGENT_NAME", "unknown"),
                action,
                hostname,
                reason,
                json.dumps(metadata or {}),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to persist safety event: %s", exc)

# In-memory cooldown tracker: {(action, hostname): last_execution_timestamp}
_cooldown_tracker: dict = {}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        logger.warning("Invalid %s value, using default %s", name, default)
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name, "").lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no"):
        return False
    return default


def check_action(
    action: str,
    hostnames: List[str],
) -> Tuple[bool, str]:
    """
    Check whether an action is permitted under the blast-radius policy.

    Returns (permitted, reason). If permitted=False, reason explains the block.
    Called by execute_node_action before dispatching.
    """
    fail_closed = _env_bool("BLAST_RADIUS_FAIL_CLOSED", True)
    max_nodes = _env_int("BLAST_RADIUS_MAX_NODES", 20)
    cooldown_s = _env_int("BLAST_RADIUS_COOLDOWN_S", 300)

    # ── Max nodes per action ───────────────────────────────────
    if len(hostnames) > max_nodes:
        reason = (
            f"Blast-radius blocked: {action} targets {len(hostnames)} nodes "
            f"(max: {max_nodes}). Nodes: {hostnames}."
        )
        _log_safety_event("blast_radius_blocked", action, reason, hostnames[0] if hostnames else None,
                          {"node_count": len(hostnames), "max_nodes": max_nodes})
        return False, reason

    # ── Cooldown per node ──────────────────────────────────────
    for hostname in hostnames:
        key = (action, hostname)
        last = _cooldown_tracker.get(key, 0)
        elapsed = time.time() - last
        if elapsed < cooldown_s:
            reason = (
                f"Cooldown active: {action} on {hostname} was executed "
                f"{elapsed:.0f}s ago (cooldown: {cooldown_s}s)."
            )
            _log_safety_event("blast_radius_blocked", action, reason, hostname,
                              {"cooldown_remaining_s": cooldown_s - elapsed})
            return False, reason

    return True, ""


def record_action(action: str, hostname: str) -> None:
    """Record that an action was executed (for cooldown tracking)."""
    _cooldown_tracker[(action, hostname)] = time.time()
    cooldown_s = _env_int("BLAST_RADIUS_COOLDOWN_S", 300)
    logger.info(
        "blast_radius: %s on %s recorded (cooldown: %ss)",
        action, hostname, cooldown_s,
    )


# ═══════════════════════════════════════════════════════════════════
# Circuit Breaker (D1) — temporal failure-rate limit
# ═══════════════════════════════════════════════════════════════════

# In-memory failure tracking: {action_type: [failure_timestamps]}
_failure_log: dict[str, list[float]] = {}

# Circuit state: None=closed, float=half_open_since_timestamp
_circuit_state: dict[str, Optional[float]] = {}

# Successful test execution tracking during half-open
_half_open_test_done: dict[str, bool] = {}


def check_circuit(action: str) -> Tuple[bool, str]:
    """
    Check if the circuit breaker permits this action.

    Returns (permitted, reason). If the circuit is open (tripped), the
    action is blocked. If half-open, exactly one test execution is allowed.
    """
    max_failures = _env_int("CIRCUIT_BREAKER_MAX_FAILURES", 5)
    cooldown_s = _env_int("CIRCUIT_BREAKER_COOLDOWN_S", 3600)

    state = _circuit_state.get(action)

    if state is None:
        # Circuit closed — check if we should trip based on recent failures
        return _check_trip(action, max_failures)

    # Circuit is tripped. Check if cooldown has elapsed → go half-open.
    elapsed = time.time() - state
    if elapsed < cooldown_s:
        return False, (
            f"Circuit breaker OPEN for '{action}': {_recent_failure_count(action)} failures "
            f"in the last hour. Auto-execution disabled. "
            f"Reopens in {cooldown_s - elapsed:.0f}s."
        )

    # Half-open: allow exactly one test execution
    if _half_open_test_done.get(action, False):
        return False, (
            f"Circuit breaker HALF-OPEN for '{action}': test execution already used. "
            f"Wait for test result before trying again."
        )

    _half_open_test_done[action] = True
    logger.warning("Circuit breaker HALF-OPEN for '%s': allowing 1 test execution.", action)
    return True, ""


def record_failure(action: str) -> None:
    """
    Record a failed action execution. If the failure threshold is exceeded
    within the window, the circuit trips.
    """
    window_s = _env_int("CIRCUIT_BREAKER_WINDOW_S", 3600)
    now = time.time()

    if action not in _failure_log:
        _failure_log[action] = []
    _failure_log[action].append(now)

    # Prune old failures outside the window
    cutoff = now - window_s
    _failure_log[action] = [t for t in _failure_log[action] if t > cutoff]

    count = len(_failure_log[action])
    max_failures = _env_int("CIRCUIT_BREAKER_MAX_FAILURES", 5)

    if count >= max_failures:
        _circuit_state[action] = now
        _half_open_test_done[action] = False
        logger.error(
            "Circuit breaker TRIPPED for '%s': %d failures within %ss. "
            "Auto-execution disabled for %ss.",
            action, count, window_s, _env_int("CIRCUIT_BREAKER_COOLDOWN_S", 3600),
        )
        _log_safety_event("circuit_breaker_tripped", action,
                          f"{count} failures in {window_s}s",
                          metadata={"failure_count": count, "window_s": window_s})


def record_success(action: str) -> None:
    """Record a successful action — resets the circuit."""
    was_open = action in _circuit_state
    if action in _failure_log:
        _failure_log[action] = []
    if action in _circuit_state:
        del _circuit_state[action]
    if action in _half_open_test_done:
        del _half_open_test_done[action]
    if was_open:
        _log_safety_event("circuit_breaker_reset", action, "successful execution after trip")
    logger.info("Circuit breaker RESET for '%s': successful execution.", action)


def _recent_failure_count(action: str) -> int:
    window_s = _env_int("CIRCUIT_BREAKER_WINDOW_S", 3600)
    cutoff = time.time() - window_s
    return sum(1 for t in _failure_log.get(action, []) if t > cutoff)


def _check_trip(action: str, max_failures: int) -> Tuple[bool, str]:
    """Check if failure count exceeds threshold (circuit closed, pre-trip check)."""
    count = _recent_failure_count(action)
    if count >= max_failures:
        _circuit_state[action] = time.time()
        _half_open_test_done[action] = False
        logger.error(
            "Circuit breaker TRIPPED for '%s': %d failures detected. Disabling auto-execution.",
            action, count,
        )
        return False, (
            f"Circuit breaker TRIPPED for '{action}': {count} failures detected. "
            f"Auto-execution disabled for {_env_int('CIRCUIT_BREAKER_COOLDOWN_S', 3600)}s."
        )
    return True, ""


def is_circuit_open(action: str) -> bool:
    """Quick check: is the circuit currently blocking this action?"""
    ok, _ = check_circuit(action)
    return not ok


# ═══════════════════════════════════════════════════════════════════
# Effect Verification Window (C3) — post-action monitoring
# ═══════════════════════════════════════════════════════════════════

# In-memory tracking: {(action, hostname): action_timestamp}
_effect_windows: dict[tuple[str, str], float] = {}
_effect_verified: dict[tuple[str, str], bool] = {}


def start_effect_window(action: str, hostname: str) -> None:
    """Start a post-action monitoring window. Called after action dispatch."""
    _effect_windows[(action, hostname)] = time.time()
    _effect_verified[(action, hostname)] = False
    window_s = _env_int("EFFECT_VERIFY_WINDOW_S", 600)  # default 10 min
    logger.info(
        "Effect window started: %s on %s (verify within %ss)",
        action, hostname, window_s,
    )


def check_effect_window(action: str, hostname: str, alert_recurred: bool = False) -> dict:
    """
    Check the effect verification window for a completed action.

    Returns a dict with keys:
      verified: bool — did the remediation work?
      window_elapsed: bool — has the monitoring window passed?
      recurred: bool — did the alert re-fire?
      instruction: str — what to do next
    """
    key = (action, hostname)
    started = _effect_windows.get(key)
    window_s = _env_int("EFFECT_VERIFY_WINDOW_S", 600)

    if started is None:
        return {
            "verified": True,
            "window_elapsed": True,
            "note": "No effect window was started for this action.",
        }

    elapsed = time.time() - started
    window_elapsed = elapsed >= window_s

    if alert_recurred:
        # Alert re-fired within window → remediation ineffective
        from node_operations.blast_radius import record_failure as _cb_fail
        _cb_fail(action)
        _effect_verified[key] = False
        return {
            "verified": False,
            "window_elapsed": window_elapsed,
            "recurred": True,
            "instruction": (
                f"REMEDIATION INEFFECTIVE: alert re-fired on {hostname} within "
                f"{elapsed:.0f}s of {action}. The underlying problem was not solved. "
                f"Escalate to human. Circuit breaker failure recorded."
            ),
        }

    if window_elapsed and not alert_recurred:
        _effect_verified[key] = True
        return {
            "verified": True,
            "window_elapsed": True,
            "recurred": False,
            "instruction": (
                f"Effect verified: no alert re-fire on {hostname} within {window_s}s "
                f"of {action}. Remediation appears successful."
            ),
        }

    # Window still open, no recurrence yet
    return {
        "verified": False,
        "window_elapsed": False,
        "recurred": False,
        "instruction": (
            f"Effect window still open: {elapsed:.0f}s elapsed, {window_s - elapsed:.0f}s "
            f"remaining. Check again after the window closes."
        ),
    }


# ═══════════════════════════════════════════════════════════════════
# Escalation Policy (D3) — when to hand off to human
# ═══════════════════════════════════════════════════════════════════

# Env vars:
#   ESCALATION_CONFIDENCE_THRESHOLD — min confidence for auto-action (default: 0.7)
#   ESCALATION_NOVELTY_ACTION       — what to do with novel faults (default: "escalate")

def should_escalate(
    fault_class: str = "",
    confidence: float = 1.0,
    is_novel: bool = False,
    circuit_open: bool = False,
) -> tuple[bool, str]:
    """
    Determine whether the agent should escalate to a human.

    Returns (should_escalate: bool, reason: str).

    Called by the agent before proposing an autonomous action. If True,
    the agent should not proceed with autonomous action — escalate instead.

    Rules (in priority order):
      1. Circuit breaker open → ESCALATE (system is not healthy)
      2. Novel fault (no taxonomy match, no memory match) → ESCALATE
      3. Confidence below threshold → ESCALATE
      4. Otherwise → proceed (no escalation needed)
    """
    confidence_threshold = float(os.environ.get("ESCALATION_CONFIDENCE_THRESHOLD", "0.7"))
    novelty_action = os.environ.get("ESCALATION_NOVELTY_ACTION", "escalate")

    if circuit_open:
        return True, "Escalating: circuit breaker is open for this action type. Auto-execution is temporarily disabled."

    if is_novel:
        if novelty_action == "escalate":
            return True, "Escalating: fault is novel — no matching taxonomy node or past incident. Human diagnosis required before autonomous action can be authorized."
        elif novelty_action == "shadow":
            return False, "Novel fault — proceeding in shadow mode (proposal only, no execution)."

    if confidence < confidence_threshold:
        return True, f"Escalating: confidence {confidence:.2f} is below threshold {confidence_threshold}. Diagnosis is too uncertain for autonomous action."

    return False, ""
