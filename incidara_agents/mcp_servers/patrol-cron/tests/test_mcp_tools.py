"""Tests for patrol_cron MCP tools — rule CRUD, collector CRUD, accuracy queries.

All DB calls are mocked. Tests verify tool logic and return format.
"""

import json
import unittest.mock as um
import pytest


class TestCreateCollector:
    def test_creates_collector(self):
        from patrol_cron.mcp_tools import create_collector

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor

            result = create_collector(
                name="switch_health",
                schedule_sec=120,
                target_type="switch",
                sources=[{"type": "ssh", "name": "check", "config": {"collector_variant": "switch"}}],
                created_by="agent",
            )

        data = json.loads(result)
        assert data["status"] == "created"
        assert data["name"] == "switch_health"
        mock_cursor.execute.assert_called_once()

    def test_duplicate_collector_returns_error(self):
        from patrol_cron.mcp_tools import create_collector
        import psycopg2

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.execute.side_effect = psycopg2.errors.UniqueViolation("duplicate")

            with pytest.raises(RuntimeError, match="duplicate"):
                create_collector(
                    name="switch_health",
                    schedule_sec=120,
                    target_type="switch",
                    sources=[],
                    created_by="agent",
                )


class TestCreateRule:
    def test_creates_rule(self):
        from patrol_cron.mcp_tools import create_rule

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor

            result = create_rule(
                rule_id="switch_health_v1",
                name="Switch Health",
                binds_to="switch_health",
                analyze_code="def analyze(c, s): return [], s",
                created_by="agent",
            )

        data = json.loads(result)
        assert data["status"] == "created"
        assert data["rule_id"] == "switch_health_v1"


class TestUpdateRuleStage:
    def test_promotes_rule(self):
        from patrol_cron.mcp_tools import update_rule_stage

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.rowcount = 1

            result = update_rule_stage("rule-1", "submit_alert")

        data = json.loads(result)
        assert data["status"] == "updated"
        assert data["new_stage"] == "submit_alert"

    def test_invalid_stage_rejected(self):
        from patrol_cron.mcp_tools import update_rule_stage

        with pytest.raises(ValueError, match="Invalid stage"):
            update_rule_stage("rule-1", "invalid_stage")

    def test_uses_role_routed_patrol_rules_table(self):
        from patrol_cron.mcp_tools import update_rule_stage

        with (
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
            um.patch("patrol_cron.mcp_tools._t", return_value="job_detection_patrol_rules"),
        ):
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.rowcount = 1

            result = update_rule_stage("rule-1", "log_only")

        data = json.loads(result)
        assert data["status"] == "updated"
        update_sql, _ = mock_cursor.execute.call_args.args
        assert "UPDATE job_detection_patrol_rules" in update_sql
        assert "patrol_rules" not in update_sql.replace("job_detection_patrol_rules", "")


