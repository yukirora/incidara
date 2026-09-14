import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


class DummyFastMCP:
    def __init__(self, *args, **kwargs):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        if args and callable(args[0]) and not kwargs:
            return decorator(args[0])
        return decorator

    def run(self, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def fake_fastmcp(monkeypatch):
    module = types.ModuleType("fastmcp")
    module.FastMCP = DummyFastMCP
    module.Context = object
    monkeypatch.setitem(sys.modules, "fastmcp", module)


def load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_agent_evidence_db_failure_raises(monkeypatch):
    package = types.ModuleType("agent_evidence")
    db = types.ModuleType("agent_evidence.evidence_db")
    db.ensure_table = lambda: None
    db.save_evidence = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db down"))
    db.get_node_evidence = lambda **kwargs: []
    db.search_evidence = lambda **kwargs: []
    db.delete_node_evidence = lambda **kwargs: 0
    db.get_finding_evidence = lambda **kwargs: []
    monkeypatch.setitem(sys.modules, "agent_evidence", package)
    monkeypatch.setitem(sys.modules, "agent_evidence.evidence_db", db)

    module = load_module(
        "agent_evidence_mcp_test",
        "incidara_agents/mcp_servers/agent-evidence/mcp_server.py",
    )

    with pytest.raises(RuntimeError, match="db down"):
        asyncio.run(module.save_evidence_tool("node-1", "probe_ssh", "content"))


def test_feishu_missing_required_config_raises(monkeypatch):
    package = types.ModuleType("feishu_bitable")
    client_module = types.ModuleType("feishu_bitable.client")
    client_module.FeishuBitableClient = object
    monkeypatch.setitem(sys.modules, "feishu_bitable", package)
    monkeypatch.setitem(sys.modules, "feishu_bitable.client", client_module)
    monkeypatch.delenv("FEISHU_BASE_TOKEN", raising=False)
    monkeypatch.delenv("FEISHU_TABLE_ID", raising=False)

    module = load_module(
        "feishu_bitable_mcp_test",
        "incidara_agents/mcp_servers/feishu-bitable/mcp_server.py",
    )

    list_tables = module.server.tools["list_tables_tool"]
    with pytest.raises(ValueError, match="app_token required"):
        list_tables("")


def test_switch_missing_collector_log_raises(monkeypatch):
    module = load_module(
        "switch_operations_mcp_test",
        "incidara_agents/mcp_servers/switch-operations/mcp_server.py",
    )

    with pytest.raises(FileNotFoundError, match="Log not found"):
        module.read_collector_log("does_not_exist")


def test_patrol_db_failure_raises(monkeypatch):
    db = types.ModuleType("patrol_cron.db")
    db._t = lambda table: table
    monkeypatch.setitem(sys.modules, "patrol_cron.db", db)

    psycopg2 = types.ModuleType("psycopg2")
    extras = types.ModuleType("psycopg2.extras")
    psycopg2.extras = extras
    psycopg2.connect = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db down"))
    monkeypatch.setitem(sys.modules, "psycopg2", psycopg2)
    monkeypatch.setitem(sys.modules, "psycopg2.extras", extras)

    module = load_module(
        "patrol_cron_mcp_tools_test",
        "incidara_agents/mcp_servers/patrol-cron/patrol_cron/mcp_tools.py",
    )

    with pytest.raises(RuntimeError, match="db down"):
        module.create_collector("c", 60, "node", [], "test")


def test_patrol_invalid_verdict_raises(monkeypatch):
    db = types.ModuleType("patrol_cron.db")
    db._t = lambda table: table
    monkeypatch.setitem(sys.modules, "patrol_cron.db", db)

    psycopg2 = types.ModuleType("psycopg2")
    extras = types.ModuleType("psycopg2.extras")
    psycopg2.extras = extras
    monkeypatch.setitem(sys.modules, "psycopg2", psycopg2)
    monkeypatch.setitem(sys.modules, "psycopg2.extras", extras)

    module = load_module(
        "patrol_cron_mcp_tools_test_invalid",
        "incidara_agents/mcp_servers/patrol-cron/patrol_cron/mcp_tools.py",
    )

    with pytest.raises(ValueError, match="Invalid verdict"):
        module.record_verdict(1, "maybe")


def test_node_invalid_action_raises(monkeypatch):
    module = load_module(
        "node_operations_mcp_test",
        "incidara_agents/mcp_servers/node-operations/mcp_server.py",
    )
    server = module.create_server("ops")
    execute_node_action = server.tools["execute_node_action"]

    with pytest.raises(ValueError, match="Invalid action"):
        execute_node_action("node-1", "reboot", "triage", "bad action")


def test_node_available_status_transition_is_domain_rejection(monkeypatch):
    module = load_module(
        "node_operations_mcp_test_transition",
        "incidara_agents/mcp_servers/node-operations/mcp_server.py",
    )
    server = module.create_server("diagnosis")
    move_node_status = server.tools["move_node_status"]

    data = json.loads(
        move_node_status("node-1", "onboard-1", "available", "triaged_hardware", "test", "test")
    )

    assert data["ok"] is False
    assert data["reason"] == "transition_denied"
    assert "error" not in data


def test_node_kubectl_mutating_command_is_domain_rejection(monkeypatch):
    module = load_module(
        "node_operations_mcp_test_kubectl",
        "incidara_agents/mcp_servers/node-operations/mcp_server.py",
    )
    server = module.create_server("diagnosis")
    run_kubectl = server.tools["run_kubectl"]

    data = json.loads(run_kubectl("patch node node-1 --type merge -p '{}'"))

    assert data["ok"] is False
    assert data["reason"] == "command_blocked"
    assert "error" not in data


def test_node_verify_action_kubectl_failure_raises(monkeypatch):
    module = load_module(
        "node_operations_mcp_test_verify_action",
        "incidara_agents/mcp_servers/node-operations/mcp_server.py",
    )
    server = module.create_server("full")
    verify_node_action = server.tools["verify_node_action"]

    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=1, stderr="kubectl down", stdout=""),
    )

    with pytest.raises(RuntimeError, match="kubectl failed: kubectl down"):
        verify_node_action("node-1", "cordon")


