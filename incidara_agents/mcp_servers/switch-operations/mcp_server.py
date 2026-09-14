"""Switch Operations MCP Server — interactive SSH commands for network switches.

Tools:
  run_switch_command  — execute a command on a switch (IB or Ruijie)
  list_switches       — query switch inventory from DB
  read_collector_log  — read patrol-cron collector output logs

No node-operations dependency. Switches are fundamentally different from nodes.
"""

from __future__ import annotations

import json
import os

from fastmcp import FastMCP

host = os.environ.get("MCP_HOST", "127.0.0.1")
port = int(os.environ.get("SWITCH_OPS_PORT", "8090"))

mcp = FastMCP("switch-operations")


def _raise_tool_error(exc: Exception) -> None:
    raise RuntimeError(str(exc)) from exc


# ── run_switch_command ──────────────────────────────────────────────────

@mcp.tool()
def run_switch_command(ip: str, command: str, timeout: int = 30) -> str:
    """Execute a command on a network switch (IB or Ruijie) via interactive SSH.

    Uses SWITCH_SSH_USER and SWITCH_SSH_PASSWORD from environment.
    Switches require interactive login (password → shell → paging disable → command).
    Examples: 'show version', 'show interfaces ib', 'show interfaces counters errors',
    'show temperature', 'show alarm', 'show fan', 'show power'.
    Read-only investigation — do NOT use for config changes.
    """
    from switch_operations.ssh import run_switch_command as _run
    result = _run(ip=ip, command=command, timeout=timeout)
    return json.dumps(result, indent=2)


# ── list_switches ──────────────────────────────────────────────────────

@mcp.tool()
def list_switches(switch_type: str | None = None) -> str:
    """List switches from inventory. Optionally filter by type: 'ib', 'ruijie', 'ufm'.

    Returns hostname, ip, type, and location for each switch.
    """
    from switch_operations.ssh import get_switch_inventory
    switches = get_switch_inventory(switch_type=switch_type)
    if not switches:
        return json.dumps({"switches": [], "note": "No switches found. Check EVIDENCE_DB_URL and switch_inventory table."})
    # Summarize — don't dump all fields
    summary = []
    for s in switches:
        summary.append({
            "hostname": s.get("hostname"),
            "ip": s.get("ip"),
            "type": s.get("type"),
        })
    return json.dumps({"switches": summary, "count": len(summary)}, indent=2)


# ── read_collector_log ─────────────────────────────────────────────────

@mcp.tool()
def read_collector_log(collector_name: str, filter: str | None = None, tail: int = 50) -> str:
    """Read patrol-cron collector output logs for pipeline validation.

    Shows what the collector actually collected — raw SSH outputs, errors, etc.
    Each cycle logs: 1 passing target (full output) + failing targets (error messages).

    Args:
        collector_name: Collector to check (e.g. 'switch_health_ib', 'switch_health_ruijie').
        filter: Grep pattern to filter lines (e.g. 'FAIL', 'outputs_keys', 'show version').
        tail: Number of lines to return from end of log (default 50).

    Agent can also read logs directly: grep 'FAIL' /tmp/patrol_cron/logs/<collector>.log
    """
    log_dir = "/tmp/patrol_cron/logs"
    log_path = os.path.join(log_dir, f"{collector_name}.log")

    if not os.path.exists(log_path):
        available = []
        if os.path.isdir(log_dir):
            available = [f.replace(".log", "") for f in os.listdir(log_dir) if f.endswith(".log")]
        suffix = f" Available collectors: {available}" if available else ""
        raise FileNotFoundError(f"Log not found: {log_path}.{suffix}")

    try:
        with open(log_path, "r") as f:
            lines = f.readlines()

        if filter:
            import re
            pattern = re.compile(filter, re.IGNORECASE)
            lines = [l for l in lines if pattern.search(l)]

        # Take last N lines
        result_lines = lines[-tail:]
        return json.dumps({
            "collector": collector_name,
            "total_lines": len(lines),
            "filtered_lines": len(result_lines),
            "log": "".join(result_lines),
        }, indent=2)
    except Exception as e:
        _raise_tool_error(e)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Switch Operations MCP Server")
    parser.add_argument("--transport", default="http",
                        choices=["stdio", "sse", "http"])
    args = parser.parse_args()

    mcp.run(transport=args.transport, host=host, port=port)
