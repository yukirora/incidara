"""Tests for patrol_cron.actions — action engine."""

import unittest.mock as um
import pytest
from patrol_cron.models import Finding
from patrol_cron.actions import execute_finding


class TestLogOnly:
    """log_only stage records to DB without taking any action."""

    def test_log_only_does_not_call_any_action(self):
        f = Finding(target_id="n1", severity="critical", action="cordon_node")
        with um.patch("patrol_cron.actions._submit_triage_alert") as mock_alert, \
             um.patch("patrol_cron.actions._create_investigation_task") as mock_task:
            result = execute_finding(f, "log_only", rule_id="test")
        assert result is True
        mock_alert.assert_not_called()
        mock_task.assert_not_called()
        # action is NOT demoted — preserved for the DB record
        assert f.action == "cordon_node"

    def test_log_only_accepts_any_confidence(self):
        f = Finding(target_id="n1", severity="info", action="alert", confidence=0.01)
        result = execute_finding(f, "log_only", rule_id="test")
        assert result is True


class TestConfidenceThreshold:
    """Stage-based confidence gating."""

    def test_low_confidence_skipped_in_auto_cordon(self):
        f = Finding(target_id="n1", severity="critical", action="cordon_node", confidence=0.5)
        result = execute_finding(f, "auto_cordon")
        assert result is False  # 0.5 < 0.9 threshold

    def test_low_confidence_skipped_in_submit_alert(self):
        f = Finding(target_id="n1", severity="warning", action="alert", confidence=0.3)
        result = execute_finding(f, "submit_alert")
        assert result is False  # 0.3 < 0.7 threshold

    def test_low_confidence_allowed_in_create_task(self):
        """create_task has threshold 0.3, so 0.5 passes."""
        f = Finding(target_id="n1", severity="warning", action="create_task", confidence=0.5)
        with um.patch("patrol_cron.actions._create_investigation_task", return_value=True) as mock:
            result = execute_finding(f, "create_task")
        assert result is True
        mock.assert_called_once()


class TestStageDemotion:
    """Stage 1 demotes destructive actions to create_task."""

    def test_cordon_demoted_to_task_in_stage1(self):
        f = Finding(target_id="n1", severity="critical", action="cordon_node")
        with um.patch("patrol_cron.actions._create_investigation_task", return_value=True) as mock:
            result = execute_finding(f, "create_task")
        assert result is True
        mock.assert_called_once()

    def test_alert_demoted_to_task_in_stage1(self):
        f = Finding(target_id="n1", severity="warning", action="alert")
        with um.patch("patrol_cron.actions._create_investigation_task", return_value=True) as mock:
            execute_finding(f, "create_task")
        mock.assert_called_once()

    def test_cordon_not_demoted_in_auto_cordon_stage(self):
        f = Finding(target_id="n1", severity="critical", action="cordon_node")
        with um.patch("patrol_cron.actions._submit_triage_alert", return_value=(True, None)) as mock:
            result = execute_finding(f, "auto_cordon")
        assert result is True
        mock.assert_called_once()
        assert f.action == "cordon_node"  # NOT demoted


class TestCordonSwitchNodes:
    """cordon_switch_nodes looks up nodes from topology."""

    def test_switch_nodes_cordoned(self):
        f = Finding(
            target_id="switch-1", severity="critical",
            action="cordon_switch_nodes",
            action_params={"alertname": "SwitchDown"},
        )
        with um.patch("patrol_cron.actions._lookup_switch_nodes", return_value=["n1", "n2"]):
            with um.patch("patrol_cron.actions._submit_triage_alert", return_value=(True, None)) as mock:
                result = execute_finding(f, "auto_cordon")
        assert result is True
        assert mock.call_count == 2  # one per node

    def test_switch_no_nodes_falls_back_to_alert(self):
        f = Finding(
            target_id="switch-1", severity="critical",
            action="cordon_switch_nodes",
        )
        with um.patch("patrol_cron.actions._lookup_switch_nodes", return_value=[]):
            with um.patch("patrol_cron.actions._submit_triage_alert", return_value=(True, None)) as mock:
                result = execute_finding(f, "auto_cordon")
        assert result is True
        # Should fall back to alert with action="alert"
        call_kwargs = mock.call_args[1]
        assert call_kwargs["action"] == "alert"


class TestCreateTask:
    """create_task dry-run when CHAT_UI_API_URL is empty."""

    def test_dry_run_when_no_api_url(self):
        f = Finding(target_id="n1", severity="warning", action="create_task")
        with um.patch("patrol_cron.actions.CHAT_UI_API_URL", ""):
            result = execute_finding(f, "create_task")
        assert result is True  # dry-run returns True
