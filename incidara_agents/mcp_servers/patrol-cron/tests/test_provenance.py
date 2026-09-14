"""Tests for hash-based provenance: rule_code_hash, collector_config_hash,
from_old_code, current_code_only, and _compute_collector_hash consistency."""

import json
import hashlib
import unittest.mock as um

import pytest


class TestComputeCollectorHash:
    """Verify _compute_collector_hash produces deterministic, consistent hashes."""

    def test_deterministic(self):
        from patrol_cron.mcp_tools import _compute_collector_hash

        h1 = _compute_collector_hash(
            "gpu_health", [{"type": "ssh"}], {"category": "b300"}, 300, True
        )
        h2 = _compute_collector_hash(
            "gpu_health", [{"type": "ssh"}], {"category": "b300"}, 300, True
        )
        assert h1 == h2

    def test_matches_run_collector_job_computation(self):
        """The hash must match what run_collector_job.py computes."""
        from patrol_cron.mcp_tools import _compute_collector_hash

        name = "nvidia_ecc_health"
        sources = [{"name": "ecc_check", "type": "ssh", "config": {"timeout": 15}}]
        target_filter = {"category": "b300"}
        schedule_sec = 300
        enabled = True

        # Same logic as run_collector_job.py
        import json as _json
        raw = (
            str(name or "")
            + _json.dumps(sources or [], sort_keys=True)
            + _json.dumps(target_filter or {}, sort_keys=True)
            + str(schedule_sec or "")
            + str(enabled or "")
        )
        expected = hashlib.md5(raw.encode()).hexdigest()

        actual = _compute_collector_hash(name, sources, target_filter, schedule_sec, enabled)
        assert actual == expected

    def test_different_sources_different_hash(self):
        from patrol_cron.mcp_tools import _compute_collector_hash

        h1 = _compute_collector_hash("c1", [{"type": "ssh"}], {}, 300, True)
        h2 = _compute_collector_hash("c1", [{"type": "job_logs"}], {}, 300, True)
        assert h1 != h2

    def test_different_target_filter_different_hash(self):
        from patrol_cron.mcp_tools import _compute_collector_hash

        h1 = _compute_collector_hash("c1", [], {"category": "b300"}, 300, True)
        h2 = _compute_collector_hash("c1", [], {"category": "h200"}, 300, True)
        assert h1 != h2

    def test_different_schedule_different_hash(self):
        from patrol_cron.mcp_tools import _compute_collector_hash

        h1 = _compute_collector_hash("c1", [], {}, 300, True)
        h2 = _compute_collector_hash("c1", [], {}, 600, True)
        assert h1 != h2

    def test_different_enabled_different_hash(self):
        from patrol_cron.mcp_tools import _compute_collector_hash

        h1 = _compute_collector_hash("c1", [], {}, 300, True)
        h2 = _compute_collector_hash("c1", [], {}, 300, False)
        assert h1 != h2

    def test_none_defaults(self):
        from patrol_cron.mcp_tools import _compute_collector_hash

        h1 = _compute_collector_hash(None, None, None, None, None)
        h2 = _compute_collector_hash("", [], {}, "", "")
        assert h1 == h2


