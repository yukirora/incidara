#!/usr/bin/env python3
"""Standalone MCP server for patrol_cron — rule/collector CRUD + findings.

Runs as its own process (or container), serves tools over SSE or stdio.
Detection-agent and other agents connect to this over the network.

Usage:
    # SSE (for remote agents)
    python mcp_server.py --transport sse --port 8080

    # Stdio (for local testing or in-container use)
    python mcp_server.py --transport stdio
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# Add this directory to path so patrol_cron package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastmcp import FastMCP
from patrol_cron.mcp_tools import register_patrol_tools

logger = logging.getLogger("patrol_cron.mcp")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="patrol_cron MCP server")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse", "http"])
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    # FastMCP reads host/port from constructor, not from run()
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    mcp = FastMCP("patrol-cron")
    register_patrol_tools(mcp)

    logger.info(f"Starting patrol-cron MCP server (transport={args.transport}, port={args.port})")
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, host=host, port=args.port)
