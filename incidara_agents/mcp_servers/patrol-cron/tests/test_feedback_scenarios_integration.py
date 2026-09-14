"""Scenario acceptance tests for RMA feedback rule optimization.

Requires a disposable PostgreSQL database:
    FEEDBACK_WORKFLOW_TEST_DB_URL=postgresql://... pytest .../test_feedback_scenarios_integration.py -q
"""

from __future__ import annotations

import json
import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest

_WORKFLOW_SPEC = importlib.util.spec_from_file_location(
    "feedback_workflow_integration_helpers",
    Path(__file__).with_name("test_feedback_workflow_integration.py"),
)
_WORKFLOW = importlib.util.module_from_spec(_WORKFLOW_SPEC)
_WORKFLOW_SPEC.loader.exec_module(_WORKFLOW)

BAD_ANALYZE_CODE = _WORKFLOW.BAD_ANALYZE_CODE
FIXED_ANALYZE_CODE = _WORKFLOW.FIXED_ANALYZE_CODE
_execute = _WORKFLOW._execute
_fetch_one = _WORKFLOW._fetch_one
feedback_workflow_db = _WORKFLOW.feedback_workflow_db


MISCLASSIFIED_BAD_CODE = """
def analyze(collected, state):
    findings = []
    for target in collected.targets:
        if target.payload.get("misclassified_event"):
            findings.append(Finding(
                target_id=target.id,
                severity="warning",
                action="alert",
                evidence={"reason": "misclassified warning"},
                confidence=0.8,
            ))
    return findings, state
"""


MISCLASSIFIED_FIXED_CODE = """
def analyze(collected, state):
    findings = []
    for target in collected.targets:
        if target.payload.get("misclassified_event"):
            findings.append(Finding(
                target_id=target.id,
                severity="critical",
                action="drain_node",
                evidence={"reason": "corrected severe event"},
                confidence=0.95,
            ))
    return findings, state
"""


FIXED_ANALYZE_CODE_V2 = FIXED_ANALYZE_CODE + "\n# second refinement\n"


def _seed_rule_case(
    connect,
    *,
    rule_id: str,
    repair_outcome: str,
    analyze_code: str = BAD_ANALYZE_CODE,
    evidence_json: str = '{"transient_xid": true}',
):
    collector_name = f"{rule_id}_collector"
    target_id = f"{rule_id}_node"

    _execute(
        connect,
        """
        INSERT INTO collectors
            (name, description, schedule_sec, target_type, sources, created_by)
        VALUES (%s, %s, 60, 'node', '[]'::jsonb, 'integration-test')
        """,
        (collector_name, f"{rule_id} collector"),
    )
    _execute(
        connect,
        """
        INSERT INTO patrol_rules
            (rule_id, name, binds_to, analyze_code, stage, created_by)
        VALUES (%s, %s, %s, %s, 'submit_alert', 'integration-test')
        """,
        (rule_id, rule_id, collector_name, analyze_code),
    )
    finding_id = _fetch_one(
        connect,
        f"""
        INSERT INTO patrol_findings
            (rule_id, target_id, target_type, severity, action, action_params,
             evidence, confidence, verdict, collector_snapshot_id, raw_evidence_hash)
        VALUES
            (%s, %s, 'node', 'warning', 'alert', '{{}}'::jsonb,
             '{evidence_json}'::jsonb, 0.9, 'confirmed', %s, %s)
        RETURNING finding_id
        """,
        (rule_id, target_id, f"{rule_id}-snap-1", f"{rule_id}-hash-1"),
    )[0]
    case_id = _fetch_one(
        connect,
        """
        INSERT INTO case_memory
            (hostname, our_classification, vendor_verdict, rma_completed_at, collected_by)
        VALUES
            (%s, 'GPU_XID', %s, NOW(), 'integration-test')
        RETURNING id
        """,
        (target_id, repair_outcome),
    )[0]
    return {
        "rule_id": rule_id,
        "collector_name": collector_name,
        "target_id": target_id,
        "finding_id": finding_id,
        "case_id": case_id,
    }


