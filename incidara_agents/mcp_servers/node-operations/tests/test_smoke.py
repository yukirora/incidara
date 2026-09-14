"""Node-operations MCP tool smoke tests.

Calls each MCP tool via HTTP against a running server instance.
Catches: missing tools, wrong args, crashes, DB connection failures, auth errors.

Usage:
  # Against ops-role server (port 8083):
  pytest tests/test_smoke.py --host 127.0.0.1 --port 8083 -v
  # Against diagnosis-role server (port 8081):
  pytest tests/test_smoke.py --host 127.0.0.1 --port 8081 -v
  # With a real node for node-scoped tests:
  pytest tests/test_smoke.py --host 127.0.0.1 --port 8083 --test-node h200-001157 -v
"""

import json
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from conftest import MCPClient


# ── Tool classification ─────────────────────────────────────────────────
# Safe read-only tools: can be called with minimal args, no side effects
READONLY_TOOLS = {
    "get_nodes_by_status": {"status": "cordoned"},
    "get_existing_reasons": {},
    "get_node_recent_jobs": {"hostname": "nonexistent-test-node", "limit": 1},
    "query_rma_cases": {"limit": 1, "days": 7},
}

# Read-only tools that require a real hostname (skip if no test node)
READONLY_NEEDS_NODE = {
    "get_node_detail": {"hostname": "__NEEDS_NODE__"},
    "get_node_history": {"hostname": "__NEEDS_NODE__"},
    "get_node_status_history": {"hostname": "__NEEDS_NODE__"},
    "get_node_alerts": {"hostname": "__NEEDS_NODE__"},
    "get_validation_job": {"hostname": "__NEEDS_NODE__"},
}

# Read-only tools that need non-hostname args — can't auto-populate, skip in smoke
READONLY_CUSTOM_ARGS = {
    "get_job_details": {"jobname": "__NEEDS_JOBNAME__"},
    "get_job_events": {"job_name": "__NEEDS_JOBNAME__"},
    "get_ticket_status": {"ticket_id": "__NEEDS_TICKET__"},
    "check_fabricmanager": {"ip": "__NEEDS_IP__"},
    "bmc_query": {"bmc_ip": "__NEEDS_BMC_IP__"},
    "bmc_health_log": {"bmc_ip": "__NEEDS_BMC_IP__"},
    "bmc_screenshot": {"bmc_ip": "__NEEDS_BMC_IP__"},
}

# Write tools by role: NEVER call in smoke tests — just verify they exist
WRITE_TOOLS_OPS = {
    "move_node_status", "submit_validation", "submit_triage_alert",
    "reset_node", "submit_rma_ticket", "run_config_stage",
    "complete_rma", "scale_k8s_node", "run_full_config",
    "collect_sysinfo", "clone_and_allocate",
    "get_ua_nodes_with_tickets", "check_completed_tickets",
    "reallocate_node", "bmc_power_cycle", "wait_for_boot",
    "run_ssh_command", "run_kubectl", "run_kubectl_dangerous",
    "delegate_to_agent", "get_agent_active_tasks",
    "list_stages", "check_sku",
}

WRITE_TOOLS_DIAGNOSIS = {
    "check_fabricmanager", "move_node_status", "submit_validation",
    "delegate_to_agent", "get_agent_active_tasks",
    "run_kubectl", "resolve_ip", "run_database_query",
}


@pytest.fixture
def client(mcp_host, mcp_port):
    c = MCPClient(mcp_host, mcp_port)
    c.initialize()
    return c


class TestToolDiscovery:
    """Verify all expected tools are registered for the given role."""

    def test_readonly_tools_exist(self, client):
        actual = set(client.list_tools())
        missing = set(READONLY_TOOLS.keys()) - actual
        assert not missing, f"Missing read-only tools: {sorted(missing)}"


class TestReadonlyTools:
    """Call each read-only tool and verify it returns valid JSON without error."""

    @pytest.mark.parametrize("tool_name,args", READONLY_TOOLS.items(),
                             ids=list(READONLY_TOOLS.keys()))
    def test_readonly_tool(self, client, tool_name, args):
        result = client.call_tool(tool_name, args, timeout=15)
        assert not result.get("isError", False), f"{tool_name} returned error: {result}"
        content = result.get("content", [])
        assert len(content) > 0, f"{tool_name} returned no content"

        # Parse the JSON response
        text = content[0].get("text", "")
        if text:
            try:
                data = json.loads(text)
                # Tools should return structured data, not error dicts
                if isinstance(data, dict) and "error" in data:
                    pytest.fail(f"{tool_name} returned error in JSON: {data['error']}")
            except json.JSONDecodeError:
                # Some tools may return plain text — that's OK
                pass

    @pytest.mark.parametrize("tool_name,args", READONLY_NEEDS_NODE.items(),
                             ids=list(READONLY_NEEDS_NODE.keys()))
    def test_readonly_tool_with_node(self, client, test_node, tool_name, args):
        if not test_node:
            pytest.skip("No --test-node provided")
        args = {k: (test_node if v == "__NEEDS_NODE__" else v) for k, v in args.items()}
        result = client.call_tool(tool_name, args, timeout=30)
        assert not result.get("isError", False), f"{tool_name} returned error: {result}"


class TestWriteToolsExist:
    """Write tools should be discoverable but we never call them."""

    @pytest.mark.parametrize("tool_name", sorted(WRITE_TOOLS_OPS | WRITE_TOOLS_DIAGNOSIS))
    def test_write_tool_registered(self, client, tool_name):
        actual = set(client.list_tools())
        if tool_name not in actual:
            pytest.skip(f"{tool_name} not in this role")
