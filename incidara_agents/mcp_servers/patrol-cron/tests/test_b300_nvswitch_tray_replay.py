"""Replay coverage for b300_nvswitch_tray_v1 lifecycle behavior."""

import unittest.mock as um

from patrol_cron.analyzer import run_sandboxed
from patrol_cron.lifecycle import apply_rule_result
from patrol_cron.models import CollectionResult, TargetData
from patrol_cron.rule_sources import B300_NVSWITCH_TRAY_V1


TARGET = "lg-cmc-demo-r01u01-b300-000001"


class FakeLifecycleDb:
    def __init__(self):
        self.lifecycle = {}
        self.findings = {}
        self.next_finding_id = 9100
        self.insert_calls = []
        self.refresh_calls = []
        self.deactivate_calls = []

    def _key(self, rule_id, target_id, signal_key, action):
        return (rule_id, target_id, signal_key, action)

    def get_lifecycle_state(self, rule_id, target_id, signal_key, action):
        row = self.lifecycle.get(self._key(rule_id, target_id, signal_key, action))
        return dict(row) if row else None

    def upsert_lifecycle_state(self, row):
        self.lifecycle[self._key(
            row["rule_id"], row["target_id"], row["signal_key"], row["action"]
        )] = dict(row)

    def find_active_finding(self, rule_id, target_id, signal_key, action):
        for finding in self.findings.values():
            if (
                finding["rule_id"] == rule_id
                and finding["target_id"] == target_id
                and finding["action"] == action
                and finding["active"]
            ):
                return dict(finding)
        return None

    def has_active_lifecycle_signals(self, rule_id, target_id, action, finding_id, exclude_signal_key=""):
        for row in self.lifecycle.values():
            if (
                row["rule_id"] == rule_id
                and row["target_id"] == target_id
                and row["action"] == action
                and row.get("active_finding_id") == finding_id
                and row["signal_key"] != exclude_signal_key
            ):
                return True
        return False

    def insert_finding(self, **kwargs):
        finding_id = self.next_finding_id
        self.next_finding_id += 1
        self.insert_calls.append(kwargs)
        self.findings[finding_id] = {
            **kwargs,
            "finding_id": finding_id,
            "active": True,
            "seen_count": 1,
        }
        return finding_id

    def refresh_active_finding(self, finding_id, evidence, confidence):
        self.refresh_calls.append((finding_id, evidence, confidence))
        self.findings[finding_id]["evidence"] = evidence
        self.findings[finding_id]["confidence"] = confidence
        self.findings[finding_id]["seen_count"] += 1
        return 1

    def deactivate_finding(self, finding_id):
        self.deactivate_calls.append(finding_id)
        self.findings[finding_id]["active"] = False
        return 1


def _collection(status):
    if status == "bad":
        payload = {
            "ssh_ok": True,
            "outputs": {
                "dmesg_rxdetect": "\n".join([
                    "knvlinkUpdatePostRxDetectLinkMask_IMPL: Failed to update Rx Detect Link mask"
                    for _ in range(5)
                ]),
                "nvidia_smi_timeout": "124",
            },
        }
    elif status == "healthy":
        payload = {
            "ssh_ok": True,
            "outputs": {
                "dmesg_rxdetect": "",
                "nvidia_smi_timeout": "0",
            },
        }
    else:
        payload = {"ssh_ok": False, "ssh_error": "timeout"}

    return CollectionResult(
        collector_name="b300_nvswitch_tray",
        targets=[TargetData(id=TARGET, type="node", payload=payload)],
    )


def _rule():
    return {
        "rule_id": "b300_nvswitch_tray_v1",
        "stage": "create_task",
        "updated_at": "2026-06-21 00:00:00+00",
    }


def _collector():
    return {"target_type": "node"}


def _provenance():
    return {
        "collector_snapshot_id": "snapshot",
        "raw_evidence_hash": "snapshot",
        "rule_version_at": "2026-06-21 00:00:00+00",
        "rule_code_hash": "rule-hash",
        "collector_config_hash": "collector-hash",
    }


def _run_cycle(fake_db, private_state, status):
    rule_result = run_sandboxed(B300_NVSWITCH_TRAY_V1, _collection(status), private_state)
    with um.patch("patrol_cron.lifecycle.db", fake_db), \
         um.patch("patrol_cron.lifecycle.execute_finding", return_value=True):
        apply_rule_result(rule_result, _rule(), _collector(), _provenance())
    return rule_result.state


def test_b300_nvswitch_tray_opens_once_refreshes_then_closes_after_healthy_threshold():
    fake_db = FakeLifecycleDb()
    private_state = {}

    private_state = _run_cycle(fake_db, private_state, "bad")
    assert fake_db.insert_calls == []
    assert fake_db.deactivate_calls == []

    private_state = _run_cycle(fake_db, private_state, "bad")
    assert len(fake_db.insert_calls) == 1
    finding_id = next(iter(fake_db.findings))
    assert fake_db.findings[finding_id]["active"] is True

    private_state = _run_cycle(fake_db, private_state, "bad")
    assert len(fake_db.insert_calls) == 1
    assert len(fake_db.refresh_calls) == 1
    assert fake_db.findings[finding_id]["seen_count"] == 2

    private_state = _run_cycle(fake_db, private_state, "unknown")
    assert fake_db.deactivate_calls == []
    assert fake_db.findings[finding_id]["active"] is True

    private_state = _run_cycle(fake_db, private_state, "healthy")
    assert fake_db.deactivate_calls == []
    assert fake_db.findings[finding_id]["active"] is True

    _run_cycle(fake_db, private_state, "healthy")
    assert fake_db.deactivate_calls == [finding_id]
    assert fake_db.findings[finding_id]["active"] is False
    assert len(fake_db.insert_calls) == 1


def test_b300_nvswitch_tray_rule_returns_condition_observations():
    result = run_sandboxed(B300_NVSWITCH_TRAY_V1, _collection("bad"), {})

    assert result.lifecycle_enabled is True
    assert result.observations[0].signal_key == "b300_nvswitch_tray_failure"
    assert result.observations[0].status == "bad"
    assert result.observations[0].action == "drain_node"
    assert result.observations[0].lifecycle["open_after_consecutive"] == 2
    assert result.observations[0].lifecycle["close_after_healthy"] == 2


def test_b300_nvswitch_tray_missing_payload_is_unknown_not_healthy():
    result = run_sandboxed(B300_NVSWITCH_TRAY_V1, _collection("unknown"), {})

    assert result.observations[0].status == "unknown"
    assert result.observations[0].signal_key == "b300_nvswitch_tray_failure"
