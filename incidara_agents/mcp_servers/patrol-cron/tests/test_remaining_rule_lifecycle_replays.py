"""Full lifecycle replays for migrated built-in patrol rules."""

import unittest.mock as um

from patrol_cron.analyzer import run_sandboxed
from patrol_cron.lifecycle import apply_rule_result
from patrol_cron.models import CollectionResult, TargetData
from patrol_cron.rule_sources import (
    B300_GPU_XID_V1,
    B300_NIC_HEALTH_V1,
    H200_NVME_HEALTH_V1,
    IB_LINK_FLAPPING_REPEAT_V1,
    JOB_FAILURE_INVESTIGATION,
    LARGE_JOB_FAILURE_V1,
    NVIDIA_ECC_ERROR_V1,
    STORAGE_NVME_HEALTH_V1,
    SWITCH_HEALTH_IB_V1,
    SWITCH_HEALTH_RUIJIE_V1,
    SWITCH_HEALTH_UFM_V1,
)


class FakeLifecycleDb:
    def __init__(self):
        self.lifecycle = {}
        self.findings = {}
        self.next_finding_id = 12000
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


def _collection(name, targets):
    return CollectionResult(
        collector_name=name,
        targets=[TargetData(id=target_id, type=target_type, payload=payload)
                 for target_id, target_type, payload in targets],
    )


def _rule(rule_id, stage="create_task"):
    return {
        "rule_id": rule_id,
        "stage": stage,
        "updated_at": "2026-06-21 00:00:00+00",
    }


def _collector(target_type="node"):
    return {"target_type": target_type}


def _provenance():
    return {
        "collector_snapshot_id": "snapshot",
        "raw_evidence_hash": "snapshot",
        "rule_version_at": "2026-06-21 00:00:00+00",
        "rule_code_hash": "rule-hash",
        "collector_config_hash": "collector-hash",
    }


def _run_cycle(fake_db, source, rule_id, collection, private_state, stage="create_task", target_type="node"):
    result = run_sandboxed(source, collection, private_state)
    with um.patch("patrol_cron.lifecycle.db", fake_db), \
         um.patch("patrol_cron.lifecycle.execute_finding", return_value=True):
        apply_rule_result(result, _rule(rule_id, stage), _collector(target_type), _provenance())
    return result.state


def _finding_id(fake_db):
    assert len(fake_db.findings) == 1
    return next(iter(fake_db.findings))


def test_b300_gpu_xid_event_opens_once_then_refreshes_on_new_xid_growth():
    fake_db = FakeLifecycleDb()
    state = {}

    state = _run_cycle(fake_db, B300_GPU_XID_V1, "b300_gpu_xid_v1", _collection(
        "b300_gpu_health",
        [("node-a", "node", {"ssh_ok": True, "outputs": {"dmesg_xid": "NVRM: Xid (PCI:0000:01:00): 79"}})],
    ), state)
    assert fake_db.insert_calls == []

    state = _run_cycle(fake_db, B300_GPU_XID_V1, "b300_gpu_xid_v1", _collection(
        "b300_gpu_health",
        [("node-a", "node", {"ssh_ok": True, "outputs": {"dmesg_xid": "\n".join([
            "NVRM: Xid (PCI:0000:01:00): 79",
            "NVRM: Xid (PCI:0000:02:00): 79",
        ])}})],
    ), state)
    first_id = _finding_id(fake_db)
    assert fake_db.findings[first_id]["active"] is True

    state = _run_cycle(fake_db, B300_GPU_XID_V1, "b300_gpu_xid_v1", _collection(
        "b300_gpu_health",
        [("node-a", "node", {"ssh_ok": True, "outputs": {"dmesg_xid": "\n".join([
            "NVRM: Xid (PCI:0000:01:00): 79",
            "NVRM: Xid (PCI:0000:02:00): 79",
            "NVRM: Xid (PCI:0000:03:00): 79",
        ])}})],
    ), state)
    assert len(fake_db.refresh_calls) == 0

    state = _run_cycle(fake_db, B300_GPU_XID_V1, "b300_gpu_xid_v1", _collection(
        "b300_gpu_health",
        [("node-a", "node", {"ssh_ok": True, "outputs": {"dmesg_xid": "\n".join([
            "NVRM: Xid (PCI:0000:01:00): 79",
            "NVRM: Xid (PCI:0000:02:00): 79",
            "NVRM: Xid (PCI:0000:03:00): 79",
            "NVRM: Xid (PCI:0000:04:00): 79",
        ])}})],
    ), state)
    assert _finding_id(fake_db) == first_id
    assert len(fake_db.insert_calls) == 1
    assert len(fake_db.refresh_calls) == 1
    assert fake_db.deactivate_calls == []


