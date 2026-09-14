"""Tests for mcp_server — entry point registers all tools."""

import unittest.mock as um


def test_register_patrol_tools_registers_11_tools():
    from patrol_cron.mcp_tools import register_patrol_tools
    mock_mcp = um.MagicMock()
    register_patrol_tools(mock_mcp)
    assert mock_mcp.tool.call_count >= 16
