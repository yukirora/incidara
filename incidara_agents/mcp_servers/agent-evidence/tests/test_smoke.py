"""Agent-evidence MCP server smoke tests.

Usage:
  pytest tests/test_smoke.py --host 127.0.0.1 --port 8092 -v
"""

import json
import pytest


ALL_TOOLS = {
    "save_evidence_tool", "get_node_evidence_tool", "search_evidence_tool",
    "get_finding_evidence_tool", "delete_node_evidence_tool",
}

READONLY_TOOLS = {
    "get_node_evidence_tool": {"hostname": "nonexistent-smoke-test"},
    "search_evidence_tool": {"source": "test", "since_days": 1},
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


class TestSaveAndDelete:
    def test_save_and_get_evidence(self, client):
        # Save — needs hostname, source, content
        result = client.call_tool("save_evidence_tool", {
            "hostname": "smoke-test-node",
            "source": "test_smoke.py",
            "content": "automated smoke test evidence",
            "category": "test",
            "summary": "automated smoke test",
        })
        assert not result.get("isError", False), f"save failed: {result}"

        # Get
        result = client.call_tool("get_node_evidence_tool", {"hostname": "smoke-test-node"})
        assert not result.get("isError", False), f"get failed: {result}"
        content = result.get("content", [{}])[0].get("text", "")
        data = json.loads(content)
        assert isinstance(data, dict) and "evidence" in data
        assert any("smoke test" in str(e.get("summary", "")) for e in data["evidence"])

        # Cleanup
        result = client.call_tool("delete_node_evidence_tool", {"hostname": "smoke-test-node"})
        assert not result.get("isError", False), f"delete failed: {result}"