def _reconcile_and_attribute(ids, *, repair_outcome: str, attribution: str):
    from agent_feedback import knowledge_db
    from patrol_cron import mcp_tools

    reconciled = json.loads(mcp_tools.reconcile_finding(ids["finding_id"], repair_outcome))
    assert reconciled["status"] == "reconciled"
    assert knowledge_db.insert_rma_finding_reconciliation(
        case_id=ids["case_id"],
        finding_id=ids["finding_id"],
        rule_id=ids["rule_id"],
        repair_outcome=repair_outcome,
    )
    assert knowledge_db.update_rma_finding_attribution(
        case_id=ids["case_id"],
        finding_id=ids["finding_id"],
        attribution=attribution,
        attribution_confidence="high",
        fix_route="rule_code" if attribution == "detection" else "observe",
    )


def _reconcile_and_attribute_with_label(
    ids,
    *,
    repair_outcome: str,
    attribution: str,
    feedback_label: str = "",
    expected_behavior: dict | None = None,
):
    from agent_feedback import knowledge_db
    from patrol_cron import mcp_tools

    reconciled = json.loads(mcp_tools.reconcile_finding(ids["finding_id"], repair_outcome))
    assert reconciled["status"] == "reconciled"
    assert knowledge_db.insert_rma_finding_reconciliation(
        case_id=ids["case_id"],
        finding_id=ids["finding_id"],
        rule_id=ids["rule_id"],
        repair_outcome=repair_outcome,
        feedback_label=feedback_label,
        expected_behavior=expected_behavior,
    )
    assert knowledge_db.update_rma_finding_attribution(
        case_id=ids["case_id"],
        finding_id=ids["finding_id"],
        attribution=attribution,
        attribution_confidence="high",
        fix_route="rule_code" if attribution == "detection" else "observe",
        feedback_label=feedback_label,
        expected_behavior=expected_behavior,
    )


def _seed_guard_case(
    connect,
    ids,
    *,
    repair_outcome: str,
    evidence_json: str,
    suffix: str,
):
    target_id = f"{ids['rule_id']}_{suffix}_node"
    finding_id = _fetch_one(
        connect,
        f"""
        INSERT INTO patrol_findings
            (rule_id, target_id, target_type, severity, action, action_params,
             evidence, confidence, verdict, collector_snapshot_id, raw_evidence_hash)
        VALUES
            (%s, %s, 'node', 'warning', 'alert', '{{}}'::jsonb,
             '{evidence_json}'::jsonb, 0.9, 'confirmed', %s, %s)
        RETURNING finding_id
        """,
        (ids["rule_id"], target_id, f"{ids['rule_id']}-{suffix}-snap", f"{ids['rule_id']}-{suffix}-hash"),
    )[0]
    case_id = _fetch_one(
        connect,
        """
        INSERT INTO case_memory
            (hostname, our_classification, vendor_verdict, rma_completed_at, collected_by)
        VALUES
            (%s, 'GPU_XID', %s, NOW(), 'integration-test')
        RETURNING id
        """,
        (target_id, repair_outcome),
    )[0]
    return {
        **ids,
        "target_id": target_id,
        "finding_id": finding_id,
        "case_id": case_id,
    }


def _add_positive_guard(connect, ids):
    guard_ids = _seed_guard_case(
        connect,
        ids,
        repair_outcome="REPAIR_CONFIRMED",
        evidence_json='{"hard_failure": true}',
        suffix="positive",
    )
    _reconcile_and_attribute(guard_ids, repair_outcome="REPAIR_CONFIRMED", attribution="detection")
    return guard_ids


def _add_negative_guard(connect, ids):
    guard_ids = _seed_guard_case(
        connect,
        ids,
        repair_outcome="NO_FAULT_FOUND",
        evidence_json='{"transient_xid": true}',
        suffix="negative",
    )
    _reconcile_and_attribute(guard_ids, repair_outcome="NO_FAULT_FOUND", attribution="detection")
    return guard_ids


def _create_replay_cases_from_feedback(rule_id: str):
    from patrol_cron import mcp_tools

    return json.loads(mcp_tools.create_rule_replay_cases_from_feedback(
        rule_id,
        created_by="integration-test",
    ))


def _mark_dirty(connect, rule_id: str):
    _execute(
        connect,
        """
        INSERT INTO rule_reconciliation_state
            (rule_id, reconciliation_dirty, updated_at)
        VALUES (%s, TRUE, NOW())
        ON CONFLICT (rule_id) DO UPDATE SET
            reconciliation_dirty = TRUE,
            updated_at = NOW()
        """,
        (rule_id,),
    )