class TestReplayGatedUpdateRuleCode:
    def test_nff_refinement_does_not_update_when_replay_fails(self):
        from patrol_cron.mcp_tools import update_rule_code

        with (
            um.patch("patrol_cron.mcp_tools.run_rule_replay_suite") as replay,
            um.patch("patrol_cron.mcp_tools.test_rule_once") as live,
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
        ):
            replay.return_value = json.dumps({"passed": False, "failures": ["expected no finding"]})
            live.return_value = json.dumps({"passed": True})

            result = update_rule_code(
                "rule-1",
                "def analyze(collected, state): return [], state",
                reason="nff_refinement",
            )

        data = json.loads(result)
        assert data == {
            "status": "rejected",
            "reason": "replay_failed",
            "details": ["expected no finding"],
        }
        assert not mock_conn_fn.called
        live.assert_not_called()

    def test_nff_refinement_updates_after_replay_and_live_pass(self):
        from patrol_cron.mcp_tools import update_rule_code

        with (
            um.patch("patrol_cron.mcp_tools.run_rule_replay_suite") as replay,
            um.patch("patrol_cron.mcp_tools._replay_coverage_counts") as coverage,
            um.patch("patrol_cron.mcp_tools.test_rule_once") as live,
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
        ):
            replay.return_value = json.dumps({"passed": True, "failures": []})
            coverage.return_value = {"positive": 1, "negative": 1}
            live.return_value = json.dumps({"passed": True})
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.rowcount = 1

            result = update_rule_code(
                "rule-1",
                "def analyze(collected, state): return [], state",
                reason="nff_refinement",
            )

        data = json.loads(result)
        assert data["status"] == "updated"
        assert data["rule_id"] == "rule-1"
        assert data["replay_gated"] is True
        assert mock_cursor.execute.call_count == 3
        archive_sql, archive_params = mock_cursor.execute.call_args_list[0].args
        update_sql, update_params = mock_cursor.execute.call_args_list[1].args
        state_sql, state_params = mock_cursor.execute.call_args_list[2].args
        assert "INSERT INTO patrol_rule_versions" in archive_sql
        assert "UPDATE patrol_rules" in update_sql
        assert update_params == ("def analyze(collected, state): return [], state", "rule-1")
        assert "INSERT INTO rule_reconciliation_state" in state_sql
        assert "reconciliation_dirty = FALSE" in state_sql
        assert "reconcile_attempts = rule_reconciliation_state.reconcile_attempts + 1" in state_sql
        assert state_params == ("rule-1",)
        replay.assert_called_once_with(
            "rule-1",
            analyze_code="def analyze(collected, state): return [], state",
        )
        coverage.assert_called_once_with("rule-1")
        live.assert_called_once_with(
            "rule-1",
            dry_run=True,
            analyze_code="def analyze(collected, state): return [], state",
            sample=5,
        )
        mock_conn.commit.assert_called_once()

    def test_free_form_rma_refinement_reason_is_rejected_without_writing(self):
        from patrol_cron.mcp_tools import update_rule_code

        with (
            um.patch("patrol_cron.mcp_tools.run_rule_replay_suite") as replay,
            um.patch("patrol_cron.mcp_tools.test_rule_once") as live,
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
        ):
            result = update_rule_code(
                "rule-1",
                "def analyze(collected, state): return [], state",
                reason="NFF/MISCLASSIFIED refinement: suppress transient XID false positives",
            )

        data = json.loads(result)
        assert data["status"] == "rejected"
        assert data["reason"] == "invalid_refinement_reason"
        assert data["details"]["reason"] == "NFF/MISCLASSIFIED refinement: suppress transient XID false positives"
        assert set(data["details"]["allowed"]) == {"nff_refinement", "misclassified_refinement"}
        replay.assert_not_called()
        live.assert_not_called()
        assert not mock_conn_fn.called

    def test_misclassified_refinement_success_is_replay_gated(self):
        from patrol_cron.mcp_tools import update_rule_code

        with (
            um.patch("patrol_cron.mcp_tools.run_rule_replay_suite", return_value=json.dumps({"passed": True})),
            um.patch("patrol_cron.mcp_tools._replay_coverage_counts", return_value={"positive": 1, "negative": 1}),
            um.patch("patrol_cron.mcp_tools.test_rule_once", return_value=json.dumps({"passed": True})),
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
        ):
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.rowcount = 1

            result = update_rule_code(
                "rule-1",
                "def analyze(collected, state): return [], state",
                reason="misclassified_refinement",
            )

        data = json.loads(result)
        assert data["status"] == "updated"
        assert data["replay_gated"] is True
        assert mock_cursor.execute.call_count == 3

    def test_refinement_requires_positive_and_negative_replay_coverage(self):
        from patrol_cron.mcp_tools import update_rule_code

        with (
            um.patch(
                "patrol_cron.mcp_tools.run_rule_replay_suite",
                return_value=json.dumps({"passed": True, "failures": []}),
            ),
            um.patch(
                "patrol_cron.mcp_tools._replay_coverage_counts",
                return_value={"positive": 0, "negative": 1},
            ),
            um.patch("patrol_cron.mcp_tools.test_rule_once") as live,
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
        ):
            result = update_rule_code(
                "rule-1",
                "def analyze(collected, state): return [], state",
                reason="nff_refinement",
            )

        data = json.loads(result)
        assert data == {
            "status": "rejected",
            "reason": "insufficient_replay_coverage",
            "details": {"positive": 0, "negative": 1},
        }
        live.assert_not_called()
        assert not mock_conn_fn.called

    def test_refinement_does_not_update_when_live_test_errors(self):
        from patrol_cron.mcp_tools import update_rule_code

        with (
            um.patch("patrol_cron.mcp_tools.run_rule_replay_suite") as replay,
            um.patch("patrol_cron.mcp_tools._replay_coverage_counts", return_value={"positive": 1, "negative": 1}),
            um.patch("patrol_cron.mcp_tools.test_rule_once") as live,
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
        ):
            replay.return_value = json.dumps({"passed": True, "failures": []})
            live.return_value = json.dumps({"error": "collector failed"})

            result = update_rule_code(
                "rule-1",
                "def analyze(collected, state): return [], state",
                reason="misclassified_refinement",
            )

        data = json.loads(result)
        assert data == {
            "status": "rejected",
            "reason": "live_test_failed",
            "details": {"error": "collector failed"},
        }
        assert not mock_conn_fn.called

    def test_replay_error_details_are_preserved(self):
        from patrol_cron.mcp_tools import update_rule_code

        with (
            um.patch(
                "patrol_cron.mcp_tools.run_rule_replay_suite",
                return_value=json.dumps({"passed": False, "error": "No replay cases found"}),
            ),
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
        ):
            result = update_rule_code(
                "rule-1",
                "def analyze(collected, state): return [], state",
                reason="nff_refinement",
            )

        data = json.loads(result)
        assert data == {
            "status": "rejected",
            "reason": "replay_failed",
            "details": {"error": "No replay cases found"},
        }
        assert not mock_conn_fn.called

    def test_malformed_replay_result_returns_json_error(self):
        from patrol_cron.mcp_tools import update_rule_code

        with (
            um.patch("patrol_cron.mcp_tools.run_rule_replay_suite", return_value="not json"),
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
        ):
            result = update_rule_code(
                "rule-1",
                "def analyze(collected, state): return [], state",
                reason="nff_refinement",
            )

        data = json.loads(result)
        assert data["status"] == "rejected"
        assert data["reason"] == "replay_failed"
        assert "Invalid replay result JSON" in data["details"]["error"]
        assert not mock_conn_fn.called

    def test_malformed_live_result_returns_json_error(self):
        from patrol_cron.mcp_tools import update_rule_code

        with (
            um.patch("patrol_cron.mcp_tools.run_rule_replay_suite", return_value=json.dumps({"passed": True})),
            um.patch("patrol_cron.mcp_tools._replay_coverage_counts", return_value={"positive": 1, "negative": 1}),
            um.patch("patrol_cron.mcp_tools.test_rule_once", return_value="not json"),
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
        ):
            result = update_rule_code(
                "rule-1",
                "def analyze(collected, state): return [], state",
                reason="nff_refinement",
            )

        data = json.loads(result)
        assert data["status"] == "rejected"
        assert data["reason"] == "live_test_failed"
        assert "Invalid live test result JSON" in data["details"]["error"]
        assert not mock_conn_fn.called

    def test_shadow_mode_demotes_stage_and_keeps_dirty(self):
        from patrol_cron.mcp_tools import update_rule_code

        with (
            um.patch("patrol_cron.mcp_tools.update_rule_stage") as update_stage,
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
        ):
            update_stage.return_value = json.dumps({"status": "updated", "new_stage": "log_only"})

            result = update_rule_code(
                "rule-1",
                "def analyze(collected, state): return [], state",
                reason="nff_refinement",
                mode="shadow_mode",
            )

        data = json.loads(result)
        assert data == {"status": "contained", "rule_id": "rule-1", "mode": "shadow_mode", "dirty": True}
        update_stage.assert_called_once_with("rule-1", "log_only")
        assert not mock_conn_fn.called

    def test_safe_mode_demotes_stage_and_keeps_dirty(self):
        from patrol_cron.mcp_tools import update_rule_code

        with (
            um.patch("patrol_cron.mcp_tools.update_rule_stage") as update_stage,
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
        ):
            update_stage.return_value = json.dumps({"status": "updated", "new_stage": "log_only"})

            result = update_rule_code(
                "rule-1",
                "def analyze(collected, state): return [], state",
                reason="nff_refinement",
                mode="safe_mode",
            )

        data = json.loads(result)
        assert data == {"status": "contained", "rule_id": "rule-1", "mode": "safe_mode", "dirty": True}
        update_stage.assert_called_once_with("rule-1", "log_only")
        assert not mock_conn_fn.called

    def test_containment_malformed_stage_result_returns_error(self):
        from patrol_cron.mcp_tools import update_rule_code

        with um.patch("patrol_cron.mcp_tools.update_rule_stage", return_value="not json"):
            with pytest.raises(ValueError, match="Invalid containment result JSON"):
                update_rule_code(
                    "rule-1",
                    "def analyze(collected, state): return [], state",
                    mode="safe_mode",
                )

    def test_invalid_mode_returns_error_without_writing(self):
        from patrol_cron.mcp_tools import update_rule_code

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            with pytest.raises(ValueError, match="Invalid mode: finallize"):
                update_rule_code(
                    "rule-1",
                    "def analyze(collected, state): return [], state",
                    mode="finallize",
                )

        assert not mock_conn_fn.called

    def test_backwards_compatible_update_without_reason_skips_replay_gate(self):
        from patrol_cron.mcp_tools import update_rule_code

        with (
            um.patch("patrol_cron.mcp_tools.run_rule_replay_suite") as replay,
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
        ):
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.rowcount = 1

            result = update_rule_code("rule-1", "def analyze(collected, state): return [], state")

        data = json.loads(result)
        assert data == {"status": "updated", "rule_id": "rule-1"}
        assert mock_cursor.execute.call_count == 2  # archive + update
        replay.assert_not_called()

    def test_update_rule_code_uses_role_routed_patrol_rules(self):
        from patrol_cron.mcp_tools import update_rule_code

        with (
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
            um.patch("patrol_cron.mcp_tools._t", return_value="job_detection_patrol_rules"),
        ):
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.rowcount = 1

            result = update_rule_code("rule-1", "def analyze(collected, state): return [], state")

        data = json.loads(result)
        assert data["status"] == "updated"
        update_sql, _ = mock_cursor.execute.call_args.args
        assert "UPDATE job_detection_patrol_rules" in update_sql
        assert "patrol_rules" not in update_sql.replace("job_detection_patrol_rules", "")


