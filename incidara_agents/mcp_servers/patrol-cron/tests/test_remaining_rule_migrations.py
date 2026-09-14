"""Contract coverage for migrated built-in patrol rules."""

from patrol_cron.analyzer import run_sandboxed
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


def _collection(name, targets):
    return CollectionResult(
        collector_name=name,
        targets=[TargetData(id=target_id, type=target_type, payload=payload)
                 for target_id, target_type, payload in targets],
    )


def test_b300_gpu_xid_is_event_gated_by_new_xid_growth():
    code = "NVRM: Xid (PCI:0000:01:00): 79"
    result = run_sandboxed(
        B300_GPU_XID_V1,
        _collection("b300_gpu_health", [("node-a", "node", {"ssh_ok": True, "outputs": {"dmesg_xid": code}})]),
        {},
    )
    assert result.observations == []

    code2 = code + "\nNVRM: Xid (PCI:0000:02:00): 79"
    result = run_sandboxed(
        B300_GPU_XID_V1,
        _collection("b300_gpu_health", [("node-a", "node", {"ssh_ok": True, "outputs": {"dmesg_xid": code2}})]),
        result.state,
    )
    assert result.observations[0].signal_key == "b300_gpu_xid"
    assert result.observations[0].lifecycle["kind"] == "event"


def test_b300_nic_health_emits_condition_signals_for_counter_delta_and_port_down():
    state = {}
    result = run_sandboxed(
        B300_NIC_HEALTH_V1,
        _collection("b300_nic_health", [("node-a", "node", {
            "ssh_ok": True,
            "outputs": {"mlx5_errors": "errors: 100", "ibstat_ports": ""},
        })]),
        state,
    )
    state = result.state

    result = run_sandboxed(
        B300_NIC_HEALTH_V1,
        _collection("b300_nic_health", [("node-a", "node", {
            "ssh_ok": True,
            "outputs": {
                "mlx5_errors": "errors: 180",
                "ibstat_ports": "CA 'mlx5_0'\nState: Down\nLink layer: InfiniBand",
            },
        })]),
        state,
    )
    signals = {obs.signal_key: obs for obs in result.observations}
    assert signals["b300_nic_mlx5_errors"].status == "bad"
    assert signals["b300_nic_mlx5_errors"].lifecycle["open_after_consecutive"] == 3
    assert signals["b300_nic_ib_down"].status == "bad"
    assert signals["b300_nic_ib_down"].lifecycle["open_after_consecutive"] == 2


def test_h200_nvme_health_emits_bad_and_healthy_condition_observations():
    bad = run_sandboxed(
        H200_NVME_HEALTH_V1,
        _collection("h200_nvme_health", [("h200-a", "node", {
            "outputs": {"nvme_smart": '===DEVICE:/dev/nvme0n1===\n"critical_warning": 1\n"media_errors": 0\n'}
        })]),
        {},
    )
    assert bad.observations[0].signal_key == "h200_nvme_health_issue"
    assert bad.observations[0].status == "bad"
    assert bad.observations[0].lifecycle["close_after_healthy"] == 2

    healthy = run_sandboxed(
        H200_NVME_HEALTH_V1,
        _collection("h200_nvme_health", [("h200-a", "node", {
            "outputs": {"nvme_smart": '===DEVICE:/dev/nvme0n1===\n"critical_warning": 0\n"media_errors": 0\n'}
        })]),
        bad.state,
    )
    assert healthy.observations[0].status == "healthy"


def test_ib_link_flapping_repeat_is_condition_signal():
    result = run_sandboxed(
        IB_LINK_FLAPPING_REPEAT_V1,
        _collection("ib_link", [("node-a", "node", {
            "ib_port_physical_state": {"values": [{"metric": {"port": "1", "instance": "node-a"}}]},
            "ib_port_state": {"values": []},
        })]),
        {},
    )
    assert result.observations[0].signal_key == "ib_link_flapping"
    assert result.observations[0].status == "bad"
    assert result.observations[0].lifecycle["open_after_consecutive"] == 2


def test_job_failure_investigation_is_event_and_dedupes_jobs_in_private_state():
    payload = {
        "entries": ["CUDA error: system not yet initialized"],
        "source_jobs": ["job-1"],
    }
    result = run_sandboxed(
        JOB_FAILURE_INVESTIGATION,
        _collection("job_failure", [("node-a", "node", payload)]),
        {},
    )
    assert result.observations[0].signal_key == "job_failure_investigation"
    assert result.observations[0].lifecycle["kind"] == "event"

    second = run_sandboxed(
        JOB_FAILURE_INVESTIGATION,
        _collection("job_failure", [("node-a", "node", payload)]),
        result.state,
    )
    assert second.observations == []


