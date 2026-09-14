import importlib.util
import json
import sys
import types
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest


AGENT_FEEDBACK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_FEEDBACK_ROOT))

from agent_feedback import knowledge_db  # noqa: E402


VALID_REPAIR_OUTCOMES = (
    "REPAIR_CONFIRMED",
    "NO_FAULT_FOUND",
    "MISCLASSIFIED",
    "MAINTENANCE_FIX",
    "CONFIG_TASK",
)


class FakeCursor:
    def __init__(self, rowcount=1, rows=None, one=None):
        self.executed = []
        self.rowcount = rowcount
        self.rows = rows or []
        self.one = one

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.commits = 0

    def cursor(self, *args, **kwargs):
        return self.cursor_obj

    def commit(self):
        self.commits += 1


def install_fake_conn(monkeypatch, cursor):
    conn = FakeConnection(cursor)

    @contextmanager
    def fake_conn():
        yield conn

    monkeypatch.setattr(knowledge_db, "_conn", fake_conn)
    return conn


def _find_mcp_server_path() -> Path:
    """Find mcp_server.py — works both from repo checkout and inside container."""
    # Try relative to test file first (repo layout)
    repo_path = Path(__file__).resolve().parents[1] / "mcp_server.py"
    if repo_path.exists():
        return repo_path
    # Inside container: installed at /opt/agent-feedback/
    container_path = Path("/opt/agent-feedback/mcp_server.py")
    if container_path.exists():
        return container_path
    raise FileNotFoundError(f"mcp_server.py not found at {repo_path} or {container_path}")