class TestListRules:
    def test_lists_rules(self):
        from patrol_cron.mcp_tools import list_rules

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchall.return_value = [
                {"rule_id": "r1", "name": "Rule 1", "binds_to": "c1",
                 "stage": "create_task", "enabled": True,
                 "created_at": "2025-01-01", "updated_at": "2025-01-01"},
            ]

            result = list_rules()

        data = json.loads(result)
        assert len(data["rules"]) == 1
        assert data["rules"][0]["rule_id"] == "r1"


class TestGetRuleAccuracy:
    def test_returns_accuracy(self):
        from patrol_cron.mcp_tools import get_rule_accuracy

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "total": 20, "confirmed": 16, "rejected": 4, "judged": 20,
            }

            result = get_rule_accuracy("rule-1", window_days=14)

        data = json.loads(result)
        assert data["accuracy"] == 0.8
        assert data["total"] == 20


class TestRuleBadFeedbackRate:
    def test_returns_helper_result(self):
        from patrol_cron.mcp_tools import get_rule_bad_feedback_rate

        helper_result = {
            "rule_id": "rule-1",
            "rma_total": 5,
            "repair_confirmed": 1,
            "config_task": 1,
            "detection_nff": 1,
            "detection_misclassified": 1,
            "bad_count": 2,
            "bad_feedback_rate": 0.4,
        }
        with um.patch("patrol_cron.db.get_rule_bad_feedback_rate", return_value=helper_result):
            result = get_rule_bad_feedback_rate("rule-1")

        assert json.loads(result) == helper_result

    def test_returns_error_json(self):
        from patrol_cron.mcp_tools import get_rule_bad_feedback_rate

        with um.patch("patrol_cron.db.get_rule_bad_feedback_rate", side_effect=RuntimeError("db down")):
            with pytest.raises(RuntimeError, match="db down"):
                get_rule_bad_feedback_rate("rule-1")


