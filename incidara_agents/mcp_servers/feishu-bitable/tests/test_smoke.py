"""Feishu-bitable MCP server smoke tests.

Usage:
  pytest tests/test_smoke.py --host 127.0.0.1 --port 8094 -v
"""

import json
import pytest


ALL_TOOLS = {
    "list_tables_tool", "get_table_schema_tool", "query_table_tool",
    "get_record_tool", "list_issue_categories_tool",
    "get_unprocessed_reports_tool", "get_node_unhealthy_reports_tool",
}

READONLY_TOOLS = {
    # These use env defaults for app_token/table_id
    "list_tables_tool": {},
    "list_issue_categories_tool": {},
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
