"""Agent-evidence DB unit tests — mock _conn, verify SQL + params.

These tests catch:
  - SQL syntax errors (missing commas, wrong column names, bad %s placeholders)
  - Wrong parameter count/order
  - Missing RETURNING clauses
  - Type conversion bugs (datetime, JSONB)
  - Filter logic errors

Run: pytest tests/test_db.py -v
"""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from contextlib import contextmanager


def _make_mock_conn():
    """Create a mock connection with cursor that supports context manager."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


class TestSaveEvidence:
    def test_insert_returns_id(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchone.return_value = (42,)

        with patch("agent_evidence.evidence_db._conn", return_value=mock_conn):
            from agent_evidence.evidence_db import save_evidence
            result = save_evidence(
                node_name="h200-001157",
                source="probe_ssh",
                content="nvidia-smi output here",
                category="gpu",
                summary="ECC errors on GPU 3",
                metadata={"gpu_index": 3, "ecc_count": 42},
                finding_id=7,
            )

        assert result == 42
        mock_conn.commit.assert_called_once()
        sql, params = mock_cursor.execute.call_args.args
        # Verify SQL structure
        assert "INSERT INTO investigation_evidence" in sql
        assert "RETURNING id" in sql
        # Verify param count matches %s placeholders
        assert sql.count("%s") == len(params)
        # Verify param values
        assert params[0] == "h200-001157"  # node_name
        assert params[3] == "gpu"  # category
        assert params[4] == "ECC errors on GPU 3"  # summary
        assert params[7] == 7  # finding_id
        # metadata should be JSON
        parsed_meta = json.loads(params[6])
        assert parsed_meta["gpu_index"] == 3

    def test_insert_without_optional_fields(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchone.return_value = (1,)

        with patch("agent_evidence.evidence_db._conn", return_value=mock_conn):
            from agent_evidence.evidence_db import save_evidence
            result = save_evidence(
                node_name="h200-001157",
                source="dmesg",
                content="kernel panic",
            )

        assert result == 1
        sql, params = mock_cursor.execute.call_args.args
        assert sql.count("%s") == len(params)
        # category and summary should be None
        assert params[3] is None  # category
        assert params[4] is None  # summary
        # metadata should default to {}
        assert json.loads(params[6]) == {}
        # finding_id should be None
        assert params[7] is None


class TestGetNodeEvidence:
    def test_basic_query(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "node_name": "h200-001157", "collected_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
             "collected_by": "repair", "source": "probe_ssh", "category": "gpu",
             "summary": "ECC error", "content": "nvidia-smi", "metadata": "{}"},
        ]

        with patch("agent_evidence.evidence_db._conn", return_value=mock_conn):
            from agent_evidence.evidence_db import get_node_evidence
            result = get_node_evidence("h200-001157")

        assert len(result) == 1
        assert result[0]["node_name"] == "h200-001157"
        assert result[0]["collected_at"] == "2026-01-01T00:00:00+00:00"
        sql, params = mock_cursor.execute.call_args.args
        assert "WHERE node_name = %s" in sql
        assert "ORDER BY collected_at DESC" in sql
        assert "LIMIT %s" in sql
        assert params[0] == "h200-001157"

    def test_filter_by_source(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchall.return_value = []

        with patch("agent_evidence.evidence_db._conn", return_value=mock_conn):
            from agent_evidence.evidence_db import get_node_evidence
            get_node_evidence("h200-001157", source="dmesg")

        sql, params = mock_cursor.execute.call_args.args
        assert "AND source = %s" in sql
        assert "dmesg" in params

    def test_filter_by_category(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchall.return_value = []

        with patch("agent_evidence.evidence_db._conn", return_value=mock_conn):
            from agent_evidence.evidence_db import get_node_evidence
            get_node_evidence("h200-001157", category="gpu")

        sql, params = mock_cursor.execute.call_args.args
        assert "AND category = %s" in sql
        assert "gpu" in params

    def test_empty_result(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchall.return_value = []

        with patch("agent_evidence.evidence_db._conn", return_value=mock_conn):
            from agent_evidence.evidence_db import get_node_evidence
            result = get_node_evidence("nonexistent")

        assert result == []


class TestSearchEvidence:
    def test_basic_search(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchall.return_value = []

        with patch("agent_evidence.evidence_db._conn", return_value=mock_conn):
            from agent_evidence.evidence_db import search_evidence
            search_evidence()

        sql, params = mock_cursor.execute.call_args.args
        assert "FROM investigation_evidence" in sql
        assert "collected_at > NOW()" in sql
        assert "ORDER BY collected_at DESC" in sql

    def test_search_with_all_filters(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchall.return_value = []

        with patch("agent_evidence.evidence_db._conn", return_value=mock_conn):
            from agent_evidence.evidence_db import search_evidence
            search_evidence(
                category="gpu",
                source="nvidia_smi",
                summary_like="ECC",
                collected_by="repair",
                since_days=7,
                limit=50,
            )

        sql, params = mock_cursor.execute.call_args.args
        assert "AND category = %s" in sql
        assert "AND source = %s" in sql
        assert "AND summary ILIKE %s" in sql
        assert "AND collected_by = %s" in sql
        assert sql.count("%s") == len(params)

    def test_since_days_interval_syntax(self):
        """Catch the interval syntax bug: '%s days' vs '%s days'::interval."""
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchall.return_value = []

        with patch("agent_evidence.evidence_db._conn", return_value=mock_conn):
            from agent_evidence.evidence_db import search_evidence
            search_evidence(since_days=30)

        sql, params = mock_cursor.execute.call_args.args
        # Must use parameterized interval, not string concat
        assert "interval '%s days'" in sql or "INTERVAL '%s days'" in sql
        assert params[0] == 30


class TestDeleteNodeEvidence:
    def test_delete_all(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.rowcount = 5

        with patch("agent_evidence.evidence_db._conn", return_value=mock_conn):
            from agent_evidence.evidence_db import delete_node_evidence
            result = delete_node_evidence("h200-001157")

        assert result == 5
        mock_conn.commit.assert_called_once()
        sql, params = mock_cursor.execute.call_args.args
        assert "DELETE FROM investigation_evidence" in sql
        assert "WHERE node_name = %s" in sql
        assert list(params) == ["h200-001157"]

    def test_delete_before_timestamp(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.rowcount = 3

        with patch("agent_evidence.evidence_db._conn", return_value=mock_conn):
            from agent_evidence.evidence_db import delete_node_evidence
            result = delete_node_evidence("h200-001157", before="2026-01-01")

        assert result == 3
        sql, params = mock_cursor.execute.call_args.args
        assert "AND collected_at < %s" in sql
        assert list(params) == ["h200-001157", "2026-01-01"]


class TestGetFindingEvidence:
    def test_by_finding_id(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "node_name": "h200-001157", "collected_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
             "collected_by": "repair", "source": "probe_ssh", "category": "gpu",
             "summary": "ECC", "content": "data", "metadata": "{}"},
        ]

        with patch("agent_evidence.evidence_db._conn", return_value=mock_conn):
            from agent_evidence.evidence_db import get_finding_evidence
            result = get_finding_evidence(finding_id=7)

        assert len(result) == 1
        sql, params = mock_cursor.execute.call_args.args
        assert "WHERE finding_id = %s" in sql
        assert "ORDER BY collected_at DESC" in sql
        assert "LIMIT %s" in sql
        assert params == (7, 100)

    def test_custom_limit(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchall.return_value = []

        with patch("agent_evidence.evidence_db._conn", return_value=mock_conn):
            from agent_evidence.evidence_db import get_finding_evidence
            get_finding_evidence(finding_id=7, limit=50)

        sql, params = mock_cursor.execute.call_args.args
        assert params == (7, 50)


class TestMcpToolWrappers:
    """Verify DB function signatures match what MCP tools expect."""

    def test_save_evidence_params(self):
        import inspect
        from agent_evidence.evidence_db import save_evidence
        sig = inspect.signature(save_evidence)
        params = list(sig.parameters.keys())
        assert "node_name" in params
        assert "source" in params
        assert "content" in params
        assert "category" in params
        assert "summary" in params
        assert "metadata" in params
        assert "finding_id" in params

    def test_get_node_evidence_params(self):
        import inspect
        from agent_evidence.evidence_db import get_node_evidence
        sig = inspect.signature(get_node_evidence)
        params = list(sig.parameters.keys())
        assert "node_name" in params
        assert "source" in params
        assert "category" in params
        assert "collected_by" in params
        assert "limit" in params

    def test_search_evidence_params(self):
        import inspect
        from agent_evidence.evidence_db import search_evidence
        sig = inspect.signature(search_evidence)
        params = list(sig.parameters.keys())
        assert "category" in params
        assert "source" in params
        assert "summary_like" in params
        assert "since_days" in params
        assert "limit" in params