class TestCreateFindingProvenance:
    """Verify create_finding stamps rule_code_hash and collector_config_hash."""

    def test_manual_finding_has_no_hashes(self):
        from patrol_cron.mcp_tools import create_finding

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.side_effect = [None, (42,)]

            create_finding(
                rule_id="manual", target_id="n1", target_type="node",
                severity="info", action="log_only", evidence={"x": 1},
            )

        # Insert SQL and params
        insert_sql, insert_params = mock_cursor.execute.call_args_list[1].args
        assert "rule_code_hash" in insert_sql
        assert "collector_config_hash" in insert_sql
        # manual findings get None hashes (no lookup query for manual)
        assert insert_params[-2] is None  # rule_code_hash
        assert insert_params[-1] is None  # collector_config_hash

    def test_real_rule_stamps_both_hashes(self):
        from patrol_cron.mcp_tools import create_finding, _compute_collector_hash

        rule_hash = hashlib.md5(b"def analyze(c, s): return [], s").hexdigest()
        # The actual hash that _compute_collector_hash would produce
        coll_data = ("gpu_health", [{"type": "ssh"}], {}, 300, True)
        expected_coll_hash = _compute_collector_hash(*coll_data)

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.side_effect = [
                None,  # dedup check
                (rule_hash, "gpu_health"),  # rule hash + binds_to
                coll_data,  # collector row
                (42,),  # RETURNING finding_id
            ]

            result = create_finding(
                rule_id="gpu_rule_v1", target_id="n1", target_type="node",
                severity="info", action="log_only", evidence={"x": 1},
            )

        data = json.loads(result)
        assert data["status"] == "created"
        insert_sql, insert_params = mock_cursor.execute.call_args_list[3].args
        assert "rule_code_hash" in insert_sql
        assert "collector_config_hash" in insert_sql
        assert insert_params[-2] == rule_hash
        assert insert_params[-1] == expected_coll_hash

    def test_real_rule_with_no_binds_to_stamps_only_rule_hash(self):
        from patrol_cron.mcp_tools import create_finding

        rule_hash = hashlib.md5(b"code").hexdigest()

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.side_effect = [
                None,  # dedup check
                (rule_hash, None),  # rule hash + no binds_to
                (42,),  # RETURNING finding_id
            ]

            create_finding(
                rule_id="orphan_rule_v1", target_id="n1", target_type="node",
                severity="info", action="log_only",
            )

        insert_sql, insert_params = mock_cursor.execute.call_args_list[2].args
        assert insert_params[-2] == rule_hash
        assert insert_params[-1] is None  # no collector hash


class TestListFindingsFromOldCode:
    """Verify from_old_code reflects both rule AND collector changes."""

    def test_both_current_from_old_code_false(self):
        from patrol_cron.mcp_tools import list_findings

        rule_hash = "aaa"
        coll_hash = "bbb"
        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchall.side_effect = [
                # findings
                [{"finding_id": 1, "rule_id": "r1", "target_id": "n1",
                  "severity": "critical", "action": "cordon_node",
                  "evidence": {}, "verdict": None, "resolved": False,
                  "detected_at": "2025-01-01", "rule_code_hash": rule_hash,
                  "collector_config_hash": coll_hash,
                  "stage": "log_only", "rule_code_updated_at": "2025-01-01",
                  "binds_to": "c1", "current_rule_code_hash": rule_hash}],
                # collector_versions
                [{"name": "c1", "config_hash": coll_hash}],
            ]

            result = list_findings(rule_id="r1")

        data = json.loads(result)
        assert data["findings"][0]["from_old_code"] is False

    def test_rule_changed_from_old_code_true(self):
        from patrol_cron.mcp_tools import list_findings

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchall.side_effect = [
                [{"finding_id": 1, "rule_id": "r1", "target_id": "n1",
                  "severity": "critical", "action": "cordon_node",
                  "evidence": {}, "verdict": None, "resolved": False,
                  "detected_at": "2025-01-01", "rule_code_hash": "old_hash",
                  "collector_config_hash": "same_coll",
                  "stage": "log_only", "rule_code_updated_at": "2025-01-01",
                  "binds_to": "c1", "current_rule_code_hash": "new_hash"}],
                [{"name": "c1", "config_hash": "same_coll"}],
            ]

            result = list_findings(rule_id="r1")

        data = json.loads(result)
        assert data["findings"][0]["from_old_code"] is True

    def test_collector_changed_from_old_code_true(self):
        """Key test: rule unchanged but collector changed → from_old_code=True."""
        from patrol_cron.mcp_tools import list_findings

        rule_hash = "same_rule_hash"
        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchall.side_effect = [
                [{"finding_id": 1, "rule_id": "r1", "target_id": "n1",
                  "severity": "critical", "action": "cordon_node",
                  "evidence": {}, "verdict": None, "resolved": False,
                  "detected_at": "2025-01-01", "rule_code_hash": rule_hash,
                  "collector_config_hash": "old_coll_hash",
                  "stage": "log_only", "rule_code_updated_at": "2025-01-01",
                  "binds_to": "c1", "current_rule_code_hash": rule_hash}],
                [{"name": "c1", "config_hash": "new_coll_hash"}],
            ]

            result = list_findings(rule_id="r1")

        data = json.loads(result)
        assert data["findings"][0]["from_old_code"] is True

    def test_no_hashes_from_old_code_true(self):
        """Findings before provenance was added: from_old_code=True."""
        from patrol_cron.mcp_tools import list_findings

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchall.side_effect = [
                [{"finding_id": 1, "rule_id": "r1", "target_id": "n1",
                  "severity": "critical", "action": "cordon_node",
                  "evidence": {}, "verdict": None, "resolved": False,
                  "detected_at": "2025-01-01", "rule_code_hash": None,
                  "collector_config_hash": None,
                  "stage": "log_only", "rule_code_updated_at": "2025-01-01",
                  "binds_to": "c1", "current_rule_code_hash": "some_hash"}],
                [],
            ]

            result = list_findings(rule_id="r1")

        data = json.loads(result)
        assert data["findings"][0]["from_old_code"] is True


