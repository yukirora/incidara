"""Shared MCPClient and pytest options for MCP server smoke tests.

Provides:
  - MCPClient: HTTP client for streamable-http MCP servers
  - --host / --port / --test-node CLI options
"""

import json
import pytest
import urllib.request


class MCPClient:
    """Minimal MCP client for streamable-http servers."""

    def __init__(self, host="127.0.0.1", port=8080):
        self.base = f"http://{host}:{port}/mcp"
        self._id = 0
        self._session_id = None

    def _next_id(self):
        self._id += 1
        return self._id

    def _post(self, payload):
        data = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(self.base, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            # Capture session ID from response
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self._session_id = sid
            body = resp.read().decode()
            # Notifications (no id) return 202 with empty body
            if not body:
                return None
            # Handle SSE streaming response
            if "text/event-stream" in resp.headers.get("Content-Type", ""):
                result = None
                for line in body.split("\n"):
                    line = line.strip()
                    if line.startswith("data:"):
                        chunk = line[5:].strip()
                        if chunk:
                            try:
                                msg = json.loads(chunk)
                                if "result" in msg:
                                    result = msg["result"]
                            except json.JSONDecodeError:
                                pass
                return result
            return json.loads(body)

    def initialize(self):
        result = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "0.1.0"},
            },
        })
        # Send initialized notification (returns empty 202)
        self._post({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        return result

    def list_tools(self):
        result = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
        })
        return [t["name"] for t in result.get("tools", [])]

    def call_tool(self, name, arguments=None, timeout=30):
        result = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        return result or {}


def pytest_addoption(parser):
    parser.addoption("--host", default="127.0.0.1")
    parser.addoption("--port", type=int, default=8080)
    parser.addoption("--test-node", default=None)


@pytest.fixture
def mcp_host(pytestconfig):
    return pytestconfig.getoption("--host")


@pytest.fixture
def mcp_port(pytestconfig):
    return pytestconfig.getoption("--port")


@pytest.fixture
def test_node(pytestconfig):
    val = pytestconfig.getoption("--test-node") or None
    return val


@pytest.fixture
def client(mcp_host, mcp_port):
    c = MCPClient(mcp_host, mcp_port)
    c.initialize()
    return c