class TestListDirtyReconciliationRules:
    def test_returns_helper_result(self):
        from patrol_cron.mcp_tools import list_dirty_reconciliation_rules

        helper_result = [
            {"rule_id": "rule-1", "reconciliation_dirty": True, "updated_at": "2026-06-03"},
        ]
        with um.patch("patrol_cron.db.list_dirty_reconciliation_rules", return_value=helper_result):
            result = list_dirty_reconciliation_rules(limit=10)

        assert json.loads(result) == {"rules": helper_result}

    def test_returns_error_json(self):
        from patrol_cron.mcp_tools import list_dirty_reconciliation_rules

        with um.patch("patrol_cron.db.list_dirty_reconciliation_rules", side_effect=RuntimeError("db down")):
            with pytest.raises(RuntimeError, match="db down"):
                list_dirty_reconciliation_rules(limit=10)


class TestListFindings:
    def test_lists_open_findings(self):
        from patrol_cron.mcp_tools import list_findings

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchall.return_value = [
                {"finding_id": 1, "rule_id": "r1", "target_id": "n1",
                 "severity": "critical", "action": "cordon_node",
                 "verdict": None, "resolved": False,
                 "detected_at": "2025-01-01T00:00:00Z"},
            ]

            result = list_findings(rule_id="r1", resolved=False)

        data = json.loads(result)
        assert len(data["findings"]) == 1


class TestRecordVerdict:
    def test_records_verdict(self):
        from patrol_cron.mcp_tools import record_verdict

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.rowcount = 1

            result = record_verdict(finding_id=1, verdict="confirmed")

        data = json.loads(result)
        assert data["status"] == "updated"

    def test_invalid_verdict_rejected(self):
        from patrol_cron.mcp_tools import record_verdict

        with pytest.raises(ValueError, match="Invalid verdict"):
            record_verdict(finding_id=1, verdict="maybe")


class TestRecordVerdictNff:
    def test_records_rejected_nff_verdict(self):
        from patrol_cron.mcp_tools import record_verdict

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.rowcount = 1

            result = record_verdict(finding_id=1, verdict="rejected_nff")

        data = json.loads(result)
        assert data["status"] == "updated"
        assert data["verdict"] == "rejected_nff"


