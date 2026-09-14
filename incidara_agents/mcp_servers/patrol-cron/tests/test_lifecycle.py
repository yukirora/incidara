"""Tests for observation lifecycle handling."""

import unittest.mock as um

from patrol_cron.models import Observation, RuleResult


def test_lifecycle_cleanup_defaults_keep_state_for_one_week():
    from patrol_cron import lifecycle

    assert lifecycle.PENDING_LIFECYCLE_TTL_HOURS == 168
    assert lifecycle.CLOSED_LIFECYCLE_TTL_HOURS == 168


def _rule(stage="create_task"):
    return {"rule_id": "rule-1", "stage": stage, "updated_at": "2026-06-21T00:00:00Z"}


def _collector():
    return {"target_type": "node"}


def _provenance():
    return {
        "collector_snapshot_id": "snapshot-1",
        "raw_evidence_hash": "snapshot-1",
        "rule_version_at": "2026-06-21T00:00:00Z",
        "rule_code_hash": "rule-hash",
        "collector_config_hash": "collector-hash",
    }


def _obs(status="bad", lifecycle=None):
    return Observation(
        signal_key="fm_bad",
        target_id="b300-000029",
        status=status,
        severity="warning",
        action="alert",
        evidence={"status": status},
        confidence=0.95,
        lifecycle=lifecycle or {
            "kind": "condition",
            "open_after_consecutive": 3,
            "close_after_healthy": 2,
        },
    )


def test_bad_observations_open_only_after_threshold():
    from patrol_cron.lifecycle import apply_rule_result

    with um.patch("patrol_cron.lifecycle.db") as mock_db, \
         um.patch("patrol_cron.lifecycle.execute_finding", return_value=True):
        mock_db.get_lifecycle_state.return_value = None
        mock_db.find_active_finding.return_value = None

        apply_rule_result(RuleResult(observations=[_obs("bad")]), _rule(), _collector(), _provenance())

    mock_db.insert_finding.assert_not_called()
    row = mock_db.upsert_lifecycle_state.call_args.args[0]
    assert row["bad_count"] == 1
    assert row["healthy_count"] == 0
    assert row["active_finding_id"] is None
    assert row["expires_at"] is not None


def test_bad_observation_at_threshold_opens_one_finding():
    from patrol_cron.lifecycle import apply_rule_result

    existing_state = {
        "rule_id": "rule-1",
        "target_id": "b300-000029",
        "signal_key": "fm_bad",
        "action": "alert",
        "bad_count": 2,
        "healthy_count": 0,
        "active_finding_id": None,
    }

    with um.patch("patrol_cron.lifecycle.db") as mock_db, \
         um.patch("patrol_cron.lifecycle.execute_finding", return_value=True) as execute:
        mock_db.get_lifecycle_state.return_value = existing_state
        mock_db.find_active_finding.return_value = None
        mock_db.insert_finding.return_value = 8907

        apply_rule_result(RuleResult(observations=[_obs("bad")]), _rule(), _collector(), _provenance())

    execute.assert_called_once()
    mock_db.insert_finding.assert_called_once()
    insert_kwargs = mock_db.insert_finding.call_args.kwargs
    assert insert_kwargs["target_id"] == "b300-000029"
    assert "signal_key" not in insert_kwargs
    row = mock_db.upsert_lifecycle_state.call_args.args[0]
    assert row["bad_count"] == 3
    assert row["active_finding_id"] == 8907
    assert row["expires_at"] is None


def test_bad_observation_refreshes_active_finding_instead_of_inserting_duplicate():
    from patrol_cron.lifecycle import apply_rule_result

    existing_state = {
        "rule_id": "rule-1",
        "target_id": "b300-000029",
        "signal_key": "fm_bad",
        "action": "alert",
        "bad_count": 3,
        "healthy_count": 0,
        "active_finding_id": 8907,
    }

    with um.patch("patrol_cron.lifecycle.db") as mock_db, \
         um.patch("patrol_cron.lifecycle.execute_finding") as execute:
        mock_db.get_lifecycle_state.return_value = existing_state
        mock_db.find_active_finding.return_value = {"finding_id": 8907}

        apply_rule_result(RuleResult(observations=[_obs("bad")]), _rule(), _collector(), _provenance())

    execute.assert_not_called()
    mock_db.insert_finding.assert_not_called()
    mock_db.refresh_active_finding.assert_called_once_with(8907, {"status": "bad"}, 0.95)
    row = mock_db.upsert_lifecycle_state.call_args.args[0]
    assert row["active_finding_id"] == 8907


