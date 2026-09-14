"""Tests for patrol_cron collectors — SSH, Prometheus, JobLogs, NodeLogs."""

import unittest.mock as um
import pytest
from patrol_cron.models import CollectionResult, TargetData


class TestSshCollector:
    """SSH collector — generic command execution."""

    def test_runs_commands_on_targets(self):
        from patrol_cron.collectors.ssh import SshCollector

        collector = SshCollector()
        cfg = {
            "name": "switch_health",
            "target_type": "switch",
            "target_filter": {"from_inventory": True},
            "sources": [{"type": "ssh", "name": "check", "config": {
                "commands": ["show version", "show fan"],
                "paging_cmd": "no cli session paging enable",
                "concurrency": 2,
                "user": "admin",
                "password_env": "SWITCH_SSH_PASSWORD",
            }}],
        }

        mock_result = {"ssh_ok": True, "outputs": {"show version": "v3.11", "show fan": "ok"}, "ssh_error": ""}
        with um.patch.object(SshCollector, "_resolve_targets",
                             return_value=[("sw1", "1.1.1.1", {}), ("sw2", "2.2.2.2", {})]):
            with um.patch("patrol_cron.collectors.ssh._ssh_run_commands", return_value=mock_result):
                result = collector.collect(cfg)

        assert isinstance(result, CollectionResult)
        assert len(result.targets) == 2
        assert result.targets[0].type == "switch"
        assert result.targets[0].payload["ssh_ok"] is True
        assert "show version" in result.targets[0].payload["outputs"]

    def test_no_targets_returns_error(self):
        from patrol_cron.collectors.ssh import SshCollector

        collector = SshCollector()
        cfg = {
            "name": "empty",
            "target_type": "switch",
            "target_filter": {"from_inventory": True},
            "sources": [{"type": "ssh", "name": "check", "config": {"commands": ["echo"]}}],
        }

        with um.patch.object(SshCollector, "_resolve_targets", return_value=[]):
            result = collector.collect(cfg)

        assert "No targets resolved" in result.errors[0]

    def test_no_commands_returns_error(self):
        from patrol_cron.collectors.ssh import SshCollector

        collector = SshCollector()
        cfg = {
            "name": "bad",
            "target_type": "node",
            "target_filter": {},
            "sources": [{"type": "ssh", "name": "x", "config": {}}],
        }

        result = collector.collect(cfg)
        assert "No commands specified" in result.errors[0]

    def test_ssh_failure_captured_in_payload(self):
        from patrol_cron.collectors.ssh import SshCollector

        collector = SshCollector()
        cfg = {
            "name": "fm_check",
            "target_type": "node",
            "target_filter": {},
            "sources": [{"type": "ssh", "name": "fm", "config": {
                "commands": ["systemctl is-active nv-fabricmanager"],
                "concurrency": 2,
            }}],
        }

        mock_result = {"ssh_ok": False, "outputs": {}, "ssh_error": "Connection timed out"}
        with um.patch.object(SshCollector, "_resolve_targets",
                             return_value=[("h200-1", "10.0.0.1", {})]):
            with um.patch("patrol_cron.collectors.ssh._ssh_run_commands", return_value=mock_result):
                result = collector.collect(cfg)

        assert len(result.targets) == 1
        assert result.targets[0].payload["ssh_ok"] is False
        assert "timed out" in result.targets[0].payload["ssh_error"]