def test_b300_nic_health_condition_opens_refreshes_and_closes_mlx5_issue():
    fake_db = FakeLifecycleDb()
    state = {}

    for count in [100, 180, 260]:
        state = _run_cycle(fake_db, B300_NIC_HEALTH_V1, "b300_nic_health_v1", _collection(
            "b300_nic_health",
            [("node-a", "node", {"ssh_ok": True, "outputs": {"mlx5_errors": f"errors: {count}", "ibstat_ports": ""}})],
        ), state)
    assert fake_db.insert_calls == []

    state = _run_cycle(fake_db, B300_NIC_HEALTH_V1, "b300_nic_health_v1", _collection(
        "b300_nic_health",
        [("node-a", "node", {"ssh_ok": True, "outputs": {"mlx5_errors": "errors: 340", "ibstat_ports": ""}})],
    ), state)
    first_id = _finding_id(fake_db)

    state = _run_cycle(fake_db, B300_NIC_HEALTH_V1, "b300_nic_health_v1", _collection(
        "b300_nic_health",
        [("node-a", "node", {"ssh_ok": True, "outputs": {"mlx5_errors": "errors: 420", "ibstat_ports": ""}})],
    ), state)
    assert len(fake_db.insert_calls) == 1
    assert len(fake_db.refresh_calls) == 1

    for _ in range(2):
        state = _run_cycle(fake_db, B300_NIC_HEALTH_V1, "b300_nic_health_v1", _collection(
            "b300_nic_health",
            [("node-a", "node", {"ssh_ok": True, "outputs": {"mlx5_errors": "errors: 420", "ibstat_ports": ""}})],
        ), state)
    assert fake_db.deactivate_calls == [first_id]
    assert fake_db.findings[first_id]["active"] is False


def test_h200_nvme_health_condition_opens_refreshes_and_closes():
    fake_db = FakeLifecycleDb()
    state = {}
    bad_payload = {"outputs": {"nvme_smart": '===DEVICE:/dev/nvme0n1===\n"critical_warning": 1\n"media_errors": 0\n'}}
    healthy_payload = {"outputs": {"nvme_smart": '===DEVICE:/dev/nvme0n1===\n"critical_warning": 0\n"media_errors": 0\n'}}

    state = _run_cycle(fake_db, H200_NVME_HEALTH_V1, "h200_nvme_health_v1", _collection("h200_nvme", [("h200-a", "node", bad_payload)]), state)
    first_id = _finding_id(fake_db)
    state = _run_cycle(fake_db, H200_NVME_HEALTH_V1, "h200_nvme_health_v1", _collection("h200_nvme", [("h200-a", "node", bad_payload)]), state)
    assert len(fake_db.refresh_calls) == 1
    for _ in range(2):
        state = _run_cycle(fake_db, H200_NVME_HEALTH_V1, "h200_nvme_health_v1", _collection("h200_nvme", [("h200-a", "node", healthy_payload)]), state)
    assert fake_db.deactivate_calls == [first_id]


def test_ib_link_flapping_condition_opens_refreshes_and_closes():
    fake_db = FakeLifecycleDb()
    state = {}
    bad_payload = {
        "ib_port_physical_state": {"values": [{"metric": {"port": "1", "instance": "node-a"}}]},
        "ib_port_state": {"values": []},
    }
    healthy_payload = {"ib_port_physical_state": {"values": []}, "ib_port_state": {"values": []}}

    state = _run_cycle(fake_db, IB_LINK_FLAPPING_REPEAT_V1, "ib_link_flapping_repeat_v1", _collection("ib_link", [("node-a", "node", bad_payload)]), state, stage="auto_cordon")
    assert fake_db.insert_calls == []
    state = _run_cycle(fake_db, IB_LINK_FLAPPING_REPEAT_V1, "ib_link_flapping_repeat_v1", _collection("ib_link", [("node-a", "node", bad_payload)]), state, stage="auto_cordon")
    first_id = _finding_id(fake_db)
    state = _run_cycle(fake_db, IB_LINK_FLAPPING_REPEAT_V1, "ib_link_flapping_repeat_v1", _collection("ib_link", [("node-a", "node", bad_payload)]), state, stage="auto_cordon")
    assert len(fake_db.refresh_calls) == 1
    for _ in range(2):
        state = _run_cycle(fake_db, IB_LINK_FLAPPING_REPEAT_V1, "ib_link_flapping_repeat_v1", _collection("ib_link", [("node-a", "node", healthy_payload)]), state, stage="auto_cordon")
    assert fake_db.deactivate_calls == [first_id]