class TestReconcileFinding:
    def test_nff_sets_repair_outcome_rejected_nff_and_resolved(self):
        from patrol_cron.mcp_tools import reconcile_finding

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "finding_id": 1,
                "repair_outcome": "NO_FAULT_FOUND",
                "verdict": "rejected_nff",
                "resolved": True,
            }

            result = reconcile_finding(1, "NO_FAULT_FOUND")

        data = json.loads(result)
        assert data == {
            "status": "reconciled",
            "finding_id": 1,
            "repair_outcome": "NO_FAULT_FOUND",
            "verdict": "rejected_nff",
            "resolved": True,
        }
        assert mock_cursor.execute.call_count == 1
        update_sql, update_params = mock_cursor.execute.call_args.args
        assert "repair_outcome = %s" in update_sql
        assert "repair_outcome_at = NOW()" in update_sql
        assert "verdict = %s" in update_sql
        assert "resolved = TRUE" in update_sql
        assert "repair_outcome IS NULL" in update_sql
        assert "reconciliation_dirty" not in update_sql
        assert "attribution" not in update_sql
        assert update_params == ("NO_FAULT_FOUND", "rejected_nff", 1)
        mock_conn.commit.assert_called_once()

    def test_existing_repair_outcome_is_idempotent(self):
        from patrol_cron.mcp_tools import reconcile_finding

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.side_effect = [
                None,
                {
                    "finding_id": 1,
                    "repair_outcome": "REPAIR_CONFIRMED",
                    "verdict": "confirmed",
                    "resolved": True,
                },
            ]

            result = reconcile_finding(1, "NO_FAULT_FOUND")

        data = json.loads(result)
        assert data == {
            "status": "already_reconciled",
            "finding_id": 1,
            "repair_outcome": "REPAIR_CONFIRMED",
            "verdict": "confirmed",
            "resolved": True,
        }
        assert mock_cursor.execute.call_count == 2
        update_sql, update_params = mock_cursor.execute.call_args_list[0].args
        select_sql, select_params = mock_cursor.execute.call_args_list[1].args
        assert "UPDATE" in update_sql
        assert "repair_outcome IS NULL" in update_sql
        assert "SELECT finding_id, repair_outcome, verdict, resolved" in select_sql
        assert update_params == ("NO_FAULT_FOUND", "rejected_nff", 1)
        assert select_params == (1,)
        mock_conn.commit.assert_not_called()

    def test_invalid_repair_outcome_returns_error(self):
        from patrol_cron.mcp_tools import reconcile_finding

        result = reconcile_finding(1, "UNKNOWN")
        data = json.loads(result)
        assert "error" in data
        assert "Invalid repair_outcome" in data["error"]

    def test_misclassified_sets_repair_outcome_without_verdict_and_unresolved(self):
        from patrol_cron.mcp_tools import reconcile_finding

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "finding_id": 2,
                "repair_outcome": "MISCLASSIFIED",
                "verdict": None,
                "resolved": False,
            }

            result = reconcile_finding(2, "MISCLASSIFIED")

        data = json.loads(result)
        assert data == {
            "status": "reconciled",
            "finding_id": 2,
            "repair_outcome": "MISCLASSIFIED",
            "verdict": None,
            "resolved": False,
        }
        update_sql, update_params = mock_cursor.execute.call_args.args
        assert "repair_outcome = %s" in update_sql
        assert "repair_outcome_at = NOW()" in update_sql
        assert "verdict =" not in update_sql
        assert "resolved = FALSE" in update_sql
        assert "repair_outcome IS NULL" in update_sql
        assert update_params == ("MISCLASSIFIED", 2)
        mock_conn.commit.assert_called_once()

    def test_misclassified_returns_existing_verdict_from_persisted_row(self):
        from patrol_cron.mcp_tools import reconcile_finding

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "finding_id": 5,
                "repair_outcome": "MISCLASSIFIED",
                "verdict": "confirmed",
                "resolved": False,
            }

            result = reconcile_finding(5, "MISCLASSIFIED")

        data = json.loads(result)
        assert data == {
            "status": "reconciled",
            "finding_id": 5,
            "repair_outcome": "MISCLASSIFIED",
            "verdict": "confirmed",
            "resolved": False,
        }
        update_sql, _ = mock_cursor.execute.call_args.args
        assert "verdict =" not in update_sql
        assert "RETURNING finding_id, repair_outcome, verdict, resolved" in update_sql
        mock_conn.commit.assert_called_once()

    @pytest.mark.parametrize("repair_outcome", ["MAINTENANCE_FIX", "CONFIG_TASK"])
    def test_resolved_repair_outcomes_return_persisted_shape(self, repair_outcome):
        from patrol_cron.mcp_tools import reconcile_finding

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "finding_id": 6,
                "repair_outcome": repair_outcome,
                "verdict": "confirmed",
                "resolved": True,
            }

            result = reconcile_finding(6, repair_outcome)

        data = json.loads(result)
        assert data == {
            "status": "reconciled",
            "finding_id": 6,
            "repair_outcome": repair_outcome,
            "verdict": "confirmed",
            "resolved": True,
        }
        update_sql, _ = mock_cursor.execute.call_args.args
        assert "RETURNING finding_id, repair_outcome, verdict, resolved" in update_sql
        mock_conn.commit.assert_called_once()

    def test_finding_not_found_returns_error(self):
        from patrol_cron.mcp_tools import reconcile_finding

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.side_effect = [None, None]

            result = reconcile_finding(404, "REPAIR_CONFIRMED")

        data = json.loads(result)
        assert data["ok"] is False
        assert data["reason"] == "not_found"
        assert data["entity"] == "Finding"
        assert data["id"] == 404
        assert mock_cursor.execute.call_count == 2
        mock_conn.commit.assert_not_called()

    def test_uses_role_routed_table_name(self):
        from patrol_cron.mcp_tools import reconcile_finding

        with (
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
            um.patch("patrol_cron.mcp_tools._t", return_value="job_detection_patrol_findings"),
        ):
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "finding_id": 3,
                "repair_outcome": "REPAIR_CONFIRMED",
                "verdict": "confirmed",
                "resolved": True,
            }

            result = reconcile_finding(3, "REPAIR_CONFIRMED")

        data = json.loads(result)
        assert data["status"] == "reconciled"
        update_sql, _ = mock_cursor.execute.call_args.args
        assert "UPDATE job_detection_patrol_findings" in update_sql
        assert "patrol_findings" not in update_sql.replace("job_detection_patrol_findings", "")


