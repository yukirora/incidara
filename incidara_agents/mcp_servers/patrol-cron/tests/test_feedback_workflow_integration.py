"""Backend integration test for the RMA feedback-to-rule-optimization loop.

Requires a disposable PostgreSQL database:
    FEEDBACK_WORKFLOW_TEST_DB_URL=postgresql://... pytest .../test_feedback_workflow_integration.py -q
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import pytest

AGENT_FEEDBACK_ROOT = Path(__file__).resolve().parents[2] / "agent-feedback"
sys.path.insert(0, str(AGENT_FEEDBACK_ROOT))

BAD_ANALYZE_CODE = """
def analyze(collected, state):
    findings = []
    for target in collected.targets:
        if target.payload.get("transient_xid"):
            findings.append(Finding(
                target_id=target.id,
                severity="warning",
                action="alert",
                evidence={"reason": "transient xid"},
                confidence=0.9,
            ))
    return findings, state
"""


FIXED_ANALYZE_CODE = """
def analyze(collected, state):
    findings = []
    for target in collected.targets:
        if target.payload.get("hard_failure"):
            findings.append(Finding(
                target_id=target.id,
                severity="critical",
                action="alert",
                evidence={"reason": "hard failure"},
                confidence=0.95,
            ))
    return findings, state
"""


@pytest.fixture()
def feedback_workflow_db(monkeypatch):
    db_url = os.environ.get("FEEDBACK_WORKFLOW_TEST_DB_URL")
    if not db_url:
        pytest.skip("Set FEEDBACK_WORKFLOW_TEST_DB_URL to run feedback workflow integration tests")
    db_name = urlparse(db_url).path.lstrip("/")
    if db_name == "ltp_agent":
        pytest.fail(
            "Refusing to run feedback workflow integration test against production DB 'ltp_agent'. "
            "Use a dedicated disposable test database on the same Postgres host."
        )

    schema = f"feedback_workflow_{uuid.uuid4().hex}"
    admin = psycopg2.connect(db_url)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        admin.close()

    def connect():
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}"')
        return conn

    @contextmanager
    def connect_context():
        conn = connect()
        try:
            yield conn
        finally:
            conn.close()

    from agent_feedback import knowledge_db
    from patrol_cron import db as patrol_db
    from patrol_cron import mcp_tools

    monkeypatch.setattr(patrol_db, "_conn", connect)
    monkeypatch.setattr(mcp_tools, "_conn", connect)
    monkeypatch.setattr(knowledge_db, "_conn", connect_context)

    schema_sql = Path("infra/postgresql/patrol_cron.sql").read_text()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()
    knowledge_db.ensure_table()

    try:
        yield connect
    finally:
        cleanup = psycopg2.connect(db_url)
        cleanup.autocommit = True
        try:
            with cleanup.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            cleanup.close()


def _fetch_one(connect, sql, params=()):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def _execute(connect, sql, params=()):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()


def _seed_collector_rule_finding_and_case(connect):
    rule_id = "feedback_transient_xid_v1"
    collector_name = "feedback_gpu_events"
    target_id = "node-1"

    _execute(
        connect,
        """
        INSERT INTO collectors
            (name, description, schedule_sec, target_type, sources, created_by)
        VALUES (%s, %s, 60, 'node', '[]'::jsonb, 'integration-test')
        """,
        (collector_name, "Feedback workflow collector"),
    )
    _execute(
        connect,
        """
        INSERT INTO patrol_rules
            (rule_id, name, binds_to, analyze_code, stage, created_by)
        VALUES (%s, %s, %s, %s, 'submit_alert', 'integration-test')
        """,
        (rule_id, "Transient XID feedback test", collector_name, BAD_ANALYZE_CODE),
    )

    finding_id = _fetch_one(
        connect,
        """
        INSERT INTO patrol_findings
            (rule_id, target_id, target_type, severity, action, action_params,
             evidence, confidence, verdict, collector_snapshot_id, raw_evidence_hash)
        VALUES
            (%s, %s, 'node', 'warning', 'alert', '{}'::jsonb,
             '{"transient_xid": true}'::jsonb, 0.9, 'confirmed', 'snap-1', 'hash-1')
        RETURNING finding_id
        """,
        (rule_id, target_id),
    )[0]

    case_id = _fetch_one(
        connect,
        """
        INSERT INTO case_memory
            (hostname, our_classification, vendor_verdict, rma_completed_at, collected_by)
        VALUES
            (%s, 'GPU_XID', 'NO_FAULT_FOUND', NOW(), 'integration-test')
        RETURNING id
        """,
        (target_id,),
    )[0]

    return {
        "rule_id": rule_id,
        "collector_name": collector_name,
        "finding_id": finding_id,
        "case_id": case_id,
        "target_id": target_id,
    }


def _seed_positive_guard_finding_and_case(connect, ids):
    target_id = "node-positive-guard"
    finding_id = _fetch_one(
        connect,
        """
        INSERT INTO patrol_findings
            (rule_id, target_id, target_type, severity, action, action_params,
             evidence, confidence, verdict, collector_snapshot_id, raw_evidence_hash)
        VALUES
            (%s, %s, 'node', 'critical', 'alert', '{}'::jsonb,
             '{"hard_failure": true}'::jsonb, 0.95, 'confirmed', 'snap-positive', 'hash-positive')
        RETURNING finding_id
        """,
        (ids["rule_id"], target_id),
    )[0]

    case_id = _fetch_one(
        connect,
        """
        INSERT INTO case_memory
            (hostname, our_classification, vendor_verdict, rma_completed_at, collected_by)
        VALUES
            (%s, 'GPU_XID', 'REPAIR_CONFIRMED', NOW(), 'integration-test')
        RETURNING id
        """,
        (target_id,),
    )[0]

    return {
        **ids,
        "finding_id": finding_id,
        "case_id": case_id,
        "target_id": target_id,
    }


def test_rma_nff_detection_feedback_optimizes_rule(feedback_workflow_db, monkeypatch):
    from agent_feedback import knowledge_db
    from patrol_cron import db as patrol_db
    from patrol_cron import mcp_tools
    from patrol_cron.models import CollectionResult, TargetData

    ids = _seed_collector_rule_finding_and_case(feedback_workflow_db)

    # Simulate analyze-rma-cases: link vendor outcome back to the finding.
    reconciled = json.loads(mcp_tools.reconcile_finding(ids["finding_id"], "NO_FAULT_FOUND"))
    assert reconciled["status"] == "reconciled"
    assert reconciled["verdict"] == "rejected_nff"

    assert knowledge_db.insert_rma_finding_reconciliation(
        case_id=ids["case_id"],
        finding_id=ids["finding_id"],
        rule_id=ids["rule_id"],
        repair_outcome="NO_FAULT_FOUND",
    )

    # Simulate case-diagnosis: attribute the NFF feedback to detection.
    assert knowledge_db.update_rma_finding_attribution(
        case_id=ids["case_id"],
        finding_id=ids["finding_id"],
        attribution="detection",
        attribution_confidence="high",
        fix_route="rule_code",
    )

    positive_ids = _seed_positive_guard_finding_and_case(feedback_workflow_db, ids)
    positive_reconciled = json.loads(mcp_tools.reconcile_finding(
        positive_ids["finding_id"],
        "REPAIR_CONFIRMED",
    ))
    assert positive_reconciled["status"] == "reconciled"
    assert knowledge_db.insert_rma_finding_reconciliation(
        case_id=positive_ids["case_id"],
        finding_id=positive_ids["finding_id"],
        rule_id=positive_ids["rule_id"],
        repair_outcome="REPAIR_CONFIRMED",
    )
    assert knowledge_db.update_rma_finding_attribution(
        case_id=positive_ids["case_id"],
        finding_id=positive_ids["finding_id"],
        attribution="detection",
        attribution_confidence="high",
        fix_route="rule_code",
    )

    replay_created = json.loads(mcp_tools.create_rule_replay_cases_from_feedback(
        ids["rule_id"],
        created_by="integration-test",
    ))
    assert replay_created["created"] == 2
    assert replay_created["positive"] == 1
    assert replay_created["negative"] == 1

    _execute(
        feedback_workflow_db,
        """
        INSERT INTO rule_reconciliation_state
            (rule_id, reconciliation_dirty, updated_at)
        VALUES (%s, TRUE, NOW())
        ON CONFLICT (rule_id) DO UPDATE SET
            reconciliation_dirty = TRUE,
            updated_at = NOW()
        """,
        (ids["rule_id"],),
    )

    feedback = patrol_db.get_rule_bad_feedback_rate(ids["rule_id"])
    assert feedback["rma_total"] == 2
    assert feedback["bad_count"] == 1
    assert feedback["bad_feedback_rate"] == 0.5

    dirty_rules = patrol_db.list_dirty_reconciliation_rules()
    assert [row["rule_id"] for row in dirty_rules] == [ids["rule_id"]]

    old_replay = json.loads(mcp_tools.run_rule_replay_suite(ids["rule_id"]))
    assert old_replay["passed"] is False
    assert old_replay["total"] == 2

    def fake_run_collector(_collector_cfg):
        return CollectionResult(
            collector_name=ids["collector_name"],
            targets=[
                TargetData(
                    id=ids["target_id"],
                    type="node",
                    payload={"transient_xid": True},
                )
            ],
        )

    monkeypatch.setattr("patrol_cron.engine.run_collector", fake_run_collector)

    # Simulate automate-detection-pattern: candidate fixed rule is replay/live gated.
    update_result = json.loads(mcp_tools.update_rule_code(
        ids["rule_id"],
        FIXED_ANALYZE_CODE,
        reason="nff_refinement",
    ))
    assert update_result == {
        "status": "updated",
        "rule_id": ids["rule_id"],
        "replay_gated": True,
    }

    rule_code, dirty, baseline_at, attempts = _fetch_one(
        feedback_workflow_db,
        """
        SELECT r.analyze_code,
               s.reconciliation_dirty,
               s.nff_baseline_at,
               s.reconcile_attempts
        FROM patrol_rules r
        JOIN rule_reconciliation_state s ON s.rule_id = r.rule_id
        WHERE r.rule_id = %s
        """,
        (ids["rule_id"],),
    )
    assert rule_code == FIXED_ANALYZE_CODE
    assert dirty is False
    assert baseline_at is not None
    assert attempts == 1

    post_fix_feedback = patrol_db.get_rule_bad_feedback_rate(ids["rule_id"])
    assert post_fix_feedback["rma_total"] == 0
    assert post_fix_feedback["bad_count"] == 0
    assert post_fix_feedback["bad_feedback_rate"] is None