def test_job_failure_investigation_event_opens_once_then_refreshes_for_new_job():
    fake_db = FakeLifecycleDb()
    state = {}
    for job_id in ["job-1", "job-2"]:
        state = _run_cycle(fake_db, JOB_FAILURE_INVESTIGATION, "job_failure_investigation", _collection(
            "job_failure",
            [("node-a", "node", {"entries": ["CUDA error: system not yet initialized"], "source_jobs": [job_id]})],
        ), state)
    first_id = _finding_id(fake_db)
    assert len(fake_db.insert_calls) == 1
    assert len(fake_db.refresh_calls) == 1
    assert fake_db.findings[first_id]["active"] is True

    state = _run_cycle(fake_db, JOB_FAILURE_INVESTIGATION, "job_failure_investigation", _collection(
        "job_failure",
        [("node-a", "node", {"entries": ["CUDA error: system not yet initialized"], "source_jobs": ["job-2"]})],
    ), state)
    assert len(fake_db.insert_calls) == 1
    assert len(fake_db.refresh_calls) == 1


def _large_job_targets(prefix):
    targets = []
    for idx in range(3):
        targets.append((f"{prefix}-{idx}", "job", {
            "taskRoles": {"worker": {"taskStatuses": [
                {"containerNodeName": "node-a", "taskState": "FAILED", "containerExitCode": 1, "completedTime": 1000},
                {"containerNodeName": "node-b", "taskState": "FAILED", "containerExitCode": -220, "completedTime": 2000},
            ]}}
        }))
    return targets


def test_large_job_failure_event_opens_once_then_refreshes_for_new_job_set():
    fake_db = FakeLifecycleDb()
    state = {}
    state = _run_cycle(fake_db, LARGE_JOB_FAILURE_V1, "large_job_failure_v1", _collection("large_job_failure", _large_job_targets("job-a")), state)
    first_id = _finding_id(fake_db)
    state = _run_cycle(fake_db, LARGE_JOB_FAILURE_V1, "large_job_failure_v1", _collection("large_job_failure", _large_job_targets("job-b")), state)
    assert _finding_id(fake_db) == first_id
    assert len(fake_db.insert_calls) == 1
    assert len(fake_db.refresh_calls) == 1
    assert fake_db.deactivate_calls == []


def test_nvidia_ecc_error_event_opens_once_then_refreshes_on_new_count_growth():
    fake_db = FakeLifecycleDb()
    state = {}
    for total in [3, 5]:
        state = _run_cycle(fake_db, NVIDIA_ECC_ERROR_V1, "nvidia_ecc_error_v1", _collection(
            "nvidia_ecc",
            [("node-a", "node", {
                "ecc_check": {"ssh_ok": True, "outputs": {"ecc_counts": f"0, {total}, 1, 2"}},
                "ecc_job_logs": {"entries": [], "source_jobs": []},
            })],
        ), state, stage="submit_alert")
    first_id = _finding_id(fake_db)
    assert fake_db.findings[first_id]["active"] is True
    assert len(fake_db.insert_calls) == 1
    assert len(fake_db.refresh_calls) == 1


def test_storage_nvme_health_condition_opens_refreshes_and_closes():
    fake_db = FakeLifecycleDb()
    state = {}
    bad_payload = {"outputs": {"nvme_smart": '===DEVICE:/dev/nvme0n1===\n"critical_warning": 1\n"media_errors": 0\n"percentage_used": 1\n'}}
    healthy_payload = {"outputs": {"nvme_smart": '===DEVICE:/dev/nvme0n1===\n"critical_warning": 0\n"media_errors": 0\n"percentage_used": 1\n'}}

    state = _run_cycle(fake_db, STORAGE_NVME_HEALTH_V1, "storage_nvme_health_v1", _collection("storage_nvme", [("storage-a", "node", bad_payload)]), state)
    first_id = _finding_id(fake_db)
    state = _run_cycle(fake_db, STORAGE_NVME_HEALTH_V1, "storage_nvme_health_v1", _collection("storage_nvme", [("storage-a", "node", bad_payload)]), state)
    assert len(fake_db.refresh_calls) == 1
    for _ in range(2):
        state = _run_cycle(fake_db, STORAGE_NVME_HEALTH_V1, "storage_nvme_health_v1", _collection("storage_nvme", [("storage-a", "node", healthy_payload)]), state)
    assert fake_db.deactivate_calls == [first_id]