def _create_nff_replay(ids):
    from patrol_cron import mcp_tools

    return json.loads(mcp_tools.create_rule_replay_case(
        rule_id=ids["rule_id"],
        finding_id=ids["finding_id"],
        case_id=ids["case_id"],
        source="rma_bad_feedback",
        repair_outcome="NO_FAULT_FOUND",
        attribution="detection",
        collector_snapshot_id=f"{ids['rule_id']}-snap-1",
        raw_evidence_hash=f"{ids['rule_id']}-hash-1",
        frozen_input={
            "collector_name": ids["collector_name"],
            "collector_status": "success",
            "targets": [
                {
                    "id": ids["target_id"],
                    "type": "node",
                    "payload": {"transient_xid": True},
                }
            ],
            "errors": [],
            "duration": 0.0,
        },
        expected_behavior={"should_fire": False},
        created_by="integration-test",
    ))


def _patch_live_collector(monkeypatch, ids, payload):
    from patrol_cron.models import CollectionResult, TargetData

    def fake_run_collector(_collector_cfg):
        return CollectionResult(
            collector_name=ids["collector_name"],
            targets=[TargetData(id=ids["target_id"], type="node", payload=payload)],
        )

    monkeypatch.setattr("patrol_cron.engine.run_collector", fake_run_collector)


def _rule_state(connect, rule_id: str):
    return _fetch_one(
        connect,
        """
        SELECT r.analyze_code, r.stage, s.reconciliation_dirty,
               s.nff_baseline_at, s.reconcile_attempts
        FROM patrol_rules r
        LEFT JOIN rule_reconciliation_state s ON s.rule_id = r.rule_id
        WHERE r.rule_id = %s
        """,
        (rule_id,),
    )


def test_detection_nff_replay_fixture_rule_fixed_and_dirty_clears(feedback_workflow_db, monkeypatch):
    from patrol_cron import db as patrol_db
    from patrol_cron import mcp_tools

    ids = _seed_rule_case(feedback_workflow_db, rule_id="scenario_nff", repair_outcome="NO_FAULT_FOUND")
    _reconcile_and_attribute(ids, repair_outcome="NO_FAULT_FOUND", attribution="detection")
    _add_positive_guard(feedback_workflow_db, ids)
    replay = _create_replay_cases_from_feedback(ids["rule_id"])
    assert replay["created"] == 2
    assert replay["positive"] == 1
    assert replay["negative"] == 1
    _mark_dirty(feedback_workflow_db, ids["rule_id"])
    _patch_live_collector(monkeypatch, ids, {"transient_xid": True})

    assert patrol_db.get_rule_bad_feedback_rate(ids["rule_id"])["bad_count"] == 1
    result = json.loads(mcp_tools.update_rule_code(
        ids["rule_id"],
        FIXED_ANALYZE_CODE,
        reason="nff_refinement",
    ))
    assert result["status"] == "updated"

    rule_code, _stage, dirty, baseline_at, attempts = _rule_state(feedback_workflow_db, ids["rule_id"])
    assert rule_code == FIXED_ANALYZE_CODE
    assert dirty is False
    assert baseline_at is not None
    assert attempts == 1


def test_detection_misclassified_replay_expects_corrected_action_and_severity(
    feedback_workflow_db,
    monkeypatch,
):
    from patrol_cron import mcp_tools

    ids = _seed_rule_case(
        feedback_workflow_db,
        rule_id="scenario_misclassified",
        repair_outcome="MISCLASSIFIED",
        analyze_code=MISCLASSIFIED_BAD_CODE,
        evidence_json='{"misclassified_event": true}',
    )
    _reconcile_and_attribute_with_label(
        ids,
        repair_outcome="MISCLASSIFIED",
        attribution="detection",
        feedback_label="positive",
        expected_behavior={
            "should_fire": True,
            "expected_action": "drain_node",
            "expected_severity": "critical",
            "expected_target_id": ids["target_id"],
        },
    )
    _add_negative_guard(feedback_workflow_db, ids)
    replay = _create_replay_cases_from_feedback(ids["rule_id"])
    assert replay["created"] == 2
    assert replay["positive"] == 1
    assert replay["negative"] == 1
    _mark_dirty(feedback_workflow_db, ids["rule_id"])
    _patch_live_collector(monkeypatch, ids, {"misclassified_event": True})

    result = json.loads(mcp_tools.update_rule_code(
        ids["rule_id"],
        MISCLASSIFIED_FIXED_CODE,
        reason="misclassified_refinement",
    ))
    assert result["status"] == "updated"

    rule_code, _stage, dirty, baseline_at, attempts = _rule_state(feedback_workflow_db, ids["rule_id"])
    assert rule_code == MISCLASSIFIED_FIXED_CODE
    assert dirty is False
    assert baseline_at is not None
    assert attempts == 1