class TestGetFindingRawDataFromOldCode:
    """Verify get_finding_raw_data provenance checks both rule and collector."""

    def _mock_finding_raw_data(self, finding_row, rule_row, coll_version_row):
        """Helper to mock get_finding_raw_data with correct fetchone sequence."""
        # fetchone sequence:
        # 1. finding row
        # 2. rule current_hash + binds_to
        # 3. collector_versions latest config_hash
        # 4. patrol_rule_versions (rule_code_at_creation) - None
        # 5. collector_versions (collector_config_at_creation) - None
        side_effects = [
            finding_row,
            rule_row,
            coll_version_row,
            None,  # patrol_rule_versions lookup
            None,  # collector_versions by config_hash lookup
        ]
        return side_effects

    def test_rule_changed_shows_old(self):
        from patrol_cron.mcp_tools import get_finding_raw_data

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.side_effect = self._mock_finding_raw_data(
                {"finding_id": 1, "rule_id": "r1", "target_id": "n1",
                 "evidence": {}, "collector_snapshot_id": None,
                 "rule_code_hash": "old_rule", "collector_config_hash": "same_coll"},
                {"current_hash": "new_rule", "binds_to": "c1"},
                {"config_hash": "same_coll"},
            )
            mock_cursor.fetchall.return_value = []

            result = get_finding_raw_data(1)

        data = json.loads(result)
        assert data["provenance"]["from_old_code"] is True

    def test_collector_changed_shows_old(self):
        from patrol_cron.mcp_tools import get_finding_raw_data

        rule_hash = "same_rule"
        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.side_effect = self._mock_finding_raw_data(
                {"finding_id": 1, "rule_id": "r1", "target_id": "n1",
                 "evidence": {}, "collector_snapshot_id": None,
                 "rule_code_hash": rule_hash, "collector_config_hash": "old_coll"},
                {"current_hash": rule_hash, "binds_to": "c1"},
                {"config_hash": "new_coll"},
            )
            mock_cursor.fetchall.return_value = []

            result = get_finding_raw_data(1)

        data = json.loads(result)
        assert data["provenance"]["from_old_code"] is True

    def test_both_current_shows_false(self):
        from patrol_cron.mcp_tools import get_finding_raw_data

        rule_hash = "same_rule"
        coll_hash = "same_coll"
        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.side_effect = self._mock_finding_raw_data(
                {"finding_id": 1, "rule_id": "r1", "target_id": "n1",
                 "evidence": {}, "collector_snapshot_id": None,
                 "rule_code_hash": rule_hash, "collector_config_hash": coll_hash},
                {"current_hash": rule_hash, "binds_to": "c1"},
                {"config_hash": coll_hash},
            )
            mock_cursor.fetchall.return_value = []

            result = get_finding_raw_data(1)

        data = json.loads(result)
        assert data["provenance"]["from_old_code"] is False

    def test_no_hashes_shows_old(self):
        from patrol_cron.mcp_tools import get_finding_raw_data

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "finding_id": 1, "rule_id": "r1", "target_id": "n1",
                "evidence": {}, "collector_snapshot_id": None,
                "rule_code_hash": None, "collector_config_hash": None,
            }
            mock_cursor.fetchall.return_value = []

            result = get_finding_raw_data(1)

        data = json.loads(result)
        assert data["provenance"]["from_old_code"] is True


