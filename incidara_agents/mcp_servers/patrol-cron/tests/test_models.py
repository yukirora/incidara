"""Tests for patrol_cron.models — data types."""

from patrol_cron.models import CollectionResult, TargetData, Finding


class TestTargetData:
    def test_defaults(self):
        t = TargetData(id="n1", type="node", payload={"ok": True})
        assert t.meta == {}

    def test_full_construction(self):
        t = TargetData(id="sw1", type="switch", payload=42, meta={"ip": "1.2.3.4"})
        assert t.id == "sw1"
        assert t.meta["ip"] == "1.2.3.4"


class TestCollectionResult:
    def test_defaults(self):
        r = CollectionResult(collector_name="test")
        assert r.targets == []
        assert r.errors == []
        assert r.duration == 0.0

    def test_with_targets(self):
        t = TargetData(id="n1", type="node", payload={})
        r = CollectionResult(collector_name="c1", targets=[t], duration=1.5)
        assert len(r.targets) == 1
        assert r.duration == 1.5


class TestFinding:
    def test_defaults(self):
        f = Finding(target_id="n1", severity="warning", action="alert")
        assert f.action_params == {}
        assert f.evidence == {}
        assert f.confidence == 1.0

    def test_custom_confidence(self):
        f = Finding(target_id="n1", severity="critical", action="cordon_node", confidence=0.8)
        assert f.confidence == 0.8


def test_observation_defaults_condition_lifecycle():
    from patrol_cron.models import Observation

    obs = Observation(
        signal_key="b300_nvlink_fm_bad",
        target_id="node-1",
        status="bad",
        action="cordon_node",
        severity="critical",
    )

    assert obs.lifecycle["kind"] == "condition"
    assert obs.lifecycle["open_after_consecutive"] == 1
    assert obs.lifecycle["close_after_healthy"] == 1
    assert obs.lifecycle["unknown_keeps_active"] is True
    assert "pending_ttl_cycles" not in obs.lifecycle
    assert "closed_ttl_hours" not in obs.lifecycle
    assert obs.dedup_key == ("rule_id", "target_id", "signal_key", "action")


def test_rule_result_carries_observations_and_private_state():
    from patrol_cron.models import Observation, RuleResult

    result = RuleResult(
        observations=[
            Observation(
                signal_key="signal-a",
                target_id="node-1",
                status="bad",
                action="alert",
                severity="warning",
            )
        ],
        state={"node-1": {"prev_counter": 7}},
    )

    assert len(result.observations) == 1
    assert result.state["node-1"]["prev_counter"] == 7