@pytest.mark.parametrize("attribution", ["triage", "inspect", "repair", "automation"])
def test_non_detection_attribution_does_not_dirty_rule_or_create_rule_replay(
    feedback_workflow_db,
    attribution,
):
    from patrol_cron import db as patrol_db
    from patrol_cron import mcp_tools

    ids = _seed_rule_case(
        feedback_workflow_db,
        rule_id=f"scenario_{attribution}",
        repair_outcome="NO_FAULT_FOUND",
    )
    _reconcile_and_attribute(ids, repair_outcome="NO_FAULT_FOUND", attribution=attribution)

    assert patrol_db.list_dirty_reconciliation_rules() == []
    assert patrol_db.get_rule_bad_feedback_rate(ids["rule_id"])["bad_count"] == 0
    replay_cases = json.loads(mcp_tools.list_rule_replay_cases(ids["rule_id"]))
    assert replay_cases["replay_cases"] == []
    rule_code, _stage, dirty, baseline_at, attempts = _rule_state(feedback_workflow_db, ids["rule_id"])
    assert rule_code == BAD_ANALYZE_CODE
    assert dirty is None
    assert baseline_at is None
    assert attempts is None


def test_replay_failure_rejects_update_and_keeps_dirty_true(feedback_workflow_db):
    from patrol_cron import mcp_tools

    ids = _seed_rule_case(feedback_workflow_db, rule_id="scenario_replay_fails", repair_outcome="NO_FAULT_FOUND")
    _reconcile_and_attribute(ids, repair_outcome="NO_FAULT_FOUND", attribution="detection")
    _create_nff_replay(ids)
    _mark_dirty(feedback_workflow_db, ids["rule_id"])

    result = json.loads(mcp_tools.update_rule_code(
        ids["rule_id"],
        BAD_ANALYZE_CODE,
        reason="nff_refinement",
    ))
    assert result["status"] == "rejected"
    assert result["reason"] == "replay_failed"

    rule_code, _stage, dirty, baseline_at, attempts = _rule_state(feedback_workflow_db, ids["rule_id"])
    assert rule_code == BAD_ANALYZE_CODE
    assert dirty is True
    assert baseline_at is None
    assert attempts == 0


def test_empty_replay_suite_rejects_update_and_keeps_dirty_true(feedback_workflow_db):
    from patrol_cron import mcp_tools

    ids = _seed_rule_case(feedback_workflow_db, rule_id="scenario_empty_replay", repair_outcome="NO_FAULT_FOUND")
    _reconcile_and_attribute(ids, repair_outcome="NO_FAULT_FOUND", attribution="detection")
    _mark_dirty(feedback_workflow_db, ids["rule_id"])

    result = json.loads(mcp_tools.update_rule_code(
        ids["rule_id"],
        FIXED_ANALYZE_CODE,
        reason="nff_refinement",
    ))
    assert result["status"] == "rejected"
    assert result["reason"] == "replay_failed"
    assert result["details"]["error"] == f"No replay cases found for rule '{ids['rule_id']}'"

    rule_code, _stage, dirty, baseline_at, attempts = _rule_state(feedback_workflow_db, ids["rule_id"])
    assert rule_code == BAD_ANALYZE_CODE
    assert dirty is True
    assert baseline_at is None
    assert attempts == 0


@pytest.mark.parametrize("mode", ["safe_mode", "shadow_mode"])
def test_containment_demotes_to_log_only_and_keeps_dirty_true(feedback_workflow_db, mode):
    from patrol_cron import mcp_tools

    ids = _seed_rule_case(
        feedback_workflow_db,
        rule_id=f"scenario_{mode}",
        repair_outcome="NO_FAULT_FOUND",
    )
    _reconcile_and_attribute(ids, repair_outcome="NO_FAULT_FOUND", attribution="detection")
    _mark_dirty(feedback_workflow_db, ids["rule_id"])

    result = json.loads(mcp_tools.update_rule_code(
        ids["rule_id"],
        FIXED_ANALYZE_CODE,
        reason="nff_refinement",
        mode=mode,
    ))
    assert result == {"status": "contained", "rule_id": ids["rule_id"], "mode": mode, "dirty": True}

    rule_code, stage, dirty, baseline_at, attempts = _rule_state(feedback_workflow_db, ids["rule_id"])
    assert rule_code == BAD_ANALYZE_CODE
    assert stage == "log_only"
    assert dirty is True
    assert baseline_at is None
    assert attempts == 0


