"""Patrol-cron MCP server smoke tests.

Runs against deployed container on .23 (port 8080 or 8082).

Usage:
  pytest tests/test_smoke.py --host 127.0.0.1 --port 8080 -v
"""

import pytest

ALL_TOOLS = {
    "create_collector", "list_collectors", "get_collector_health",
    "update_collector", "create_rule", "update_rule_stage",
    "update_rule_code", "toggle_rule", "list_rules", "get_rule_detail",
    "list_dirty_reconciliation_rules", "list_findings", "record_verdict",
    "get_finding_raw_data", "reconcile_finding", "create_finding",
    "update_finding", "get_rule_accuracy", "get_rule_rejection_reasons",
    "get_rule_bad_feedback_rate", "create_rule_replay_case",
    "create_rule_replay_cases_from_feedback", "list_rule_replay_cases",
    "replay_rule_case", "run_rule_replay_suite",
    "execute_node_action", "execute_job_action", "query_prometheus",
    "ssh_run", "read_collector_log", "query_job_metadata",
    "fetch_job_logs", "fetch_node_logs", "run_collector_once",
    "test_rule_once",
}

# Subset available on job-patrol (port 8082) — no get_rule_rejection_reasons, get_finding_raw_data
JOB_PATROL_TOOLS = ALL_TOOLS - {"get_rule_rejection_reasons", "get_finding_raw_data"}

READONLY_TOOLS = {
    "list_collectors": {},
    "list_rules": {},
    "list_findings": {},
}


class TestToolDiscovery:
    def test_all_tools_registered(self, client):
        actual = set(client.list_tools())
        # All core tools must be present (both instances have these)
        core_tools = {
            "create_collector", "list_collectors", "create_rule",
            "list_rules", "list_findings", "reconcile_finding",
            "create_finding", "get_rule_accuracy",
        }
        missing = core_tools - actual
        assert not missing, f"Missing tools: {sorted(missing)}"


class TestReadonlyTools:
    @pytest.mark.parametrize("tool_name,args", READONLY_TOOLS.items(),
                             ids=list(READONLY_TOOLS.keys()))
    def test_readonly_tool(self, client, tool_name, args):
        result = client.call_tool(tool_name, args)
        assert not result.get("isError", False), f"Tool error: {result}"
