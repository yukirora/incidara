"""Switch-operations MCP server smoke tests.

Usage:
  pytest tests/test_smoke.py --host 127.0.0.1 --port 8090 -v
"""

import json
import pytest


ALL_TOOLS = {"run_switch_command", "list_switches", "read_collector_log"}

READONLY_TOOLS = {
    "list_switches": {},
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