def test_second_bad_feedback_after_baseline_increments_attempts_again(
    feedback_workflow_db,
    monkeypatch,
):
    from agent_feedback import knowledge_db
    from patrol_cron import mcp_tools

    ids = _seed_rule_case(feedback_workflow_db, rule_id="scenario_second_feedback", repair_outcome="NO_FAULT_FOUND")
    _reconcile_and_attribute(ids, repair_outcome="NO_FAULT_FOUND", attribution="detection")
    _add_positive_guard(feedback_workflow_db, ids)
    replay = _create_replay_cases_from_feedback(ids["rule_id"])
    assert replay["created"] == 2
    _mark_dirty(feedback_workflow_db, ids["rule_id"])
    _patch_live_collector(monkeypatch, ids, {"transient_xid": True})

    first = json.loads(mcp_tools.update_rule_code(
        ids["rule_id"],
        FIXED_ANALYZE_CODE,
        reason="nff_refinement",
    ))
    assert first["status"] == "updated"
    _rule_code, _stage, dirty, first_baseline_at, attempts = _rule_state(
        feedback_workflow_db,
        ids["rule_id"],
    )
    assert dirty is False
    assert first_baseline_at is not None
    assert attempts == 1

    second_detected_at = first_baseline_at + timedelta(milliseconds=1)
    second_finding_id = _fetch_one(
        feedback_workflow_db,
        """
        INSERT INTO patrol_findings
            (rule_id, target_id, target_type, severity, action, action_params,
             evidence, confidence, verdict, collector_snapshot_id, raw_evidence_hash, detected_at)
        VALUES
            (%s, %s, 'node', 'warning', 'alert', '{}'::jsonb,
             '{"transient_xid": true}'::jsonb, 0.9, 'confirmed', 'snap-2', 'hash-2', %s)
        RETURNING finding_id
        """,
        (ids["rule_id"], ids["target_id"], second_detected_at),
    )[0]
    second_case_id = _fetch_one(
        feedback_workflow_db,
        """
        INSERT INTO case_memory
            (hostname, our_classification, vendor_verdict, rma_completed_at, collected_by)
        VALUES
            (%s, 'GPU_XID', 'NO_FAULT_FOUND', NOW(), 'integration-test')
        RETURNING id
        """,
        (ids["target_id"],),
    )[0]
    assert knowledge_db.insert_rma_finding_reconciliation(
        case_id=second_case_id,
        finding_id=second_finding_id,
        rule_id=ids["rule_id"],
        repair_outcome="NO_FAULT_FOUND",
    )
    assert knowledge_db.update_rma_finding_attribution(
        case_id=second_case_id,
        finding_id=second_finding_id,
        attribution="detection",
        attribution_confidence="high",
        fix_route="rule_code",
    )
    replay = json.loads(mcp_tools.create_rule_replay_case(
        rule_id=ids["rule_id"],
        finding_id=second_finding_id,
        case_id=second_case_id,
        source="rma_bad_feedback",
        repair_outcome="NO_FAULT_FOUND",
        attribution="detection",
        frozen_input={
            "collector_name": ids["collector_name"],
            "collector_status": "success",
            "targets": [
                {
                    "id": ids["target_id"],
                    "type": "node",
                    "payload": {"transient_xid": True},
                }
            ],
        },
        expected_behavior={"should_fire": False},
        created_by="integration-test",
    ))
    assert replay["status"] == "created"
    _mark_dirty(feedback_workflow_db, ids["rule_id"])

    second_feedback = json.loads(mcp_tools.update_rule_code(
        ids["rule_id"],
        FIXED_ANALYZE_CODE_V2,
        reason="nff_refinement",
    ))
    assert second_feedback["status"] == "updated"

    rule_code, _stage, dirty, second_baseline_at, attempts = _rule_state(
        feedback_workflow_db,
        ids["rule_id"],
    )
    assert rule_code == FIXED_ANALYZE_CODE_V2
    assert dirty is False
    assert second_baseline_at > first_baseline_at
    assert attempts == 2
