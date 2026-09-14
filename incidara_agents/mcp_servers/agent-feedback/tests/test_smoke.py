"""Agent-feedback MCP server smoke tests.

Usage:
  pytest tests/test_smoke.py --host 127.0.0.1 --port 8091 -v
"""

import json
import pytest


ALL_TOOLS = {
    "query_similar_cases", "get_rule_stats", "get_misclass_paths",
    "get_cases_by_hostname", "get_problems", "list_unreconciled_rma_outcomes",
    "list_rule_feedback_examples", "insert_case", "update_investigation",
    "insert_rma_finding_reconciliation", "update_rma_finding_attribution",
    "insert_analysis_problem_tool", "update_problem_tool",
    "find_session_by_hostname", "get_repeat_offenders_tool",
    "get_problem_history_tool", "get_rejected_proposals",
}

READONLY_TOOLS = {
    "query_similar_cases": {"limit": 3},
    "get_rule_stats": {},
    "get_cases_by_hostname": {"hostname": "nonexistent-smoke-test"},
    "get_problems": {},
    "list_unreconciled_rma_outcomes": {"limit": 3},
    "get_rejected_proposals": {"limit": 3},
}


class TestToolDiscovery:
    def test_all_tools_registered(self, client):
        actual = set(client.list_tools())
        missing = ALL_TOOLS - actual
        assert not missing, f"Missing tools: {sorted(missing)}"


class TestReadonlyTools:
    @pytest.mark.parametrize("tool_name,args", READONLY_TOOLS.items(),
                             ids=list(READONLY_TOOLS.keys()))
    def test_readonly_tool(self, client, tool_name, args):
        result = client.call_tool(tool_name, args, timeout=15)
        assert not result.get("isError", False), f"{tool_name} error: {result}"