def install_agent_feedback_stub(monkeypatch, query_similar_cases=None, insert_case=None):
    package = types.ModuleType("agent_feedback")
    db = types.ModuleType("agent_feedback.knowledge_db")

    def noop(*args, **kwargs):
        return None

    db.ensure_table = noop
    db.ensure_analysis_problems_table = noop
    db.ensure_rejected_proposals_table = noop
    db.ensure_agent_memory_table = noop
    db.clamp_unreconciled_limit = lambda limit: limit
    db.insert_case = insert_case or (lambda **kwargs: 1)
    db.update_investigation = lambda *args, **kwargs: True
    db.update_case = lambda *args, **kwargs: True
    db.query_similar_cases = query_similar_cases or (lambda **kwargs: [])
    db.get_cases_by_hostname = lambda *args, **kwargs: []
    db.get_rule_stats = lambda *args, **kwargs: []
    db.get_misclass_paths = lambda *args, **kwargs: []
    db.insert_analysis_problem = lambda *args, **kwargs: 1
    db.insert_rma_finding_reconciliation = lambda *args, **kwargs: True
    db.record_finding_verdict_from_case = lambda *args, **kwargs: {}
    db.list_rule_feedback_examples = lambda *args, **kwargs: []
    db.list_unreconciled_rma_outcomes = lambda *args, **kwargs: []
    db.get_problems = lambda *args, **kwargs: []
    db.update_problem = lambda *args, **kwargs: True
    db.update_rma_finding_attribution = lambda *args, **kwargs: True
    db.get_problem_history = lambda *args, **kwargs: []
    db.get_repeat_offenders = lambda *args, **kwargs: []
    db.insert_rejected_proposal = lambda *args, **kwargs: 1
    db.get_rejected_proposals = lambda *args, **kwargs: []
    db.insert_agent_memory = lambda *args, **kwargs: 1
    db.query_agent_memory = lambda *args, **kwargs: []

    monkeypatch.setitem(sys.modules, "agent_feedback", package)
    monkeypatch.setitem(sys.modules, "agent_feedback.knowledge_db", db)


def test_agent_feedback_query_failure_raises(monkeypatch):
    install_agent_feedback_stub(
        monkeypatch,
        query_similar_cases=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("feedback db down")),
    )
    monkeypatch.setenv("AGENT_FEEDBACK_ROLE", "feedback_readonly")

    module = load_module(
        "agent_feedback_mcp_test",
        "incidara_agents/mcp_servers/agent-feedback/mcp_server.py",
    )

    with pytest.raises(RuntimeError, match="feedback db down"):
        module.query_similar_cases()


def test_agent_feedback_write_denied_is_domain_rejection(monkeypatch):
    install_agent_feedback_stub(monkeypatch)
    monkeypatch.setenv("AGENT_FEEDBACK_ROLE", "feedback_readonly")

    module = load_module(
        "agent_feedback_mcp_test_denied",
        "incidara_agents/mcp_servers/agent-feedback/mcp_server.py",
    )

    data = json.loads(module.insert_case("node-1", "GPUFault"))

    assert data["ok"] is False
    assert data["reason"] == "write_denied"
    assert data["inserted"] is False
    assert "error" not in data


def test_node_alert_manager_missing_endpoint_raises(monkeypatch):
    monkeypatch.delenv("ALERT_MANAGER_URL", raising=False)
    monkeypatch.delenv("LTP_HOST", raising=False)

    module = load_module(
        "node_alert_manager_test",
        "incidara_agents/mcp_servers/node-operations/node_operations/alert_manager.py",
    )

    with pytest.raises(RuntimeError, match="ALERT_MANAGER_URL or LTP_HOST not set"):
        module.submit_triage_alert("node-1", "triaged_hardware", "agent_test")
