"""Tests for patrol_cron.db — DB access with mocked psycopg2."""

import json
from datetime import datetime, timezone
import unittest.mock as um
import pytest

# We mock psycopg2.connect at the module level so no real DB is needed.


def _make_mock_conn():
    """Create a mock connection with cursor that supports context manager."""
    mock_conn = um.MagicMock()
    mock_cursor = um.MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = um.MagicMock(return_value=False)
    return mock_conn, mock_cursor


class TestGetEnabledCollectors:
    def test_returns_list_of_dicts(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchall.return_value = [
            {"name": "switch_health", "schedule_sec": 120, "enabled": True},
            {"name": "nvidia_ecc", "schedule_sec": 60, "enabled": True},
        ]

        with um.patch("patrol_cron.db._conn", return_value=mock_conn):
            from patrol_cron.db import get_enabled_collectors
            result = get_enabled_collectors()

        assert len(result) == 2
        assert result[0]["name"] == "switch_health"
        assert result[1]["schedule_sec"] == 60


class TestRuleState:
    def test_load_empty_state(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchone.return_value = None

        with um.patch("patrol_cron.db._conn", return_value=mock_conn):
            from patrol_cron.db import load_rule_state
            state = load_rule_state("rule-1")

        assert state == {}

    def test_load_existing_state(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchone.return_value = {
            "state_data": {"sw1": {"fail_count": 3}}
        }

        with um.patch("patrol_cron.db._conn", return_value=mock_conn):
            from patrol_cron.db import load_rule_state
            state = load_rule_state("rule-1")

        assert state["sw1"]["fail_count"] == 3

    def test_save_state(self):
        mock_conn, mock_cursor = _make_mock_conn()

        with um.patch("patrol_cron.db._conn", return_value=mock_conn):
            from patrol_cron.db import save_rule_state
            save_rule_state("rule-1", {"sw1": {"fail_count": 5}})

        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args[0]
        assert "rule_state" in call_args[0]
        assert call_args[1][0] == "rule-1"
        # Second param should be JSON string
        parsed = json.loads(call_args[1][1])
        assert parsed["sw1"]["fail_count"] == 5


class TestFindingExists:
    def test_no_existing_finding(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchone.return_value = None

        with um.patch("patrol_cron.db._conn", return_value=mock_conn):
            from patrol_cron.db import finding_exists
            assert finding_exists("rule-1", "node-1", "cordon_node") is False

    def test_existing_finding(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchone.return_value = (1,)

        with um.patch("patrol_cron.db._conn", return_value=mock_conn):
            from patrol_cron.db import finding_exists
            assert finding_exists("rule-1", "node-1", "cordon_node") is True


class TestInsertFinding:
    def test_inserts_with_correct_params(self):
        mock_conn, mock_cursor = _make_mock_conn()

        with um.patch("patrol_cron.db._conn", return_value=mock_conn):
            from patrol_cron.db import insert_finding
            insert_finding(
                rule_id="rule-1",
                target_id="node-1",
                target_type="node",
                severity="critical",
                action="cordon_node",
                action_params={"triaged_label": "triaged_hardware"},
                evidence={"ecc_count": 5},
                confidence=0.95,
            )

        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args[0]
        assert "patrol_findings" in call_args[0]
        params = call_args[1]
        assert params[0] == "rule-1"
        assert params[1] == "node-1"
        assert params[2] == "node"
        assert params[3] == "critical"
        assert params[4] == "cordon_node"
        assert params[7] == 0.95


class TestRuleAccuracy:
    def test_accuracy_calculation(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchone.return_value = {
            "total": 10, "confirmed": 8, "rejected": 2, "judged": 10,
        }

        with um.patch("patrol_cron.db._conn", return_value=mock_conn):
            from patrol_cron.db import get_rule_accuracy
            acc = get_rule_accuracy("rule-1", window_days=14)

        assert acc["total"] == 10
        assert acc["confirmed"] == 8
        assert acc["accuracy"] == 0.8

    def test_accuracy_none_when_no_judgments(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchone.return_value = {
            "total": 5, "confirmed": 0, "rejected": 0, "judged": 0,
        }

        with um.patch("patrol_cron.db._conn", return_value=mock_conn):
            from patrol_cron.db import get_rule_accuracy
            acc = get_rule_accuracy("rule-1")

        assert acc["accuracy"] is None


def test_get_rule_bad_feedback_rate_counts_detection_bad_feedback():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = {
        "rma_total": 5,
        "repair_confirmed": 1,
        "config_task": 1,
        "detection_nff": 1,
        "detection_misclassified": 1,
    }

    with um.patch("patrol_cron.db._conn", return_value=mock_conn):
        from patrol_cron.db import get_rule_bad_feedback_rate
        result = get_rule_bad_feedback_rate("rule-1")

    assert result == {
        "rule_id": "rule-1",
        "rma_total": 5,
        "total_count": 5,
        "repair_confirmed": 1,
        "config_task": 1,
        "detection_nff": 1,
        "detection_misclassified": 1,
        "bad_count": 2,
        "bad_feedback_rate": 0.4,
    }
    sql, params = mock_cursor.execute.call_args.args
    assert "JOIN rule_reconciliation_state rrs" in sql
    assert "JOIN rma_finding_reconciliations rfr" in sql
    assert "count(*) AS rma_total" in sql
    assert "rfr.repair_outcome = 'REPAIR_CONFIRMED'" in sql
    assert "rfr.repair_outcome = 'CONFIG_TASK'" in sql
    assert "rfr.repair_outcome = 'NO_FAULT_FOUND'" in sql
    assert "rfr.repair_outcome = 'MISCLASSIFIED'" in sql
    assert "pf.detected_at > COALESCE(rrs.nff_baseline_at, '-infinity'::timestamptz)" in sql
    assert "rfr.reconciled_at > COALESCE" not in sql
    assert "rfr.attribution = 'detection'" in sql
    assert "rfr.attribution_confidence IN ('high', 'medium')" in sql
    assert params == ("rule-1",)


def test_get_rule_bad_feedback_rate_zero_total_has_no_rate():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = {
        "rma_total": 0,
        "repair_confirmed": 0,
        "config_task": 0,
        "detection_nff": 0,
        "detection_misclassified": 0,
    }

    with um.patch("patrol_cron.db._conn", return_value=mock_conn):
        from patrol_cron.db import get_rule_bad_feedback_rate
        result = get_rule_bad_feedback_rate("rule-empty")

    assert result["rule_id"] == "rule-empty"
    assert result["rma_total"] == 0
    assert result["total_count"] == 0
    assert result["bad_count"] == 0
    assert result["bad_feedback_rate"] is None


def test_list_dirty_reconciliation_rules_clamps_limit_and_serializes_updated_at():
    mock_conn, mock_cursor = _make_mock_conn()
    updated_at = datetime(2026, 6, 3, 12, 34, 56, tzinfo=timezone.utc)
    mock_cursor.fetchall.return_value = [
        {"rule_id": "rule-1", "reconciliation_dirty": True, "updated_at": updated_at},
    ]

    with um.patch("patrol_cron.db._conn", return_value=mock_conn):
        from patrol_cron.db import list_dirty_reconciliation_rules
        result = list_dirty_reconciliation_rules(limit=999)

    assert result == [
        {
            "rule_id": "rule-1",
            "reconciliation_dirty": True,
            "updated_at": str(updated_at),
        },
    ]
    sql, params = mock_cursor.execute.call_args.args
    assert "FROM rule_reconciliation_state" in sql
    assert "WHERE reconciliation_dirty = TRUE" in sql
    assert "ORDER BY updated_at ASC" in sql
    assert params == (500,)


def test_reconciliation_state_queries_use_shared_bare_table_when_role_routed():
    rate_conn, rate_cursor = _make_mock_conn()
    rate_cursor.fetchone.return_value = {
        "rma_total": 0,
        "repair_confirmed": 0,
        "config_task": 0,
        "detection_nff": 0,
        "detection_misclassified": 0,
    }
    dirty_conn, dirty_cursor = _make_mock_conn()
    dirty_cursor.fetchall.return_value = []

    with um.patch("patrol_cron.db.ROLE", "job_detection"):
        with um.patch("patrol_cron.db._conn", return_value=rate_conn):
            from patrol_cron.db import get_rule_bad_feedback_rate
            get_rule_bad_feedback_rate("rule-1")

        with um.patch("patrol_cron.db._conn", return_value=dirty_conn):
            from patrol_cron.db import list_dirty_reconciliation_rules
            list_dirty_reconciliation_rules()

    rate_sql, _ = rate_cursor.execute.call_args.args
    dirty_sql, _ = dirty_cursor.execute.call_args.args
    assert "job_detection_patrol_findings pf" in rate_sql
    assert "rule_reconciliation_state rrs" in rate_sql
    assert "job_detection_rule_reconciliation_state" not in rate_sql
    assert "FROM rule_reconciliation_state" in dirty_sql
    assert "job_detection_rule_reconciliation_state" not in dirty_sql


@pytest.mark.parametrize("limit", [None, 0, -10, "bad"])
def test_list_dirty_reconciliation_rules_defaults_invalid_limit(limit):
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchall.return_value = []

    with um.patch("patrol_cron.db._conn", return_value=mock_conn):
        from patrol_cron.db import list_dirty_reconciliation_rules
        assert list_dirty_reconciliation_rules(limit=limit) == []

    _, params = mock_cursor.execute.call_args.args
    assert params == (50,)


def test_get_lifecycle_state_returns_none_when_absent():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = None

    with um.patch("patrol_cron.db._conn", return_value=mock_conn):
        from patrol_cron.db import get_lifecycle_state
        assert get_lifecycle_state("r1", "n1", "sig", "alert") is None

    sql, params = mock_cursor.execute.call_args.args
    assert "detection_lifecycle_state" in sql
    assert params == ("r1", "n1", "sig", "alert")


def test_upsert_lifecycle_state_persists_sparse_row():
    mock_conn, mock_cursor = _make_mock_conn()

    row = {
        "rule_id": "r1",
        "target_id": "n1",
        "signal_key": "sig",
        "action": "alert",
        "bad_count": 2,
        "healthy_count": 0,
        "active_finding_id": None,
        "last_status": "bad",
        "first_seen_at": None,
        "last_seen_at": None,
        "expires_at": None,
    }

    with um.patch("patrol_cron.db._conn", return_value=mock_conn):
        from patrol_cron.db import upsert_lifecycle_state
        upsert_lifecycle_state(row)

    sql, params = mock_cursor.execute.call_args.args
    assert "INSERT INTO detection_lifecycle_state" in sql
    assert "ON CONFLICT" in sql
    assert params[0:4] == ("r1", "n1", "sig", "alert")


def test_find_active_finding_ignores_signal_key_for_node_issue_identity():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = {"finding_id": 42}

    with um.patch("patrol_cron.db._conn", return_value=mock_conn):
        from patrol_cron.db import find_active_finding
        assert find_active_finding("r1", "n1", "sig", "alert")["finding_id"] == 42

    sql, params = mock_cursor.execute.call_args.args
    assert "signal_key = %s" not in sql
    assert "active" in sql
    assert params == ("r1", "n1", "alert")


def test_has_active_lifecycle_signals_excludes_recovered_signal():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (1,)

    with um.patch("patrol_cron.db._conn", return_value=mock_conn):
        from patrol_cron.db import has_active_lifecycle_signals
        assert has_active_lifecycle_signals(
            "r1", "n1", "alert", 42, exclude_signal_key="sig-a"
        ) is True

    sql, params = mock_cursor.execute.call_args.args
    assert "detection_lifecycle_state" in sql
    assert "signal_key <> %s" in sql
    assert params == ("r1", "n1", "alert", 42, "sig-a")


def test_refresh_active_finding_updates_last_seen_and_seen_count():
    mock_conn, mock_cursor = _make_mock_conn()

    with um.patch("patrol_cron.db._conn", return_value=mock_conn):
        from patrol_cron.db import refresh_active_finding
        refresh_active_finding(42, {"latest": True}, confidence=0.9)

    sql, params = mock_cursor.execute.call_args.args
    assert "last_seen_at = NOW()" in sql
    assert "seen_count = COALESCE(seen_count, 1) + 1" in sql
    assert params[1] == 0.9
    assert params[2] == 42


def test_delete_expired_lifecycle_state_keeps_active_rows():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.rowcount = 7

    with um.patch("patrol_cron.db._conn", return_value=mock_conn):
        from patrol_cron.db import delete_expired_lifecycle_state
        assert delete_expired_lifecycle_state() == 7

    sql = mock_cursor.execute.call_args.args[0]
    assert "DELETE FROM detection_lifecycle_state" in sql
    assert "active_finding_id IS NULL" in sql
    assert "expires_at < NOW()" in sql