def test_bad_observation_ignores_stale_inactive_lifecycle_finding_id():
    from patrol_cron.lifecycle import apply_rule_result

    existing_state = {
        "rule_id": "rule-1",
        "target_id": "b300-000029",
        "signal_key": "fm_bad",
        "action": "alert",
        "bad_count": 3,
        "healthy_count": 0,
        "active_finding_id": 8907,
    }

    with um.patch("patrol_cron.lifecycle.db") as mock_db, \
         um.patch("patrol_cron.lifecycle.execute_finding", return_value=True) as execute:
        mock_db.get_lifecycle_state.return_value = existing_state
        mock_db.find_active_finding.return_value = None
        mock_db.insert_finding.return_value = 8908

        apply_rule_result(RuleResult(observations=[_obs("bad")]), _rule(), _collector(), _provenance())

    mock_db.refresh_active_finding.assert_not_called()
    execute.assert_called_once()
    mock_db.insert_finding.assert_called_once()
    row = mock_db.upsert_lifecycle_state.call_args.args[0]
    assert row["active_finding_id"] == 8908


def test_different_bad_signals_refresh_same_node_finding():
    from patrol_cron.lifecycle import apply_rule_result

    first = Observation(
        signal_key="signal-a",
        target_id="node-1",
        status="bad",
        action="alert",
        evidence={"signal": "a"},
        lifecycle={"kind": "condition", "open_after_consecutive": 1, "close_after_healthy": 1},
    )
    second = Observation(
        signal_key="signal-b",
        target_id="node-1",
        status="bad",
        action="alert",
        evidence={"signal": "b"},
        lifecycle={"kind": "condition", "open_after_consecutive": 1, "close_after_healthy": 1},
    )

    with um.patch("patrol_cron.lifecycle.db") as mock_db, \
         um.patch("patrol_cron.lifecycle.execute_finding", return_value=True):
        mock_db.get_lifecycle_state.return_value = None
        mock_db.find_active_finding.side_effect = [None, {"finding_id": 100}]
        mock_db.insert_finding.return_value = 100

        apply_rule_result(
            RuleResult(observations=[first, second]),
            _rule(),
            _collector(),
            _provenance(),
        )

    mock_db.insert_finding.assert_called_once()
    mock_db.refresh_active_finding.assert_called_once_with(100, {"signal": "b"}, 1.0)
    rows = [call.args[0] for call in mock_db.upsert_lifecycle_state.call_args_list]
    assert rows[0]["signal_key"] == "signal-a"
    assert rows[0]["active_finding_id"] == 100
    assert rows[1]["signal_key"] == "signal-b"
    assert rows[1]["active_finding_id"] == 100


def test_recovered_signal_does_not_close_finding_when_other_signal_still_active():
    from patrol_cron.lifecycle import apply_rule_result

    existing_state = {
        "rule_id": "rule-1",
        "target_id": "node-1",
        "signal_key": "signal-a",
        "action": "alert",
        "bad_count": 1,
        "healthy_count": 0,
        "active_finding_id": 100,
    }
    obs = Observation(
        signal_key="signal-a",
        target_id="node-1",
        status="healthy",
        action="alert",
        lifecycle={"kind": "condition", "open_after_consecutive": 1, "close_after_healthy": 1},
    )

    with um.patch("patrol_cron.lifecycle.db") as mock_db:
        mock_db.get_lifecycle_state.return_value = existing_state
        mock_db.find_active_finding.return_value = {"finding_id": 100}
        mock_db.has_active_lifecycle_signals.return_value = True

        apply_rule_result(RuleResult(observations=[obs]), _rule(), _collector(), _provenance())

    mock_db.deactivate_finding.assert_not_called()
    row = mock_db.upsert_lifecycle_state.call_args.args[0]
    assert row["active_finding_id"] is None
    assert row["healthy_count"] == 1


