#!/usr/bin/env python3
"""Agent Evidence MCP Server.

Provides tools for triage and repair agents to persist and query
investigation evidence in the agent-specific PostgreSQL DB on .23:5434.

Separate from node-operations MCP server (which reads/writes platform DB on .19).
"""

import argparse
import json
import logging
import os
import sys

from fastmcp import FastMCP

from agent_evidence.evidence_db import (
    ensure_table,
    save_evidence,
    get_node_evidence,
    search_evidence,
    delete_node_evidence,
    get_finding_evidence,
)

logger = logging.getLogger(__name__)

_host = os.environ.get("MCP_HOST", "127.0.0.1")
_port = int(os.environ.get("AGENT_EVIDENCE_PORT", "8092"))

mcp = FastMCP("agent-evidence")

# Ensure table exists at import time (no on_startup hook in this MCP version)
ensure_table()
logger.info("agent-evidence MCP server started, table ensured")


def _raise_tool_error(exc: Exception) -> None:
    raise RuntimeError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def save_evidence_tool(
    hostname: str,
    source: str,
    content: str,
    category: str = None,
    summary: str = None,
    metadata: dict = None,
    finding_id: int = None,
) -> str:
    """Save investigation evidence for a node. Call after each investigation step.

    Args:
        hostname: Node hostname (e.g., "lg-cmc-demo-r01u01-h200-000001")
        source: Evidence source. One of: probe_ssh, nvidia_smi, dmesg, ib_stat,
                job_log, alert, nvlink, fabricmanager, job_list, job_detail,
                job_events, other
        content: Raw output from the investigation tool/command. This is the
                 primary evidence — include full output, not just interpretation.
        category: Fault subsystem: gpu, ib, nvlink, pcie, cpu, memory, platform, unknown
        summary: 1-line interpretation of what was found
                 (e.g., "GPU 4: 42 uncorrectable ECC, Xid 79")
        metadata: Optional structured fields for query/filter
                  (e.g., {"gpu_index": 4, "ecc_count": 42, "baseline": 55.25})
        finding_id: Optional patrol_findings ID to link this evidence to a finding.
                    Use when creating evidence during investigation of a specific finding.
    """
    try:
        row_id = save_evidence(
            node_name=hostname,
            source=source,
            content=content,
            category=category,
            summary=summary,
            metadata=metadata,
            finding_id=finding_id,
        )
        return json.dumps({"saved": True, "id": row_id})
    except Exception as e:
        logger.error(f"save_evidence failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
async def get_node_evidence_tool(
    hostname: str,
    source: str = None,
    category: str = None,
    collected_by: str = None,
    limit: int = 100,
) -> str:
    """Get saved investigation evidence for a node. Newest first.

    Use this to check what evidence already exists before re-investigating,
    and in the evidence completeness gate before submitting a ticket.

    Args:
        hostname: Node hostname
        source: Filter by evidence source (probe_ssh, nvidia_smi, dmesg, etc.)
        category: Filter by fault subsystem (gpu, ib, nvlink, etc.)
        collected_by: Filter by collecting agent (e.g., "repair-draft", "repair", "triage")
        limit: Max rows to return (default 100)
    """
    try:
        rows = get_node_evidence(
            node_name=hostname,
            source=source,
            category=category,
            collected_by=collected_by,
            limit=limit,
        )
        return json.dumps({"evidence": rows, "count": len(rows)})
    except Exception as e:
        logger.error(f"get_node_evidence failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
async def search_evidence_tool(
    category: str = None,
    source: str = None,
    summary_like: str = None,
    collected_by: str = None,
    since_days: int = 30,
    limit: int = 100,
) -> str:
    """Search investigation evidence across ALL nodes. Used for pattern detection.

    Unlike get_node_evidence_tool (per-host), this searches across all hosts.
    Find recurring patterns by filtering on category or summary substring.

    Args:
        category: Filter by fault subsystem (gpu, ib, nvlink, pcie, platform, etc.)
        source: Filter by evidence source (probe_ssh, dmesg, alert, etc.)
        summary_like: Case-insensitive substring match on summary (e.g., "FM down", "ECC")
        collected_by: Filter by collecting agent (e.g., "triage", "repair")
        since_days: Only evidence from last N days (default 30)
        limit: Max rows to return (default 100)
    """
    try:
        rows = search_evidence(
            category=category,
            source=source,
            summary_like=summary_like,
            collected_by=collected_by,
            since_days=since_days,
            limit=limit,
        )
        return json.dumps({"evidence": rows, "count": len(rows)})
    except Exception as e:
        logger.error(f"search_evidence failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
async def get_finding_evidence_tool(
    finding_id: int,
    limit: int = 100,
) -> str:
    """Get all investigation evidence linked to a finding. Newest first.

    Use this to retrieve the raw diagnostic data collected during investigation
    of a finding. The detection agent uses this to learn patterns from raw data
    when creating or refining rules.

    Args:
        finding_id: The patrol_findings ID to look up evidence for
        limit: Max rows to return (default 100)
    """
    try:
        rows = get_finding_evidence(finding_id=finding_id, limit=limit)
        return json.dumps({"evidence": rows, "count": len(rows)})
    except Exception as e:
        logger.error(f"get_finding_evidence failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
async def delete_node_evidence_tool(
    hostname: str,
    before: str = None,
) -> str:
    """Delete evidence for a node. Use for cleanup or re-investigation.

    Args:
        hostname: Node hostname
        before: ISO timestamp — only delete evidence older than this.
                If omitted, deletes ALL evidence for this node.
    """
    try:
        deleted = delete_node_evidence(node_name=hostname, before=before)
        return json.dumps({"deleted": deleted})
    except Exception as e:
        logger.error(f"delete_node_evidence failed: {e}")
        _raise_tool_error(e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Agent Evidence MCP Server")
    parser.add_argument("--transport", default="http",
                        choices=["stdio", "sse", "http"])
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    logger.info(f"agent-evidence starting (transport={args.transport})")
    mcp.run(transport=args.transport, host=_host, port=_port)


if __name__ == "__main__":
    main()