class TestCreateFinding:
    def test_persists_optional_snapshot_and_hash(self):
        from patrol_cron.mcp_tools import create_finding

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            # First fetchone: dedup check (no duplicate) → None
            # Second fetchone: RETURNING finding_id → (42,)
            mock_cursor.fetchone.side_effect = [None, (42,)]

            result = create_finding(
                rule_id="manual",
                target_id="node-1",
                target_type="node",
                severity="warning",
                action="alert",
                evidence={"detail": "manual evidence"},
                collector_snapshot_id="snapshot-123",
                raw_evidence_hash="hash-abc",
            )

        data = json.loads(result)
        assert data["status"] == "created"
        # Find the INSERT execute (dedup is first, INSERT is second for manual)
        insert_call = None
        for call in mock_cursor.execute.call_args_list:
            sql = call.args[0] if call.args else ""
            if "INSERT" in sql and "patrol_findings" in sql:
                insert_call = call
                break
        assert insert_call is not None
        insert_sql, insert_params = insert_call.args
        assert "collector_snapshot_id" in insert_sql
        assert "raw_evidence_hash" in insert_sql
        # Verify snapshot and hash are in the params
        assert "snapshot-123" in insert_params
        assert "hash-abc" in insert_params

    def test_uses_role_routed_table_name(self):
        from patrol_cron.mcp_tools import create_finding

        with (
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
            um.patch("patrol_cron.mcp_tools._t", return_value="job_detection_patrol_findings"),
        ):
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            # First fetchone: dedup check (no duplicate) → None
            # Second fetchone: RETURNING finding_id → (42,)
            mock_cursor.fetchone.side_effect = [None, (42,)]

            result = create_finding(
                rule_id="manual",
                target_id="node-1",
                target_type="node",
                severity="warning",
                action="alert",
            )

        data = json.loads(result)
        assert data["status"] == "created"
        dedupe_sql, _ = mock_cursor.execute.call_args_list[0].args
        insert_sql, _ = mock_cursor.execute.call_args_list[1].args
        assert "FROM job_detection_patrol_findings" in dedupe_sql
        assert "INSERT INTO job_detection_patrol_findings" in insert_sql
        assert "patrol_findings" not in dedupe_sql.replace("job_detection_patrol_findings", "")
        assert "patrol_findings" not in insert_sql.replace("job_detection_patrol_findings", "")