def load_mcp_server(monkeypatch, role="feedback"):
    monkeypatch.setenv("AGENT_FEEDBACK_ROLE", role)
    monkeypatch.setattr(knowledge_db, "ensure_table", lambda: None)
    monkeypatch.setattr(knowledge_db, "ensure_analysis_problems_table", lambda: None)
    monkeypatch.setattr(knowledge_db, "ensure_rejected_proposals_table", lambda: None)
    monkeypatch.setattr(knowledge_db, "ensure_agent_memory_table", lambda: None)

    module_name = "agent_feedback_test_mcp_server"
    # Remove any cached module to force re-import
    if module_name in sys.modules:
        del sys.modules[module_name]

    mcp_path = _find_mcp_server_path()
    spec = importlib.util.spec_from_file_location(module_name, mcp_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("repair_outcome", VALID_REPAIR_OUTCOMES)
def test_insert_writes_all_valid_repair_outcomes(monkeypatch, repair_outcome):
    cursor = FakeCursor()
    conn = install_fake_conn(monkeypatch, cursor)

    ok = knowledge_db.insert_rma_finding_reconciliation(
        case_id=11,
        finding_id=22,
        rule_id="rule.gpu.ecc",
        repair_outcome=repair_outcome,
    )

    assert ok is True
    assert conn.commits == 1
    assert cursor.executed[0][1] == (
        11,
        22,
        "rule.gpu.ecc",
        repair_outcome,
        knowledge_db._default_feedback_label(repair_outcome),
        json.dumps(knowledge_db._default_expected_behavior(
            knowledge_db._default_feedback_label(repair_outcome)
        )),
        "auto",
    )


def test_insert_rejects_invalid_repair_outcome_before_db_write(monkeypatch):
    called = False

    @contextmanager
    def fail_if_called():
        nonlocal called
        called = True
        raise AssertionError("DB should not be opened")
        yield

    monkeypatch.setattr(knowledge_db, "_conn", fail_if_called)

    with pytest.raises(ValueError):
        knowledge_db.insert_rma_finding_reconciliation(
            case_id=11,
            finding_id=22,
            rule_id="rule.gpu.ecc",
            repair_outcome="INVALID",
        )

    assert called is False


def test_update_attribution_returns_true_when_rowcount_one(monkeypatch):
    cursor = FakeCursor(rowcount=1, one=("NO_FAULT_FOUND", "negative", {"should_fire": False}))
    conn = install_fake_conn(monkeypatch, cursor)

    ok = knowledge_db.update_rma_finding_attribution(
        case_id=11,
        finding_id=22,
        attribution="detection",
        attribution_confidence="high",
        fix_route="rule_code",
    )

    assert ok is True
    assert conn.commits == 1


def test_update_attribution_returns_false_when_rowcount_zero(monkeypatch):
    cursor = FakeCursor(rowcount=0, one=None)
    install_fake_conn(monkeypatch, cursor)

    ok = knowledge_db.update_rma_finding_attribution(
        case_id=11,
        finding_id=22,
        attribution="repair",
        attribution_confidence="low",
        fix_route="repair_skill",
    )

    assert ok is False


def test_update_rejects_invalid_attribution_and_fix_route_before_db_write(monkeypatch):
    called = False

    @contextmanager
    def fail_if_called():
        nonlocal called
        called = True
        raise AssertionError("DB should not be opened")
        yield

    monkeypatch.setattr(knowledge_db, "_conn", fail_if_called)

    with pytest.raises(ValueError):
        knowledge_db.update_rma_finding_attribution(
            case_id=11,
            finding_id=22,
            attribution="bad_attribution",
            attribution_confidence="high",
            fix_route="rule_code",
        )
    with pytest.raises(ValueError):
        knowledge_db.update_rma_finding_attribution(
            case_id=11,
            finding_id=22,
            attribution="detection",
            attribution_confidence="high",
            fix_route="bad_route",
        )

    assert called is False


def test_update_rejects_invalid_attribution_confidence_before_db_write(monkeypatch):
    called = False

    @contextmanager
    def fail_if_called():
        nonlocal called
        called = True
        raise AssertionError("DB should not be opened")
        yield

    monkeypatch.setattr(knowledge_db, "_conn", fail_if_called)

    with pytest.raises(ValueError):
        knowledge_db.update_rma_finding_attribution(
            case_id=11,
            finding_id=22,
            attribution="detection",
            attribution_confidence="certain",
            fix_route="rule_code",
        )

    assert called is False


def test_insert_on_conflict_does_not_null_existing_attribution_fields(monkeypatch):
    cursor = FakeCursor()
    install_fake_conn(monkeypatch, cursor)

    knowledge_db.insert_rma_finding_reconciliation(
        case_id=11,
        finding_id=22,
        rule_id="rule.gpu.ecc",
        repair_outcome="NO_FAULT_FOUND",
    )

    sql = cursor.executed[0][0]
    conflict_update = sql.split("DO UPDATE SET", 1)[1]
    assert "rule_id" in conflict_update
    assert "repair_outcome" in conflict_update
    assert "attribution =" not in conflict_update
    assert "attribution_confidence" not in conflict_update
    assert "fix_route" not in conflict_update


def test_insert_labels_nff_as_negative_by_default(monkeypatch):
    cursor = FakeCursor()
    install_fake_conn(monkeypatch, cursor)

    knowledge_db.insert_rma_finding_reconciliation(
        case_id=11,
        finding_id=22,
        rule_id="rule.gpu.ecc",
        repair_outcome="NO_FAULT_FOUND",
    )

    assert cursor.executed[0][1][4:] == (
        "negative",
        '{"should_fire": false}',
        "auto",
    )


def test_update_non_detection_attribution_marks_example_unknown(monkeypatch):
    cursor = FakeCursor(rowcount=1, one=("NO_FAULT_FOUND", "negative", {"should_fire": False}))
    install_fake_conn(monkeypatch, cursor)

    ok = knowledge_db.update_rma_finding_attribution(
        case_id=11,
        finding_id=22,
        attribution="repair",
        attribution_confidence="low",
        fix_route="repair_skill",
    )

    assert ok is True
    assert cursor.executed[-1][1][3:6] == (
        "unknown",
        '{"should_fire": false}',
        "case_diagnosis",
    )


def test_mcp_insert_rma_finding_reconciliation_success(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch, role="feedback")
    calls = []

    def fake_insert(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(mcp_server, "_insert_rma_finding_reconciliation", fake_insert)

    response = mcp_server.insert_rma_finding_reconciliation(
        case_id=11,
        finding_id=22,
        rule_id="rule.gpu.ecc",
        repair_outcome="REPAIR_CONFIRMED",
    )

    assert json.loads(response) == {"inserted": True}
    assert calls == [{
        "case_id": 11,
        "finding_id": 22,
        "rule_id": "rule.gpu.ecc",
        "repair_outcome": "REPAIR_CONFIRMED",
        "feedback_label": "",
        "expected_behavior": None,
        "label_source": "auto",
    }]


def test_mcp_insert_rma_finding_reconciliation_error(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch, role="feedback")

    def fake_insert(**kwargs):
        raise ValueError("invalid repair outcome")

    monkeypatch.setattr(mcp_server, "_insert_rma_finding_reconciliation", fake_insert)

    with pytest.raises(RuntimeError, match="invalid repair outcome"):
        mcp_server.insert_rma_finding_reconciliation(
            case_id=11,
            finding_id=22,
            rule_id="rule.gpu.ecc",
            repair_outcome="BAD",
        )


def test_mcp_update_rma_finding_attribution_success(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch, role="feedback")
    calls = []

    def fake_update(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(mcp_server, "_update_rma_finding_attribution", fake_update)

    response = mcp_server.update_rma_finding_attribution(
        case_id=11,
        finding_id=22,
        attribution="detection",
        attribution_confidence="high",
        fix_route="rule_code",
    )

    assert json.loads(response) == {"updated": True}
    assert calls == [{
        "case_id": 11,
        "finding_id": 22,
        "attribution": "detection",
        "attribution_confidence": "high",
        "fix_route": "rule_code",
        "feedback_label": "",
        "expected_behavior": None,
        "label_source": "case_diagnosis",
    }]


def test_mcp_update_rma_finding_attribution_error(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch, role="feedback")

    def fake_update(**kwargs):
        raise ValueError("invalid attribution")

    monkeypatch.setattr(mcp_server, "_update_rma_finding_attribution", fake_update)

    with pytest.raises(RuntimeError, match="invalid attribution"):
        mcp_server.update_rma_finding_attribution(
            case_id=11,
            finding_id=22,
            attribution="bad",
            attribution_confidence="high",
            fix_route="rule_code",
        )


def test_ensure_table_adds_feedback_label_columns(monkeypatch):
    cursor = FakeCursor()
    conn = install_fake_conn(monkeypatch, cursor)

    knowledge_db.ensure_table()

    executed_sql = "\n".join(sql for sql, _params in cursor.executed)
    assert "feedback_label          TEXT DEFAULT 'unknown'" in executed_sql
    assert "expected_behavior       JSONB DEFAULT '{}'::jsonb" in executed_sql
    assert "label_source            TEXT DEFAULT 'auto'" in executed_sql
    assert "ALTER TABLE rma_finding_reconciliations ADD COLUMN IF NOT EXISTS feedback_label" in executed_sql
    assert conn.commits == 1


def test_mcp_list_rule_feedback_examples(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch, role="feedback_readonly")

    def fake_list_examples(**kwargs):
        assert kwargs == {"rule_id": "rule.gpu.ecc", "feedback_label": "positive"}
        return [{"case_id": 11, "feedback_label": "positive"}]

    monkeypatch.setattr(mcp_server, "_list_rule_feedback_examples", fake_list_examples)

    response = json.loads(mcp_server.list_rule_feedback_examples(
        rule_id="rule.gpu.ecc",
        feedback_label="positive",
    ))

    assert response == {
        "rule_id": "rule.gpu.ecc",
        "examples": [{"case_id": 11, "feedback_label": "positive"}],
        "count": 1,
    }


def test_mcp_insert_rma_finding_reconciliation_denied_in_readonly(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch, role="feedback_readonly")
    called = False

    def fake_insert(**kwargs):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(mcp_server, "_insert_rma_finding_reconciliation", fake_insert)

    response = json.loads(mcp_server.insert_rma_finding_reconciliation(
        case_id=11,
        finding_id=22,
        rule_id="rule.gpu.ecc",
        repair_outcome="REPAIR_CONFIRMED",
    ))

    assert response["inserted"] is False
    assert response["ok"] is False
    assert response["reason"] == "write_denied"
    assert "error" not in response
    assert called is False


def test_mcp_update_rma_finding_attribution_denied_in_readonly(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch, role="feedback_readonly")
    called = False

    def fake_update(**kwargs):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(mcp_server, "_update_rma_finding_attribution", fake_update)

    response = json.loads(mcp_server.update_rma_finding_attribution(
        case_id=11,
        finding_id=22,
        attribution="detection",
        attribution_confidence="high",
        fix_route="rule_code",
    ))

    assert response["updated"] is False
    assert response["ok"] is False
    assert response["reason"] == "write_denied"
    assert "error" not in response
    assert called is False


def test_mcp_insert_case_denied_in_readonly(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch, role="feedback_readonly")
    called = False

    def fake_insert_case(**kwargs):
        nonlocal called
        called = True
        return 101

    monkeypatch.setattr(mcp_server, "_insert_case", fake_insert_case)

    response = json.loads(mcp_server.insert_case(
        hostname="h200-000011",
        our_classification="triaged_hardware / GPUFault",
        vendor_verdict="REPAIR_CONFIRMED",
    ))

    assert response["inserted"] is False
    assert response["ok"] is False
    assert response["reason"] == "write_denied"
    assert "error" not in response
    assert called is False


def test_mcp_update_investigation_denied_in_readonly(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch, role="feedback_readonly")
    called = False

    def fake_update_investigation(*args, **kwargs):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(mcp_server, "_update_investigation", fake_update_investigation)

    response = json.loads(mcp_server.update_investigation(
        case_id=11,
        our_investigation='{"checked": ["gpu"]}',
    ))

    assert response["updated"] is False
    assert response["ok"] is False
    assert response["reason"] == "write_denied"
    assert "error" not in response
    assert called is False


def test_mcp_insert_analysis_problem_denied_in_readonly(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch, role="feedback_readonly")
    called = False

    def fake_insert_analysis_problem(**kwargs):
        nonlocal called
        called = True
        return 33

    monkeypatch.setattr(mcp_server, "_insert_analysis_problem", fake_insert_analysis_problem)

    response = json.loads(mcp_server.insert_analysis_problem_tool(
        prompt="investigate this pattern",
        case_ids=[11],
        title="GPU NFF",
        fault_type="GPUFault",
    ))

    assert response["inserted"] is False
    assert response["ok"] is False
    assert response["reason"] == "write_denied"
    assert "error" not in response
    assert called is False


def test_insert_analysis_problem_sets_problem_id_on_insert(monkeypatch):
    cursor = FakeCursor(one=(42,))
    conn = install_fake_conn(monkeypatch, cursor)

    problem_id = knowledge_db.insert_analysis_problem(
        prompt="investigate this pattern",
        case_ids=[11],
        title="GPU NFF",
        fault_type="GPUFault",
    )

    assert problem_id == 42
    assert conn.commits == 1
    insert_sql, params = cursor.executed[0]
    assert "id, problem_id" in insert_sql
    assert "nextval(pg_get_serial_sequence('analysis_problems', 'id'))" in insert_sql
    assert params == ("GPU NFF", "GPUFault", "investigate this pattern", [11])


def test_ensure_analysis_problems_table_adds_pr_url_column(monkeypatch):
    cursor = FakeCursor()
    conn = install_fake_conn(monkeypatch, cursor)

    knowledge_db.ensure_analysis_problems_table()

    executed_sql = "\n".join(sql for sql, _params in cursor.executed)
    assert "pr_url              TEXT" in executed_sql
    assert "ALTER TABLE analysis_problems ADD COLUMN IF NOT EXISTS pr_url TEXT" in executed_sql
    assert conn.commits == 1


def test_mcp_update_problem_denied_in_readonly(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch, role="feedback_readonly")
    called = False

    def fake_update_problem(**kwargs):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(mcp_server, "_update_problem", fake_update_problem)

    response = json.loads(mcp_server.update_problem_tool(
        problem_id=33,
        status="monitoring",
        diagnosis='{"root": "rule"}',
    ))

    assert response["updated"] is False
    assert response["ok"] is False
    assert response["reason"] == "write_denied"
    assert "error" not in response
    assert called is False


def test_mcp_insert_case_allowed_in_feedback(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch, role="feedback")
    calls = []

    def fake_insert_case(**kwargs):
        calls.append(kwargs)
        return 101

    monkeypatch.setattr(mcp_server, "_insert_case", fake_insert_case)

    response = json.loads(mcp_server.insert_case(
        hostname="h200-000011",
        our_classification="triaged_hardware / GPUFault",
        vendor_verdict="REPAIR_CONFIRMED",
        our_evidence='{"alert_types": ["GpuEcc"]}',
    ))

    assert response == {"inserted": True, "id": 101}
    assert calls[0]["hostname"] == "h200-000011"
    assert calls[0]["our_evidence"] == {"alert_types": ["GpuEcc"]}


def test_list_unreconciled_rma_outcomes_sql_filters_and_limit(monkeypatch):
    cursor = FakeCursor(rows=[])
    install_fake_conn(monkeypatch, cursor)

    rows = knowledge_db.list_unreconciled_rma_outcomes(limit=37)

    assert rows == []
    sql, params = cursor.executed[0]
    for outcome in VALID_REPAIR_OUTCOMES:
        assert outcome in sql
    assert "rma_completed_at IS NOT NULL" in sql
    assert "NOT EXISTS" in sql
    assert "r.case_id = cm.id" in sql
    assert "ORDER BY cm.rma_completed_at ASC" in sql
    assert "LIMIT %s" in sql
    assert params == (37,)


@pytest.mark.parametrize(("input_limit", "expected_limit"), [
    (None, 100),
    ("bad", 100),
    (-10, 1),
    (0, 1),
    (9999, 500),
])
def test_list_unreconciled_rma_outcomes_clamps_limit(monkeypatch, input_limit, expected_limit):
    cursor = FakeCursor(rows=[])
    install_fake_conn(monkeypatch, cursor)

    knowledge_db.list_unreconciled_rma_outcomes(limit=input_limit)

    assert cursor.executed[0][1] == (expected_limit,)


def test_list_unreconciled_rma_outcomes_returns_sanitized_rows(monkeypatch):
    completed_at = datetime(2026, 6, 3, 12, 30)
    cursor = FakeCursor(rows=[{
        "case_id": 11,
        "hostname": "h200-000011",
        "vendor_verdict": "REPAIR_CONFIRMED",
        "rma_ticket_id": "RMA-11",
        "rma_completed_at": completed_at,
        "our_classification": "triaged_hardware / GPUFault",
        "vendor_component": "GPU",
        "vendor_answer_quality": "DETAILED",
        "claude_session_id": [{"agent": "repair"}],
    }])
    install_fake_conn(monkeypatch, cursor)

    rows = knowledge_db.list_unreconciled_rma_outcomes(limit=10)

    assert rows == [{
        "case_id": 11,
        "hostname": "h200-000011",
        "vendor_verdict": "REPAIR_CONFIRMED",
        "rma_ticket_id": "RMA-11",
        "rma_completed_at": "2026-06-03T12:30:00",
        "our_classification": "triaged_hardware / GPUFault",
        "vendor_component": "GPU",
        "vendor_answer_quality": "DETAILED",
        "claude_session_id": [{"agent": "repair"}],
    }]


def test_mcp_list_unreconciled_rma_outcomes_success(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch)
    expected_rows = [{"case_id": 11, "hostname": "h200-000011"}]
    calls = []

    def fake_list(limit=100):
        calls.append(limit)
        return expected_rows

    monkeypatch.setattr(mcp_server, "_list_unreconciled_rma_outcomes", fake_list)

    response = json.loads(mcp_server.list_unreconciled_rma_outcomes(limit=25))

    assert response == {"cases": expected_rows, "count": 1}
    assert calls == [25]


@pytest.mark.parametrize(("input_limit", "expected_limit"), [
    (-4, 1),
    (9999, 500),
    ("bad", 100),
])
def test_mcp_list_unreconciled_rma_outcomes_clamps_limit(monkeypatch, input_limit, expected_limit):
    mcp_server = load_mcp_server(monkeypatch)
    calls = []

    def fake_list(limit=100):
        calls.append(limit)
        return []

    monkeypatch.setattr(mcp_server, "_list_unreconciled_rma_outcomes", fake_list)

    response = json.loads(mcp_server.list_unreconciled_rma_outcomes(limit=input_limit))

    assert response == {"cases": [], "count": 0}
    assert calls == [expected_limit]


def test_mcp_list_unreconciled_rma_outcomes_error(monkeypatch):
    mcp_server = load_mcp_server(monkeypatch)

    def fake_list(limit=100):
        raise ValueError("bad limit")

    monkeypatch.setattr(mcp_server, "_list_unreconciled_rma_outcomes", fake_list)

    with pytest.raises(RuntimeError, match="bad limit"):
        mcp_server.list_unreconciled_rma_outcomes(limit=25)