def test_large_job_failure_is_event_for_repeated_root_cause_node():
    targets = []
    for idx in range(3):
        targets.append((f"job-{idx}", "job", {
            "taskRoles": {"worker": {"taskStatuses": [
                {"containerNodeName": "node-a", "taskState": "FAILED", "containerExitCode": 1, "completedTime": 1000},
                {"containerNodeName": "node-b", "taskState": "FAILED", "containerExitCode": -220, "completedTime": 2000},
            ]}}
        }))
    result = run_sandboxed(LARGE_JOB_FAILURE_V1, _collection("large_job_failure", targets), {})
    assert result.observations[0].signal_key == "large_job_failure_root_cause"
    assert result.observations[0].lifecycle["kind"] == "event"


def test_nvidia_ecc_error_is_event_from_ecc_count_or_job_log():
    result = run_sandboxed(
        NVIDIA_ECC_ERROR_V1,
        _collection("nvidia_ecc", [("node-a", "node", {
            "ecc_check": {"ssh_ok": True, "outputs": {"ecc_counts": "0, 3, 1, 2"}},
            "ecc_job_logs": {"entries": [], "source_jobs": []},
        })]),
        {},
    )
    assert result.observations[0].signal_key == "nvidia_ecc_error"
    assert result.observations[0].lifecycle["kind"] == "event"


def test_storage_nvme_health_emits_action_specific_condition_observations():
    result = run_sandboxed(
        STORAGE_NVME_HEALTH_V1,
        _collection("storage_nvme", [("storage-a", "node", {
            "outputs": {"nvme_smart": '===DEVICE:/dev/nvme0n1===\n"critical_warning": 1\n"media_errors": 0\n"percentage_used": 1\n'}
        })]),
        {},
    )
    assert result.observations[0].signal_key == "storage_nvme_critical"
    assert result.observations[0].action == "cordon_node"
    assert result.observations[0].status == "bad"


def test_switch_health_ib_unreachable_and_symbol_errors_are_condition_signals():
    result = run_sandboxed(
        SWITCH_HEALTH_IB_V1,
        _collection("switch_ib", [("sw-a", "switch", {"ssh_ok": False, "ssh_error": "timeout"})]),
        {},
    )
    assert result.observations[0].signal_key == "switch_ib_ssh_unreachable"
    assert result.observations[0].lifecycle["open_after_consecutive"] == 10

    state = {"sw-a": {"prev_symbol_errors": {"IB1/1": 0}}}
    result = run_sandboxed(
        SWITCH_HEALTH_IB_V1,
        _collection("switch_ib", [("sw-a", "switch", {
            "ssh_ok": True,
            "outputs": {"show interfaces ib": "IB1/1 state:\n Symbol errors : 200"},
        })]),
        state,
    )
    assert result.observations[0].signal_key == "switch_ib_symbol_errors"
    assert result.observations[0].lifecycle["open_after_consecutive"] == 3


def test_switch_health_ruijie_tracks_growing_port_errors_as_condition():
    first = run_sandboxed(
        SWITCH_HEALTH_RUIJIE_V1,
        _collection("switch_ruijie", [("sw-a", "switch", {
            "ssh_ok": True,
            "outputs": {"show interfaces counters errors": "Port CRC-Align-Err FCS-Err\nTe1 0 1 0 1"},
        })]),
        {},
    )
    second = run_sandboxed(
        SWITCH_HEALTH_RUIJIE_V1,
        _collection("switch_ruijie", [("sw-a", "switch", {
            "ssh_ok": True,
            "outputs": {"show interfaces counters errors": "Port CRC-Align-Err FCS-Err\nTe1 0 4 0 1"},
        })]),
        first.state,
    )
    assert second.observations[0].signal_key == "switch_ruijie_port_errors"
    assert second.observations[0].lifecycle["open_after_consecutive"] == 2


def test_switch_health_ufm_unreachable_condition_and_reboot_event():
    result = run_sandboxed(
        SWITCH_HEALTH_UFM_V1,
        _collection("switch_ufm", [("ufm-a", "switch", {"ssh_ok": False, "ssh_error": "timeout"})]),
        {},
    )
    assert result.observations[0].signal_key == "switch_ufm_unreachable"
    assert result.observations[0].lifecycle["open_after_consecutive"] == 15

    result = run_sandboxed(
        SWITCH_HEALTH_UFM_V1,
        _collection("switch_ufm", [("ufm-a", "switch", {
            "ssh_ok": True,
            "outputs": {"cat /proc/uptime": "90.0 1000.0"},
        })]),
        result.state,
    )
    assert result.observations[0].signal_key == "switch_ufm_rebooted"
    assert result.observations[0].lifecycle["kind"] == "event"