def test_switch_health_ib_unreachable_condition_opens_refreshes_and_closes():
    fake_db = FakeLifecycleDb()
    state = {}
    bad_payload = {"ssh_ok": False, "ssh_error": "timeout"}
    healthy_payload = {"ssh_ok": True, "outputs": {"show interfaces ib": ""}}

    for _ in range(9):
        state = _run_cycle(fake_db, SWITCH_HEALTH_IB_V1, "switch_health_ib_v1", _collection("switch_ib", [("sw-a", "switch", bad_payload)]), state, target_type="switch")
    assert fake_db.insert_calls == []
    state = _run_cycle(fake_db, SWITCH_HEALTH_IB_V1, "switch_health_ib_v1", _collection("switch_ib", [("sw-a", "switch", bad_payload)]), state, target_type="switch")
    first_id = _finding_id(fake_db)
    state = _run_cycle(fake_db, SWITCH_HEALTH_IB_V1, "switch_health_ib_v1", _collection("switch_ib", [("sw-a", "switch", bad_payload)]), state, target_type="switch")
    assert len(fake_db.refresh_calls) == 1
    for _ in range(2):
        state = _run_cycle(fake_db, SWITCH_HEALTH_IB_V1, "switch_health_ib_v1", _collection("switch_ib", [("sw-a", "switch", healthy_payload)]), state, target_type="switch")
    assert fake_db.deactivate_calls == [first_id]


def test_switch_health_ruijie_condition_opens_refreshes_and_closes():
    fake_db = FakeLifecycleDb()
    state = {}

    def payload(crc):
        return {
            "ssh_ok": True,
            "outputs": {"show interfaces counters errors": f"Port CRC-Align-Err FCS-Err\nTe1 0 {crc} 0 1"},
        }

    state = _run_cycle(fake_db, SWITCH_HEALTH_RUIJIE_V1, "switch_health_ruijie_v1", _collection("switch_ruijie", [("sw-a", "switch", payload(1))]), state, target_type="switch")
    state = _run_cycle(fake_db, SWITCH_HEALTH_RUIJIE_V1, "switch_health_ruijie_v1", _collection("switch_ruijie", [("sw-a", "switch", payload(4))]), state, target_type="switch")
    assert fake_db.insert_calls == []
    state = _run_cycle(fake_db, SWITCH_HEALTH_RUIJIE_V1, "switch_health_ruijie_v1", _collection("switch_ruijie", [("sw-a", "switch", payload(7))]), state, target_type="switch")
    first_id = _finding_id(fake_db)
    state = _run_cycle(fake_db, SWITCH_HEALTH_RUIJIE_V1, "switch_health_ruijie_v1", _collection("switch_ruijie", [("sw-a", "switch", payload(10))]), state, target_type="switch")
    assert len(fake_db.refresh_calls) == 1
    for _ in range(2):
        state = _run_cycle(fake_db, SWITCH_HEALTH_RUIJIE_V1, "switch_health_ruijie_v1", _collection("switch_ruijie", [("sw-a", "switch", payload(10))]), state, target_type="switch")
    assert fake_db.deactivate_calls == [first_id]


def test_switch_health_ufm_unreachable_condition_opens_refreshes_and_closes():
    fake_db = FakeLifecycleDb()
    state = {}
    bad_payload = {"ssh_ok": False, "ssh_error": "timeout"}
    healthy_payload = {"ssh_ok": True, "outputs": {"cat /proc/uptime": "130.0 1000.0"}}

    for _ in range(14):
        state = _run_cycle(fake_db, SWITCH_HEALTH_UFM_V1, "switch_health_ufm_v1", _collection("switch_ufm", [("ufm-a", "switch", bad_payload)]), state, target_type="switch")
    assert fake_db.insert_calls == []
    state = _run_cycle(fake_db, SWITCH_HEALTH_UFM_V1, "switch_health_ufm_v1", _collection("switch_ufm", [("ufm-a", "switch", bad_payload)]), state, target_type="switch")
    first_id = _finding_id(fake_db)
    state = _run_cycle(fake_db, SWITCH_HEALTH_UFM_V1, "switch_health_ufm_v1", _collection("switch_ufm", [("ufm-a", "switch", bad_payload)]), state, target_type="switch")
    assert len(fake_db.refresh_calls) == 1
    for _ in range(2):
        state = _run_cycle(fake_db, SWITCH_HEALTH_UFM_V1, "switch_health_ufm_v1", _collection("switch_ufm", [("ufm-a", "switch", healthy_payload)]), state, target_type="switch")
    assert fake_db.deactivate_calls == [first_id]


def test_switch_health_ufm_reboot_event_opens_once_then_refreshes():
    fake_db = FakeLifecycleDb()
    state = {}
    for uptime in ["90.0 1000.0", "80.0 1000.0"]:
        state = _run_cycle(fake_db, SWITCH_HEALTH_UFM_V1, "switch_health_ufm_v1", _collection(
            "switch_ufm",
            [("ufm-a", "switch", {"ssh_ok": True, "outputs": {"cat /proc/uptime": uptime}})],
        ), state, target_type="switch")
    first_id = _finding_id(fake_db)
    assert fake_db.findings[first_id]["action"] == "alert"
    assert len(fake_db.insert_calls) == 1
    assert len(fake_db.refresh_calls) == 1