def test_recovered_final_signal_closes_finding():
    from patrol_cron.lifecycle import apply_rule_result

    existing_state = {
        "rule_id": "rule-1",
        "target_id": "node-1",
        "signal_key": "signal-a",
        "action": "alert",
        "bad_count": 1,
        "healthy_count": 0,
        "active_finding_id": 100,
    }
    obs = Observation(
        signal_key="signal-a",
        target_id="node-1",
        status="healthy",
        action="alert",
        lifecycle={"kind": "condition", "open_after_consecutive": 1, "close_after_healthy": 1},
    )

    with um.patch("patrol_cron.lifecycle.db") as mock_db:
        mock_db.get_lifecycle_state.return_value = existing_state
        mock_db.find_active_finding.return_value = {"finding_id": 100}
        mock_db.has_active_lifecycle_signals.return_value = False

        apply_rule_result(RuleResult(observations=[obs]), _rule(), _collector(), _provenance())

    mock_db.deactivate_finding.assert_called_once_with(100)
    row = mock_db.upsert_lifecycle_state.call_args.args[0]
    assert row["active_finding_id"] is None
    assert row["healthy_count"] == 1


def test_event_observation_refreshes_aggregate_active_finding():
    from patrol_cron.lifecycle import apply_rule_result

    obs = Observation(
        signal_key="gpu_xid_event",
        target_id="node-1",
        status="bad",
        severity="critical",
        action="cordon_node",
        evidence={"xid_codes": [94]},
        confidence=0.9,
        lifecycle={"kind": "event"},
    )

    with um.patch("patrol_cron.lifecycle.db") as mock_db, \
         um.patch("patrol_cron.lifecycle.execute_finding") as execute:
        mock_db.find_active_finding.return_value = {"finding_id": 123}
        mock_db.get_lifecycle_state.return_value = None

        apply_rule_result(
            RuleResult(observations=[obs]),
            {"rule_id": "b300_gpu_xid_v1", "stage": "create_task"},
            {"target_type": "node"},
            _provenance(),
        )

    execute.assert_not_called()
    mock_db.insert_finding.assert_not_called()
    mock_db.refresh_active_finding.assert_called_once_with(123, {"xid_codes": [94]}, 0.9)
    row = mock_db.upsert_lifecycle_state.call_args.args[0]
    assert row["active_finding_id"] == 123
    assert row["last_status"] == "bad"


def test_unknown_observation_keeps_active_finding_open():
    from patrol_cron.lifecycle import apply_rule_result

    existing_state = {
        "rule_id": "rule-1",
        "target_id": "b300-000029",
        "signal_key": "fm_bad",
        "action": "alert",
        "bad_count": 3,
        "healthy_count": 0,
        "active_finding_id": 8907,
    }

    with um.patch("patrol_cron.lifecycle.db") as mock_db:
        mock_db.get_lifecycle_state.return_value = existing_state
        mock_db.find_active_finding.return_value = {"finding_id": 8907}

        apply_rule_result(RuleResult(observations=[_obs("unknown")]), _rule(), _collector(), _provenance())

    mock_db.insert_finding.assert_not_called()
    mock_db.deactivate_finding.assert_not_called()
    row = mock_db.upsert_lifecycle_state.call_args.args[0]
    assert row["active_finding_id"] == 8907
    assert row["last_status"] == "unknown"


def test_healthy_observation_deactivates_only_after_threshold():
    from patrol_cron.lifecycle import apply_rule_result

    existing_state = {
        "rule_id": "rule-1",
        "target_id": "b300-000029",
        "signal_key": "fm_bad",
        "action": "alert",
        "bad_count": 3,
        "healthy_count": 1,
        "active_finding_id": 8907,
    }

    with um.patch("patrol_cron.lifecycle.db") as mock_db:
        mock_db.get_lifecycle_state.return_value = existing_state
        mock_db.find_active_finding.return_value = {"finding_id": 8907}
        mock_db.has_active_lifecycle_signals.return_value = False

        apply_rule_result(RuleResult(observations=[_obs("healthy")]), _rule(), _collector(), _provenance())

    mock_db.deactivate_finding.assert_called_once_with(8907)
    row = mock_db.upsert_lifecycle_state.call_args.args[0]
    assert row["bad_count"] == 0
    assert row["healthy_count"] == 2
    assert row["active_finding_id"] is None
    assert row["expires_at"] is not None