class TestPrometheusCollector:
    """Prometheus collector."""

    def test_collect_returns_targets_from_series(self):
        from patrol_cron.collectors.prometheus import PrometheusCollector

        collector = PrometheusCollector()
        cfg = {
            "name": "nvidia_ecc",
            "target_type": "node",
            "schedule_sec": 60,
            "sources": [{"type": "prometheus", "name": "ecc", "config": {
                "query": 'nvidiasmi_ecc_error_count{type="double"} > 0',
                "step": "15s",
            }}],
        }

        mock_data = {
            "result": [
                {"metric": {"node_name": "gpu-01", "instance": "10.0.0.1:9400"}, "values": [[1, "1"]]},
                {"metric": {"node_name": "gpu-02", "instance": "10.0.0.2:9400"}, "values": [[1, "3"]]},
            ]
        }

        with um.patch.object(PrometheusCollector, "_get_client") as mock_client_fn:
            mock_client = um.MagicMock()
            mock_client.query_range.return_value = mock_data
            mock_client_fn.return_value = mock_client
            result = collector.collect(cfg)

        assert len(result.targets) == 2
        assert result.targets[0].id == "gpu-01"
        assert result.targets[0].type == "node"

    def test_collect_handles_none_response(self):
        from patrol_cron.collectors.prometheus import PrometheusCollector

        collector = PrometheusCollector()
        cfg = {
            "name": "test",
            "target_type": "node",
            "schedule_sec": 60,
            "sources": [{"type": "prometheus", "name": "q", "config": {"query": "up"}}],
        }

        with um.patch.object(PrometheusCollector, "_get_client") as mock_client_fn:
            mock_client = um.MagicMock()
            mock_client.query_range.return_value = None
            mock_client_fn.return_value = mock_client
            result = collector.collect(cfg)

        assert len(result.targets) == 0
        assert len(result.errors) == 1


class TestJobLogsCollector:
    """Job logs collector."""

    def test_collect_returns_per_node_targets(self):
        from patrol_cron.collectors.job_logs import JobLogsCollector

        collector = JobLogsCollector()
        cfg = {
            "name": "job_failure_logs",
            "target_type": "job",
            "schedule_sec": 60,
            "target_filter": {"status": ["failed"]},
            "sources": [{"type": "job_logs", "name": "logs", "config": {
                "patterns": [{"regex": ".*NCCL.*error.*"}],
                "max_entries": 50,
            }}],
        }

        mock_jobs = {
            "user1~job1~0": {
                "username": "user1", "name": "job1",
                "frameworkName": "fw1", "state": "FAILED",
                "nodes": {"node-1": {"container_ip": "10.0.0.1", "container_id": "c1"}},
            }
        }
        mock_entry = um.MagicMock()
        mock_entry.message = "NCCL error in job"
        mock_entry.fields = {}

        with um.patch("patrol_cron.collectors.job_logs.JobMetadataClient") as MockMeta:
            mock_meta = MockMeta.return_value
            mock_meta.get_job_metadata.return_value = mock_jobs
            mock_meta.get_filtered_job_attempts.return_value = mock_jobs

            with um.patch("patrol_cron.collectors.job_logs.JobLogsClient") as MockLogs:
                mock_log_client = MockLogs.return_value
                mock_log_client.get_job_logs.return_value = {"node-1": "Some NCCL error happened"}
                mock_log_client.parse_logs_with_patterns.return_value = [mock_entry]

                result = collector.collect(cfg)

        assert isinstance(result, CollectionResult)
        assert len(result.targets) == 1
        assert result.targets[0].type == "job"


class TestNodeLogsCollector:
    """Node logs collector."""

    def test_collect_returns_per_node_targets(self):
        from patrol_cron.collectors.node_logs import NodeLogsCollector

        collector = NodeLogsCollector()
        cfg = {
            "name": "dmesg_errors",
            "target_type": "node",
            "target_filter": {"schedulable": True},
            "sources": [{"type": "node_logs", "name": "dmesg", "config": {
                "log_paths": ["/var/log/dmesg"],
                "patterns": [{"regex": ".*GPU.*Xid.*"}],
                "max_entries": 100,
            }}],
        }

        mock_entry = um.MagicMock()
        mock_entry.message = "GPU Xid error 79"
        mock_entry.fields = {}

        with um.patch("patrol_cron.collectors.node_logs.NodeLogsCollector._resolve_nodes",
                       return_value=[("gpu-01", "10.0.0.1")]):
            with um.patch("patrol_cron.collectors.node_logs.NodeLogsClient") as MockClient:
                mock_client = MockClient.return_value
                mock_client.collect_node_logs.return_value = [mock_entry]

                result = collector.collect(cfg)

        assert isinstance(result, CollectionResult)
        assert len(result.targets) == 1
        assert result.targets[0].id == "gpu-01"
        assert result.targets[0].type == "node"
