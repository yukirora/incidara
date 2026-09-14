"""Tests for blast_radius.py — Feature 1 (Autonomy Graduation) C1."""

import os
import time
from unittest import mock

import pytest

# Import the module under test by path
import sys
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_TEST_DIR, "../../../..")
_NODE_OPS_DIR = os.path.join(_PROJECT_ROOT, "incidara_agents/mcp_servers/node-operations")
sys.path.insert(0, _NODE_OPS_DIR)

from node_operations.blast_radius import check_action, record_action, _cooldown_tracker
from node_operations.blast_radius import (
    check_circuit,
    record_failure,
    record_success,
    is_circuit_open,
    _failure_log,
    _circuit_state,
    _half_open_test_done,
)


class TestBlastRadius:
    def setup_method(self):
        """Reset env and cooldown tracker before each test."""
        for key in list(os.environ.keys()):
            if key.startswith("BLAST_RADIUS_"):
                del os.environ[key]
        _cooldown_tracker.clear()

    # ── Max nodes per action ────────────────────────────────────

    def test_single_node_allowed_by_default(self):
        ok, reason = check_action("cordon", hostnames=["h200-001"])
        assert ok
        assert reason == ""

    def test_21_nodes_blocked_by_default(self):
        ok, reason = check_action("cordon", hostnames=[f"h200-{i:03d}" for i in range(21)])
        assert not ok
        assert "targets 21 nodes" in reason
        assert "max: 20" in reason

    def test_max_nodes_from_env(self):
        os.environ["BLAST_RADIUS_MAX_NODES"] = "3"
        ok, _ = check_action("cordon", hostnames=["h200-001", "h200-002"])
        assert ok  # 2 nodes allowed when max=3

        ok, reason = check_action("cordon", hostnames=["h200-001", "h200-002", "h200-003", "h200-004"])
        assert not ok
        assert "targets 4 nodes" in reason

    # ── Cooldown ────────────────────────────────────────────────

    def test_cooldown_blocks_rapid_repeat(self):
        ok, _ = check_action("cordon", hostnames=["h200-001"])
        assert ok
        record_action("cordon", "h200-001")

        # Immediate retry should be blocked
        ok, reason = check_action("cordon", hostnames=["h200-001"])
        assert not ok
        assert "Cooldown active" in reason

    def test_cooldown_expires(self):
        cooldown = 1  # 1 second for fast test
        os.environ["BLAST_RADIUS_COOLDOWN_S"] = str(cooldown)

        ok, _ = check_action("cordon", hostnames=["h200-001"])
        assert ok
        record_action("cordon", "h200-001")

        # Wait for cooldown to expire
        time.sleep(1.1)

        ok, _ = check_action("cordon", hostnames=["h200-001"])
        assert ok

    def test_cooldown_per_action_per_node(self):
        """Cooldown is tracked per (action, hostname) pair."""
        record_action("cordon", "h200-001")

        # Different action on same node — should be allowed
        ok, _ = check_action("drain", hostnames=["h200-001"])
        assert ok

        # Same action on different node — should be allowed
        ok, _ = check_action("cordon", hostnames=["h200-002"])
        assert ok

        # Same action on same node — blocked
        ok, reason = check_action("cordon", hostnames=["h200-001"])
        assert not ok

    # ── Fail-closed ─────────────────────────────────────────────

    def test_fail_closed_true_by_default(self):
        # With no env vars set, max_nodes is 1 — a single node is fine
        ok, _ = check_action("cordon", hostnames=["h200-001"])
        assert ok

    def test_fail_closed_false_allows_all(self):
        os.environ["BLAST_RADIUS_MAX_NODES"] = "100"
        os.environ["BLAST_RADIUS_FAIL_CLOSED"] = "false"
        ok, _ = check_action("cordon", hostnames=[f"h200-{i:03d}" for i in range(50)])
        assert ok  # 50 nodes allowed when max=100

    # ── record_action ───────────────────────────────────────────

    def test_record_action_stores_timestamp(self):
        before = time.time()
        record_action("cordon", "h200-001")
        after = time.time()

        ts = _cooldown_tracker[("cordon", "h200-001")]
        assert before <= ts <= after


# ═══════════════════════════════════════════════════════════════════
# Circuit Breaker (D1) Tests
# ═══════════════════════════════════════════════════════════════════

