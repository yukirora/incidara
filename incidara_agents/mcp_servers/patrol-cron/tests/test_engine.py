"""Tests for patrol_cron.engine — collector dispatch."""

import unittest.mock as um
import pytest
from patrol_cron.models import CollectionResult, TargetData
from patrol_cron.engine import run_collector


class TestRunCollector:
    def test_ssh_collector_dispatched(self):
        mock_ssh = um.MagicMock()
        mock_ssh.collect.return_value = CollectionResult(
            collector_name="test", targets=[TargetData(id="sw1", type="switch", payload={})]
        )

        with um.patch("patrol_cron.engine._get_collectors", return_value={"ssh": mock_ssh}):
            result = run_collector({
                "name": "switch_health",
                "sources": [{"type": "ssh", "name": "check", "config": {}}],
            })

        assert len(result.targets) == 1
        mock_ssh.collect.assert_called_once()

    def test_prometheus_collector_dispatched(self):
        mock_prom = um.MagicMock()
        mock_prom.collect.return_value = CollectionResult(collector_name="test")

        with um.patch("patrol_cron.engine._get_collectors", return_value={"prometheus": mock_prom}):
            result = run_collector({
                "name": "ecc_check",
                "sources": [{"type": "prometheus", "name": "ecc", "config": {}}],
            })

        mock_prom.collect.assert_called_once()

    def test_unknown_source_returns_error(self):
        with um.patch("patrol_cron.engine._get_collectors", return_value={"ssh": um.MagicMock()}):
            result = run_collector({
                "name": "bad",
                "sources": [{"type": "kafka", "name": "x", "config": {}}],
            })
        assert len(result.errors) == 1
        assert "Unknown source type" in result.errors[0]

    def test_no_sources_returns_error(self):
        result = run_collector({"name": "empty", "sources": []})
        assert "No sources configured" in result.errors[0]

    def test_multi_source_merges_by_target_id(self):
        """Two sources returning overlapping targets → payload keyed by source name."""
        mock_ssh = um.MagicMock()
        mock_ssh.collect.return_value = CollectionResult(
            collector_name="test",
            targets=[
                TargetData(id="node-1", type="node", payload={"outputs": {"cmd": "ok"}}),
                TargetData(id="node-2", type="node", payload={"outputs": {"cmd": "fail"}}),
            ],
        )
        mock_prom = um.MagicMock()
        mock_prom.collect.return_value = CollectionResult(
            collector_name="test",
            targets=[
                TargetData(id="node-1", type="node", payload={"metric": "ecc", "values": [1]}),
                TargetData(id="node-3", type="node", payload={"metric": "ecc", "values": [5]}),
            ],
        )

        with um.patch("patrol_cron.engine._get_collectors",
                       return_value={"ssh": mock_ssh, "prometheus": mock_prom}):
            result = run_collector({
                "name": "multi_test",
                "sources": [
                    {"type": "ssh", "name": "gpu_check", "config": {}},
                    {"type": "prometheus", "name": "ecc", "config": {}},
                ],
            })

        assert len(result.targets) == 3  # node-1, node-2, node-3
        by_id = {t.id: t for t in result.targets}

        # node-1 has both sources
        assert "gpu_check" in by_id["node-1"].payload
        assert "ecc" in by_id["node-1"].payload
        assert by_id["node-1"].payload["gpu_check"]["outputs"]["cmd"] == "ok"
        assert by_id["node-1"].payload["ecc"]["values"] == [1]

        # node-2 has only ssh
        assert "gpu_check" in by_id["node-2"].payload
        assert "ecc" not in by_id["node-2"].payload

        # node-3 has only prometheus
        assert "ecc" in by_id["node-3"].payload
        assert "gpu_check" not in by_id["node-3"].payload

    def test_multi_source_one_fails(self):
        """If one source fails, others still run."""
        mock_ssh = um.MagicMock()
        mock_ssh.collect.return_value = CollectionResult(
            collector_name="test",
            targets=[TargetData(id="n1", type="node", payload={"ok": True})],
        )

        with um.patch("patrol_cron.engine._get_collectors",
                       return_value={"ssh": mock_ssh}):
            result = run_collector({
                "name": "multi_test",
                "sources": [
                    {"type": "ssh", "name": "check", "config": {}},
                    {"type": "nonexistent", "name": "bad", "config": {}},
                ],
            })

        assert len(result.targets) == 1  # ssh still worked
        assert any("Unknown source type" in e for e in result.errors)