class TestUpdateCollectorArchivesHash:
    """Verify update_collector archives current version with consistent hash."""

    def test_archives_before_update(self):
        from patrol_cron.mcp_tools import update_collector

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "name": "c1", "sources": [{"type": "ssh"}],
                "target_filter": {}, "schedule_sec": 300, "enabled": True,
            }
            mock_cursor.rowcount = 1

            update_collector(name="c1", schedule_sec=600)

        # execute calls: 0=SELECT current, 1=INSERT archive, 2=UPDATE
        calls = mock_cursor.execute.call_args_list
        assert len(calls) == 3
        select_sql, _ = calls[0].args
        archive_sql, archive_params = calls[1].args
        assert "SELECT" in select_sql
        assert "INSERT INTO collector_versions" in archive_sql
        assert archive_params[-1] == "update_collector"

    def test_hash_computed_in_python_not_sql(self):
        from patrol_cron.mcp_tools import update_collector, _compute_collector_hash

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "name": "c1", "sources": [{"type": "ssh"}],
                "target_filter": {"category": "b300"},
                "schedule_sec": 300, "enabled": True,
            }
            mock_cursor.rowcount = 1

            update_collector(name="c1", schedule_sec=600)

        # execute calls: 0=SELECT, 1=INSERT archive, 2=UPDATE
        archive_sql, archive_params = mock_cursor.execute.call_args_list[1].args
        expected = _compute_collector_hash(
            "c1", [{"type": "ssh"}], {"category": "b300"}, 300, True
        )
        assert archive_params[0] == expected

    def test_not_found_returns_error(self):
        from patrol_cron.mcp_tools import update_collector

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = None

            result = update_collector(name="nonexistent", schedule_sec=600)

        data = json.loads(result)
        assert data["ok"] is False
        assert data["reason"] == "not_found"
        assert data["entity"] == "Collector"
        assert data["id"] == "nonexistent"


class TestGetRuleAccuracySinceCodeUpdate:
    """Verify get_rule_accuracy with since_code_update filters both hashes."""

    def test_sql_includes_collector_hash_filter(self):
        from patrol_cron.mcp_tools import get_rule_accuracy

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "total": 5, "confirmed": 3, "rejected": 1, "judged": 4,
            }

            result = get_rule_accuracy("r1", since_code_update=True)

        data = json.loads(result)
        assert data["mode"] == "since_code_update"
        sql, params = mock_cursor.execute.call_args.args
        # Must include both rule hash and collector hash filters
        assert "rule_code_hash" in sql
        assert "collector_config_hash" in sql
        assert "collector_versions" in sql

    def test_window_mode_no_hash_filter(self):
        from patrol_cron.mcp_tools import get_rule_accuracy

        with um.patch("patrol_cron.mcp_tools._conn") as mock_conn_fn:
            mock_conn = um.MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_conn.__enter__ = um.MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = um.MagicMock(return_value=False)
            mock_cursor = um.MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "total": 10, "confirmed": 8, "rejected": 2, "judged": 10,
            }

            result = get_rule_accuracy("r1", since_code_update=False, window_days=7)

        data = json.loads(result)
        assert data["mode"] == "window_7d"
        sql, _ = mock_cursor.execute.call_args.args
        # Window mode should NOT filter by hashes
        assert "rule_code_hash" not in sql
        assert "collector_config_hash" not in sql