class TestCircuitBreaker:
    def setup_method(self):
        """Reset circuit breaker state before each test."""
        for key in list(os.environ.keys()):
            if key.startswith("CIRCUIT_BREAKER_"):
                del os.environ[key]
        _failure_log.clear()
        _circuit_state.clear()
        _half_open_test_done.clear()

    def test_circuit_closed_allows_action(self):
        ok, reason = check_circuit("cordon")
        assert ok

    def test_one_failure_does_not_trip(self):
        record_failure("cordon")
        ok, _ = check_circuit("cordon")
        assert ok

    def test_five_failures_trips_circuit(self):
        for _ in range(5):
            record_failure("cordon")
        ok, reason = check_circuit("cordon")
        assert not ok
        assert "TRIPPED" in reason or "OPEN" in reason

    def test_circuit_trip_only_affects_specific_action(self):
        for _ in range(5):
            record_failure("cordon")
        ok, _ = check_circuit("drain")
        assert ok

    def test_half_open_allows_one_test(self):
        os.environ["CIRCUIT_BREAKER_COOLDOWN_S"] = "0"
        for _ in range(5):
            record_failure("cordon")
        # record_failure already tripped on 3rd failure.
        # First check_circuit after trip = half-open test (allowed).
        ok, _ = check_circuit("cordon")
        assert ok

    def test_half_open_blocks_second_test(self):
        os.environ["CIRCUIT_BREAKER_COOLDOWN_S"] = "0"
        for _ in range(5):
            record_failure("cordon")
        # record_failure already tripped. First check = half-open test.
        check_circuit("cordon")  # half-open: test allowed
        ok, reason = check_circuit("cordon")  # half-open: second blocked
        assert not ok

    def test_record_success_resets_circuit(self):
        os.environ["CIRCUIT_BREAKER_MAX_FAILURES"] = "1"
        record_failure("cordon")
        assert is_circuit_open("cordon")
        record_success("cordon")
        assert not is_circuit_open("cordon")

    def test_failures_outside_window_dont_count(self):
        os.environ["CIRCUIT_BREAKER_WINDOW_S"] = "1"
        os.environ["CIRCUIT_BREAKER_MAX_FAILURES"] = "2"
        record_failure("cordon")
        record_failure("cordon")
        assert is_circuit_open("cordon")
        time.sleep(1.1)
        record_success("cordon")
        record_failure("cordon")
        ok, _ = check_circuit("cordon")
        assert ok

    def test_custom_max_failures_from_env(self):
        os.environ["CIRCUIT_BREAKER_MAX_FAILURES"] = "5"
        for _ in range(4):
            record_failure("cordon")
        ok, _ = check_circuit("cordon")
        assert ok
        record_failure("cordon")
        ok, _ = check_circuit("cordon")
        assert not ok


# ═══════════════════════════════════════════════════════════════════
# Effect Verification Window (C3) Tests
# ═══════════════════════════════════════════════════════════════════

from node_operations.blast_radius import start_effect_window, check_effect_window


class TestEffectWindow:
    def setup_method(self):
        for key in list(os.environ.keys()):
            if key.startswith("EFFECT_VERIFY_"):
                del os.environ[key]
        from node_operations.blast_radius import _effect_windows, _effect_verified
        _effect_windows.clear()
        _effect_verified.clear()

    def test_no_window_returns_verified(self):
        result = check_effect_window("cordon", "h200-001")
        assert result["verified"] is True
        assert "No effect window" in result["note"]

    def test_alert_recurred_flags_ineffective(self):
        start_effect_window("cordon", "h200-001")
        result = check_effect_window("cordon", "h200-001", alert_recurred=True)
        assert result["verified"] is False
        assert result["recurred"] is True
        assert "REMEDIATION INEFFECTIVE" in result["instruction"]

    def test_window_still_open_without_recurrence(self):
        os.environ["EFFECT_VERIFY_WINDOW_S"] = "600"
        start_effect_window("cordon", "h200-001")
        result = check_effect_window("cordon", "h200-001")
        assert result["verified"] is False
        assert result["window_elapsed"] is False
        assert "still open" in result["instruction"]

    def test_window_elapsed_no_recurrence_is_verified(self):
        os.environ["EFFECT_VERIFY_WINDOW_S"] = "0"
        start_effect_window("cordon", "h200-001")
        result = check_effect_window("cordon", "h200-001")
        assert result["verified"] is True
        assert result["window_elapsed"] is True

    def test_alert_recurred_records_circuit_failure(self):
        os.environ["CIRCUIT_BREAKER_MAX_FAILURES"] = "3"
        start_effect_window("cordon", "h200-001")
        check_effect_window("cordon", "h200-001", alert_recurred=True)
        # Should have recorded 1 failure in circuit breaker
        assert len(_failure_log.get("cordon", [])) >= 1


# ═══════════════════════════════════════════════════════════════════
# Escalation Policy (D3) Tests
# ═══════════════════════════════════════════════════════════════════

from node_operations.blast_radius import should_escalate


class TestEscalationPolicy:
    def setup_method(self):
        for key in list(os.environ.keys()):
            if key.startswith("ESCALATION_"):
                del os.environ[key]

    def test_normal_case_no_escalation(self):
        escalate, _ = should_escalate(fault_class="Xid74", confidence=0.9)
        assert not escalate

    def test_low_confidence_escalates(self):
        escalate, reason = should_escalate(fault_class="Xid74", confidence=0.3)
        assert escalate
        assert "confidence" in reason.lower()

    def test_novel_fault_escalates(self):
        escalate, reason = should_escalate(is_novel=True)
        assert escalate
        assert "novel" in reason.lower()

    def test_novel_fault_shadow_mode(self):
        os.environ["ESCALATION_NOVELTY_ACTION"] = "shadow"
        escalate, reason = should_escalate(is_novel=True)
        assert not escalate
        assert "shadow" in reason.lower()

    def test_circuit_open_escalates(self):
        escalate, reason = should_escalate(circuit_open=True)
        assert escalate
        assert "circuit breaker" in reason.lower()

    def test_custom_confidence_threshold(self):
        os.environ["ESCALATION_CONFIDENCE_THRESHOLD"] = "0.95"
        escalate, _ = should_escalate(confidence=0.9)
        assert escalate  # 0.9 < 0.95 → escalate
        escalate2, _ = should_escalate(confidence=0.97)
        assert not escalate2  # 0.97 >= 0.95 → ok

    def test_circuit_open_overrides_confidence(self):
        """Circuit open should escalate regardless of confidence."""
        os.environ["ESCALATION_CONFIDENCE_THRESHOLD"] = "0.1"
        escalate, reason = should_escalate(confidence=0.99, circuit_open=True)
        assert escalate
        assert "circuit breaker" in reason.lower()