class TestRuleReplayTools:
    def test_create_rule_replay_case_returns_id(self):
        from patrol_cron.mcp_tools import create_rule_replay_case

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {"replay_case_id": 7}

            result = create_rule_replay_case(
                rule_id="rule-1",
                finding_id=42,
                case_id=1,
                source="rma_bad_feedback",
                frozen_input={"collector_name": "c1", "targets": [], "errors": [], "duration": 0.0},
                expected_behavior={"should_fire": False},
                repair_outcome="NO_FAULT_FOUND",
                attribution="detection",
            )

        data = json.loads(result)
        assert data == {"status": "created", "replay_case_id": 7, "rule_id": "rule-1"}
        insert_sql, insert_params = mock_cursor.execute.call_args.args
        assert "INSERT INTO rule_replay_cases" in insert_sql
        assert insert_params[3] == "rma_bad_feedback"
        assert json.loads(insert_params[10])["collector_name"] == "c1"
        assert json.loads(insert_params[11])["should_fire"] is False

    def test_create_rule_replay_case_uses_role_routed_table_name(self):
        from patrol_cron.mcp_tools import create_rule_replay_case

        with (
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
            um.patch("patrol_cron.mcp_tools._t", return_value="job_detection_rule_replay_cases"),
        ):
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {"replay_case_id": 8}

            result = create_rule_replay_case(
                rule_id="rule-1",
                source="rma_bad_feedback",
                frozen_input={"collector_name": "c1"},
                expected_behavior={"should_fire": False},
            )

        data = json.loads(result)
        assert data["status"] == "created"
        insert_sql, _ = mock_cursor.execute.call_args.args
        assert "INSERT INTO job_detection_rule_replay_cases" in insert_sql
        assert "rule_replay_cases" not in insert_sql.replace("job_detection_rule_replay_cases", "")

    def test_create_rule_replay_case_rejects_invalid_source(self):
        from patrol_cron.mcp_tools import create_rule_replay_case

        with pytest.raises(ValueError, match="Invalid source"):
            create_rule_replay_case(
                rule_id="rule-1",
                source="bad_source",
                frozen_input={"collector_name": "c1"},
                expected_behavior={"should_fire": False},
            )

    def test_create_rule_replay_cases_from_feedback_creates_positive_and_negative_cases(self):
        from patrol_cron.mcp_tools import create_rule_replay_cases_from_feedback

        rows = [
            {
                "case_id": 1,
                "finding_id": 42,
                "rule_id": "rule-1",
                "repair_outcome": "NO_FAULT_FOUND",
                "attribution": "detection",
                "feedback_label": "negative",
                "expected_behavior": {"should_fire": False},
                "target_id": "node-a",
                "target_type": "node",
                "evidence": {"transient_xid": True},
                "collector_snapshot_id": "snap-a",
                "raw_evidence_hash": "hash-a",
                "collector_name": "gpu_health",
            },
            {
                "case_id": 2,
                "finding_id": 43,
                "rule_id": "rule-1",
                "repair_outcome": "REPAIR_CONFIRMED",
                "attribution": "detection",
                "feedback_label": "positive",
                "expected_behavior": {"should_fire": True},
                "target_id": "node-b",
                "target_type": "node",
                "evidence": {"hard_failure": True},
                "collector_snapshot_id": "snap-b",
                "raw_evidence_hash": "hash-b",
                "collector_name": "gpu_health",
            },
        ]

        with (
            um.patch("patrol_cron.mcp_tools._feedback_examples_for_replay", return_value=rows),
            um.patch("patrol_cron.mcp_tools._replay_case_exists", return_value=False),
            um.patch("patrol_cron.mcp_tools.create_rule_replay_case") as create_case,
        ):
            create_case.side_effect = [
                json.dumps({"status": "created", "replay_case_id": 10, "rule_id": "rule-1"}),
                json.dumps({"status": "created", "replay_case_id": 11, "rule_id": "rule-1"}),
            ]

            result = create_rule_replay_cases_from_feedback("rule-1", created_by="test-agent")

        data = json.loads(result)
        assert data == {
            "rule_id": "rule-1",
            "created": 2,
            "skipped": 0,
            "positive": 1,
            "negative": 1,
            "replay_case_ids": [10, 11],
        }
        first = create_case.call_args_list[0].kwargs
        assert first["source"] == "rma_bad_feedback"
        assert first["frozen_input"] == {
            "collector_name": "gpu_health",
            "collector_status": "success",
            "errors": [],
            "duration": 0.0,
            "targets": [
                {
                    "id": "node-a",
                    "type": "node",
                    "payload": {"transient_xid": True},
                    "meta": {},
                }
            ],
        }
        assert first["expected_behavior"] == {"should_fire": False}
        second = create_case.call_args_list[1].kwargs
        assert second["source"] == "confirmed_counterexample"
        assert second["expected_behavior"] == {"should_fire": True}
        assert second["created_by"] == "test-agent"

    def test_list_rule_replay_cases_excludes_counterexamples_when_requested(self):
        from patrol_cron.mcp_tools import list_rule_replay_cases

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchall.return_value = []

            result = list_rule_replay_cases("rule-1", include_counterexamples=False)

        data = json.loads(result)
        assert data == {"rule_id": "rule-1", "replay_cases": []}
        sql, params = mock_cursor.execute.call_args.args
        assert "source = 'rma_bad_feedback'" in sql
        assert params == ("rule-1",)

    def test_list_rule_replay_cases_uses_role_routed_table_name(self):
        from patrol_cron.mcp_tools import list_rule_replay_cases

        with (
            um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn,
            um.patch("patrol_cron.mcp_tools._t", return_value="job_detection_rule_replay_cases"),
        ):
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchall.return_value = []

            result = list_rule_replay_cases("rule-1")

        data = json.loads(result)
        assert data == {"rule_id": "rule-1", "replay_cases": []}
        sql, _ = mock_cursor.execute.call_args.args
        assert "FROM job_detection_rule_replay_cases" in sql
        assert "rule_replay_cases" not in sql.replace("job_detection_rule_replay_cases", "")

    def test_replay_rule_case_passes_when_rule_does_not_fire(self):
        from patrol_cron.mcp_tools import replay_rule_case

        replay_case = {
            "replay_case_id": 7,
            "rule_id": "rule-1",
            "frozen_input": {"collector_name": "c1", "targets": [], "errors": [], "duration": 0.0},
            "expected_behavior": {"should_fire": False},
        }
        rule = {"analyze_code": "def analyze(collected, state): return [], state"}

        with (
            um.patch("patrol_cron.db.get_rule", return_value=rule),
            um.patch("patrol_cron.mcp_tools._get_replay_case", return_value=replay_case),
        ):
            result = replay_rule_case("rule-1", 7)

        data = json.loads(result)
        assert data["passed"] is True
        assert data["findings"] == []
        assert data["failures"] == []

    def test_replay_rule_case_fails_when_rule_fires_for_no_fire_case(self):
        from patrol_cron.mcp_tools import replay_rule_case

        replay_case = {
            "replay_case_id": 7,
            "rule_id": "rule-1",
            "frozen_input": {
                "collector_name": "c1",
                "targets": [{"id": "n1", "type": "node", "payload": {"ok": False}}],
            },
            "expected_behavior": {"should_fire": False},
        }
        rule = {
            "analyze_code": (
                "def analyze(collected, state):\n"
                "    return [Finding(target_id='n1', severity='warning', action='alert')], state"
            )
        }

        with (
            um.patch("patrol_cron.db.get_rule", return_value=rule),
            um.patch("patrol_cron.mcp_tools._get_replay_case", return_value=replay_case),
        ):
            result = replay_rule_case("rule-1", 7)

        data = json.loads(result)
        assert data["passed"] is False
        assert data["findings"][0]["target_id"] == "n1"
        assert "expected no bad observation" in data["failures"][0]

    def test_replay_rule_case_returns_not_found_contract_when_rule_missing(self):
        from patrol_cron.mcp_tools import replay_rule_case

        with um.patch("patrol_cron.db.get_rule", return_value=None):
            result = replay_rule_case("rule-404", 7)

        data = json.loads(result)
        assert data["ok"] is False
        assert data["reason"] == "not_found"
        assert data["entity"] == "Rule"
        assert data["id"] == "rule-404"

    def test_replay_rule_case_with_candidate_code_still_validates_rule_exists(self):
        from patrol_cron.mcp_tools import replay_rule_case

        with um.patch("patrol_cron.db.get_rule", return_value=None) as get_rule:
            result = replay_rule_case(
                "rule-404",
                7,
                analyze_code="def analyze(collected, state): return [], state",
            )

        data = json.loads(result)
        assert data["ok"] is False
        assert data["reason"] == "not_found"
        assert data["entity"] == "Rule"
        assert data["id"] == "rule-404"
        get_rule.assert_called_once_with("rule-404")

    def test_expected_action_and_severity_must_match_same_finding(self):
        from patrol_cron.mcp_tools import replay_rule_case

        replay_case = {
            "replay_case_id": 7,
            "rule_id": "rule-1",
            "frozen_input": {"collector_name": "c1", "targets": []},
            "expected_behavior": {
                "should_fire": True,
                "expected_action": "drain_node",
                "expected_severity": "critical",
            },
        }
        rule = {
            "analyze_code": (
                "def analyze(collected, state):\n"
                "    return [\n"
                "        Finding(target_id='n1', severity='warning', action='drain_node'),\n"
                "        Finding(target_id='n2', severity='critical', action='alert'),\n"
                "    ], state"
            )
        }

        with (
            um.patch("patrol_cron.db.get_rule", return_value=rule),
            um.patch("patrol_cron.mcp_tools._get_replay_case", return_value=replay_case),
        ):
            result = replay_rule_case("rule-1", 7)

        data = json.loads(result)
        assert data["passed"] is False
        assert "expected one observation matching action drain_node and severity critical" in data["failures"]

    def test_run_rule_replay_suite_aggregates_failures(self):
        from patrol_cron.mcp_tools import run_rule_replay_suite

        cases = [
            {
                "replay_case_id": 1,
                "rule_id": "rule-1",
                "frozen_input": {"collector_name": "c1", "targets": []},
                "expected_behavior": {"should_fire": False},
            },
            {
                "replay_case_id": 2,
                "rule_id": "rule-1",
                "frozen_input": {"collector_name": "c1", "targets": []},
                "expected_behavior": {"should_fire": True},
            },
        ]
        rule = {"analyze_code": "def analyze(collected, state): return [], state"}

        with (
            um.patch("patrol_cron.db.get_rule", return_value=rule),
            um.patch("patrol_cron.mcp_tools._get_replay_cases", return_value=cases),
        ):
            result = run_rule_replay_suite("rule-1")

        data = json.loads(result)
        assert data["passed"] is False
        assert data["total"] == 2
        assert len(data["failures"]) == 1
        assert data["failures"][0]["replay_case_id"] == 2

    def test_run_rule_replay_suite_returns_no_pass_when_no_cases(self):
        from patrol_cron.mcp_tools import run_rule_replay_suite

        with (
            um.patch("patrol_cron.db.get_rule", return_value={"analyze_code": "def analyze(c, s): return [], s"}),
            um.patch("patrol_cron.mcp_tools._get_replay_cases", return_value=[]),
        ):
            result = run_rule_replay_suite("rule-1")

        data = json.loads(result)
        assert data["passed"] is False
        assert data["total"] == 0
        assert data["error"] == "No replay cases found for rule 'rule-1'"

    def test_run_rule_replay_suite_with_candidate_code_still_validates_rule_exists(self):
        from patrol_cron.mcp_tools import run_rule_replay_suite

        with um.patch("patrol_cron.db.get_rule", return_value=None) as get_rule:
            result = run_rule_replay_suite(
                "rule-404",
                analyze_code="def analyze(collected, state): return [], state",
            )

        data = json.loads(result)
        assert data["ok"] is False
        assert data["reason"] == "not_found"
        assert data["entity"] == "Rule"
        assert data["id"] == "rule-404"
        get_rule.assert_called_once_with("rule-404")


class TestRuleOnceCandidateCode:
    def test_uses_candidate_analyze_code_for_dry_run(self):
        from patrol_cron.mcp_tools import test_rule_once
        from patrol_cron.models import CollectionResult, TargetData

        db_rule_code = (
            "def analyze(collected, state):\n"
            "    return [Finding(target_id='n1', severity='warning', action='alert')], state"
        )
        candidate_code = "def analyze(collected, state): return [], state"

        with (
            um.patch(
                "patrol_cron.db.get_rule",
                return_value={"binds_to": "c1", "stage": "submit_alert", "analyze_code": db_rule_code},
            ),
            um.patch("patrol_cron.db.get_collector_by_name", return_value={"name": "c1", "target_type": "node"}),
            um.patch("patrol_cron.db.load_rule_state", return_value={}),
            um.patch(
                "patrol_cron.engine.run_collector",
                return_value=CollectionResult(
                    collector_name="c1",
                    targets=[TargetData(id="n1", type="node", payload={"ok": False})],
                ),
            ),
        ):
            result = test_rule_once("rule-1", dry_run=True, analyze_code=candidate_code)

        data = json.loads(result)
        assert data["findings_count"] == 0
        assert data["findings"] == []
