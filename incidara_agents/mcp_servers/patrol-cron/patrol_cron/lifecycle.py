"""Observation lifecycle engine for patrol_cron rules."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from patrol_cron import db
from patrol_cron.actions import execute_finding
from patrol_cron.models import Observation, RuleResult

logger = logging.getLogger(__name__)

PENDING_LIFECYCLE_TTL_HOURS = float(os.environ.get("PATROL_PENDING_LIFECYCLE_TTL_HOURS", "168"))
CLOSED_LIFECYCLE_TTL_HOURS = float(os.environ.get("PATROL_CLOSED_LIFECYCLE_TTL_HOURS", "168"))


def _expiry_after(hours: float):
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _set_pending_expiry(state: dict) -> None:
    state["expires_at"] = _expiry_after(PENDING_LIFECYCLE_TTL_HOURS)


def _set_closed_expiry(state: dict) -> None:
    state["expires_at"] = _expiry_after(CLOSED_LIFECYCLE_TTL_HOURS)


def _clear_expiry(state: dict) -> None:
    state["expires_at"] = None


def _empty_state(rule_id: str, obs: Observation) -> dict:
    return {
        "rule_id": rule_id,
        "target_id": obs.target_id,
        "signal_key": obs.signal_key,
        "action": obs.action,
        "bad_count": 0,
        "healthy_count": 0,
        "active_finding_id": None,
        "last_status": None,
        "first_seen_at": None,
        "last_seen_at": None,
        "expires_at": None,
    }


def _active_finding_id(rule_id: str, state: dict, obs: Observation) -> int | None:
    active = db.find_active_finding(rule_id, obs.target_id, obs.signal_key, obs.action)
    if active:
        return active["finding_id"]
    return None


def _open_finding(
    rule: dict,
    collector: dict,
    obs: Observation,
    provenance: dict[str, Any],
) -> int | None:
    finding = obs.to_finding()
    execute_finding(finding, rule.get("stage", "log_only"), rule_id=rule["rule_id"])
    return db.insert_finding(
        rule_id=rule["rule_id"],
        target_id=finding.target_id,
        target_type=collector.get("target_type", "unknown"),
        severity=finding.severity,
        action=finding.action,
        action_params=finding.action_params,
        evidence=finding.evidence,
        confidence=finding.confidence,
        event_id=obs.event_id,
        **provenance,
    )


def _apply_condition_observation(
    obs: Observation,
    rule: dict,
    collector: dict,
    provenance: dict[str, Any],
) -> None:
    rule_id = rule["rule_id"]
    state = db.get_lifecycle_state(rule_id, obs.target_id, obs.signal_key, obs.action)
    state = state or _empty_state(rule_id, obs)

    active_id = _active_finding_id(rule_id, state, obs)
    open_after = int(obs.lifecycle.get("open_after_consecutive", 1))
    close_after = int(obs.lifecycle.get("close_after_healthy", 1))

    if obs.status == "bad":
        state["bad_count"] = int(state.get("bad_count") or 0) + 1
        state["healthy_count"] = 0
        state["last_status"] = "bad"

        if active_id:
            db.refresh_active_finding(active_id, obs.evidence, obs.confidence)
            state["active_finding_id"] = active_id
            _clear_expiry(state)
        elif state["bad_count"] >= open_after:
            state["active_finding_id"] = _open_finding(rule, collector, obs, provenance)
            _clear_expiry(state)
        else:
            _set_pending_expiry(state)

        db.upsert_lifecycle_state(state)
        return

    if obs.status == "healthy":
        state["bad_count"] = 0
        state["healthy_count"] = int(state.get("healthy_count") or 0) + 1
        state["last_status"] = "healthy"

        if active_id and state["healthy_count"] >= close_after:
            state["active_finding_id"] = None
            _set_closed_expiry(state)
            if not db.has_active_lifecycle_signals(
                rule_id,
                obs.target_id,
                obs.action,
                active_id,
                exclude_signal_key=obs.signal_key,
            ):
                db.deactivate_finding(active_id)
        elif active_id:
            state["active_finding_id"] = active_id
            _clear_expiry(state)
        else:
            _set_closed_expiry(state)

        db.upsert_lifecycle_state(state)
        return

    state["last_status"] = "unknown"
    if active_id:
        state["active_finding_id"] = active_id
    db.upsert_lifecycle_state(state)


def _apply_event_observation(
    obs: Observation,
    rule: dict,
    collector: dict,
    provenance: dict[str, Any],
) -> None:
    if obs.status != "bad":
        return
    rule_id = rule["rule_id"]
    state = db.get_lifecycle_state(rule_id, obs.target_id, obs.signal_key, obs.action)
    state = state or _empty_state(rule_id, obs)

    active = db.find_active_finding(rule_id, obs.target_id, obs.signal_key, obs.action)
    if active:
        active_id = active["finding_id"]
        db.refresh_active_finding(active_id, obs.evidence, obs.confidence)
    else:
        active_id = _open_finding(rule, collector, obs, provenance)

    state["bad_count"] = int(state.get("bad_count") or 0) + 1
    state["healthy_count"] = 0
    state["active_finding_id"] = active_id
    state["last_status"] = "bad"
    _clear_expiry(state)
    db.upsert_lifecycle_state(state)


def apply_rule_result(
    rule_result: RuleResult,
    rule: dict,
    collector: dict,
    provenance: dict[str, Any],
) -> None:
    """Apply observations to findings through rule-specified lifecycle policy."""
    for obs in rule_result.observations:
        if obs.lifecycle.get("kind") == "event":
            _apply_event_observation(obs, rule, collector, provenance)
        else:
            _apply_condition_observation(obs, rule, collector, provenance)
