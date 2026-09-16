from __future__ import annotations

import argparse
import asyncio
import logging
import os
import json
import psycopg2
from fastmcp import FastMCP, Context

logger = logging.getLogger(__name__)


ROLE_TOOLS = {
    "readonly":  {"get_nodes_by_status", "get_node_detail", "get_node_history",
                  "get_node_status_history", "get_node_alerts", "get_node_recent_jobs",
                  "get_job_details", "get_job_events", "get_validation_job",
                  "get_existing_reasons",
                  "probe_ssh", "bmc_query", "bmc_screenshot", "bmc_health_log", "query_rma_cases"},
    "diagnosis": {"get_nodes_by_status", "get_node_detail", "get_node_history",
                  "get_node_status_history", "get_node_alerts", "get_node_recent_jobs",
                  "get_job_details", "get_job_events", "get_validation_job",
                  "get_existing_reasons",
                  "probe_ssh", "check_fabricmanager", "bmc_query", "bmc_screenshot",
                  "bmc_health_log", "list_stages", "check_sku",
                  "move_node_status", "submit_validation", "query_rma_cases",
                  "submit_triage_alert",
                  "delegate_to_agent", "get_agent_active_tasks",
                  "run_kubectl", "resolve_ip", "run_database_query",
                  "check_kubelet", "check_ib_ports", "check_ip_routing", "check_gpu_clocks"},
    "ops":       {"get_nodes_by_status", "get_node_detail", "get_node_history",
                  "get_node_status_history", "get_node_alerts", "get_node_recent_jobs",
                  "get_job_details", "get_job_events", "get_validation_job",
                  "get_existing_reasons",
                  "probe_ssh", "check_fabricmanager", "bmc_query", "bmc_screenshot",
                  "bmc_health_log",
                  "move_node_status",
                  "reset_node", "submit_rma_ticket", "run_config_stage", "list_stages", "check_sku", "get_ticket_status",
                  "execute_node_action", "submit_triage_alert",
                  "query_rma_cases", "run_ssh_command", "run_kubectl", "run_kubectl_dangerous",
                  "scale_k8s_node",
                  "complete_rma", "run_full_config", "collect_sysinfo", "clone_and_allocate",
                  "get_ua_nodes_with_tickets", "check_completed_tickets", "reallocate_node",
                  "bmc_power_cycle", "wait_for_boot",
                  "submit_validation", "submit_triage_alert", "execute_node_action",
                  "delegate_to_agent", "get_agent_active_tasks"},
    "feedback":  {"query_completed_rmas",
                  "get_nodes_by_status", "get_node_detail", "get_node_history",
                  "get_node_status_history", "get_node_alerts", "get_node_recent_jobs",
                  "get_job_details", "get_job_events", "get_validation_job",
                  "get_existing_reasons",
                  "probe_ssh", "check_fabricmanager", "bmc_query", "bmc_screenshot", "bmc_health_log", "list_stages",
                  "delegate_to_agent", "get_agent_active_tasks",
                  "run_ssh_command"},
    "full":      None,  # None = register all tools
}

# When REPLAY_MOCK_DIR is set, tool calls return mock responses from JSON files.
REPLAY_MOCK_DIR = os.environ.get("REPLAY_MOCK_DIR", "")
REPLAY_MOCK_STRICT = os.environ.get("REPLAY_MOCK_STRICT", "") == "1"

# Evidence DB — auto-save investigation tool output.
# If EVIDENCE_DB_URL is not set, auto-save is silently disabled.
EVIDENCE_DB_URL = os.environ.get("EVIDENCE_DB_URL", "")
AGENT_NAME = os.environ.get("AGENT_NAME", os.environ.get("AGENT_ROLE", "unknown"))


def _raise_tool_error(exc: Exception) -> None:
    raise RuntimeError(str(exc)) from exc


def _input_error(message: str) -> None:
    raise ValueError(message)


def _domain_result(reason: str, message: str, **details) -> str:
    payload = {"ok": False, "reason": reason, "message": message}
    payload.update(details)
    return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# Evidence DB helpers (lightweight — just INSERT, no dependency on agent-evidence pkg)
# ---------------------------------------------------------------------------

def _ensure_evidence_table():
    """Create investigation_evidence table if it doesn't exist."""
    if not EVIDENCE_DB_URL:
        return
    try:
        with psycopg2.connect(EVIDENCE_DB_URL) as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS investigation_evidence (
                    id           BIGSERIAL PRIMARY KEY,
                    node_name    VARCHAR NOT NULL,
                    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    collected_by VARCHAR NOT NULL,
                    source       VARCHAR NOT NULL,
                    category     VARCHAR,
                    summary      TEXT,
                    content      TEXT NOT NULL,
                    metadata     JSONB DEFAULT '{}',
                    finding_id   INTEGER REFERENCES patrol_findings(finding_id) ON DELETE SET NULL
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_evidence_node
                ON investigation_evidence(node_name, collected_at DESC);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_evidence_source
                ON investigation_evidence(node_name, source);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_evidence_finding
                ON investigation_evidence(finding_id) WHERE finding_id IS NOT NULL;
            """)
            conn.commit()
    except Exception as e:
        logger.warning(f"Could not ensure evidence table: {e}")


def _get_existing_ticket_ids() -> set:
    """Fetch ticket_ids already in case_memory for dedup. Returns empty set on failure."""
    if not EVIDENCE_DB_URL:
        return set()
    try:
        with psycopg2.connect(EVIDENCE_DB_URL) as conn:
            cur = conn.cursor()
            cur.execute("SELECT rma_ticket_id FROM case_memory WHERE rma_ticket_id IS NOT NULL")
            return {row[0] for row in cur.fetchall()}
    except Exception as e:
        logger.warning(f"Could not fetch existing ticket_ids: {e}")
        return set()


def _resolve_ip_or_empty(hostname: str) -> str:
    """Resolve a hostname to its IP from the node DB. Returns empty string on failure."""
    try:
        from node_operations.db_read import get_node_detail
        detail = get_node_detail(hostname)
        if detail:
            ips = detail.get("ip", [])
            if ips:
                return ips[0]
    except Exception as e:
        logger.warning(f"Could not resolve IP for {hostname}: {e}")
    return ""


def _auto_save_evidence(hostname: str, source: str, content: str,
                        category: str = None, summary: str = None,
                        metadata: dict = None, finding_id: int = None):
    """Save investigation evidence to the agent evidence DB.
    Never raises — logs warning on failure. Does not block the calling tool.
    """
    if not EVIDENCE_DB_URL or not hostname:
        return
    try:
        with psycopg2.connect(EVIDENCE_DB_URL) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO investigation_evidence
                    (node_name, collected_by, source, category, summary, content, metadata, finding_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                hostname,
                AGENT_NAME,
                source,
                category,
                summary,
                content[:50000],  # Cap at 50KB per evidence row
                json.dumps(metadata or {}),
                finding_id,
            ))
            conn.commit()
            logger.info(f"Auto-saved evidence: node={hostname} source={source} finding={finding_id}")
    except Exception as e:
        logger.warning(f"Auto-save evidence failed: {e}")





# ---------------------------------------------------------------------------
# Evidence summary helpers — produce informative one-line summaries
# ---------------------------------------------------------------------------

def _alert_summary(rows: list) -> str:
    """Build summary for alert evidence: count + unique alert names."""
    if not rows:
        return "0 alerts"
    names = []
    for r in rows:
        name = r.get("alertname", "") if isinstance(r, dict) else ""
        if name and name not in names:
            names.append(name)
    return f"{len(rows)} alerts: {', '.join(names)}"


def _job_list_summary(rows: list) -> str:
    """Build summary for job list evidence: count + exit breakdown + names."""
    if not rows:
        return "0 recent jobs"
    failed = sum(1 for r in rows if isinstance(r, dict) and r.get("job_state") == "FAILED")
    running = sum(1 for r in rows if isinstance(r, dict) and r.get("job_state") == "RUNNING")
    header = f"{len(rows)} jobs"
    if failed:
        header += f", {failed} failed"
    if running:
        header += f", {running} running"
    # Add job names when list is short
    if len(rows) <= 5:
        names = []
        for r in rows:
            if isinstance(r, dict):
                name = r.get("job_name", "")
                state = r.get("job_state", "")
                if name:
                    names.append(f"{name} ({state})")
        if names:
            header += ": " + ", ".join(names)
    return header


def _history_summary(result) -> str:
    """Build summary for node history evidence.

    Shows total count + recent status transitions with reasons,
    and counts revalidation cycles.
    """
    if isinstance(result, (list, tuple)):
        n = len(result)

        # Extract recent transitions (newest first in the list)
        transitions = []
        for r in result[:8]:
            action = getattr(r, "Action", "") or (r.get("Action", "") if isinstance(r, dict) else "")
            reason = getattr(r, "Reason", "") or (r.get("Reason", "") if isinstance(r, dict) else "")
            cat = getattr(r, "Category", "") or (r.get("Category", "") if isinstance(r, dict) else "")
            detail = getattr(r, "Detail", {}) or (r.get("Detail", {}) if isinstance(r, dict) else {})

            # Build transition description
            # Action format: "from_status-to_status" e.g. "cordoned-triaged_hardware"
            parts = []
            if action:
                # Show just the transition arrow
                parts.append(action)
            # Condense reason — it can be very long (CordonValidationFailedNodes: ...)
            if reason:
                if len(reason) > 60:
                    reason = reason[:57] + "..."
                parts.append(reason)
            transitions.append(" | ".join(parts) if parts else "unknown")

        # Count revalidation cycles: each "validating-cordoned" with
        # CordonValidationFailedNodes = one failed revalidation
        reval_count = sum(
            1 for r in result
            if (getattr(r, "Reason", "") or (r.get("Reason", "") if isinstance(r, dict) else ""))
            .startswith("CordonValidationFailedNodes")
        )

        # Count RMA submissions
        rma_count = sum(
            1 for r in result
            if (getattr(r, "Reason", "") or (r.get("Reason", "") if isinstance(r, dict) else ""))
            == "InitiateRMA"
        )

        # Build summary
        summary = f"{n} actions"
        if reval_count:
            summary += f", {reval_count} failed revalidations"
        if rma_count:
            summary += f", {rma_count} RMA submissions"

        # Latest transition — include reason or detail summary
        if transitions:
            latest_row = result[0]
            latest_action = getattr(latest_row, "Action", "") or (latest_row.get("Action", "") if isinstance(latest_row, dict) else "")
            latest_reason = getattr(latest_row, "Reason", "") or (latest_row.get("Reason", "") if isinstance(latest_row, dict) else "")
            latest_detail = getattr(latest_row, "Detail", None) or (latest_row.get("Detail") if isinstance(latest_row, dict) else None)

            # Extract detail summary
            detail_summary = ""
            if latest_detail:
                if isinstance(latest_detail, str):
                    try:
                        latest_detail = json.loads(latest_detail)
                    except Exception:
                        pass
                if isinstance(latest_detail, dict):
                    detail_summary = latest_detail.get("summary", "")

            # Build latest description: transition + reason or summary
            latest_parts = [latest_action] if latest_action else []
            if latest_reason:
                if len(latest_reason) > 60:
                    latest_reason = latest_reason[:57] + "..."
                latest_parts.append(latest_reason)
            elif detail_summary:
                if len(detail_summary) > 60:
                    detail_summary = detail_summary[:57] + "..."
                latest_parts.append(detail_summary)

            if latest_parts:
                latest_str = " | ".join(latest_parts)
                summary += f"; latest: {latest_str}"

        return summary

    if isinstance(result, dict):
        actions = result.get("actions", [])
        alerts = result.get("alerts", [])
        parts = [f"{len(actions)} actions"]
        if alerts:
            parts.append(f"{len(alerts)} alerts")
        return ", ".join(parts)
    return "Status/action history"


def _probe_ssh_summary(result: str) -> str:
    """Build summary for probe_ssh evidence: reachable + key findings."""
    if "SSH unreachable" in result:
        return "SSH unreachable"
    parts = ["SSH reachable"]
    # Extract GPU count
    gpu_lines = [l for l in result.split("\n") if "GPU" in l and "NVIDIA" in l]
    if gpu_lines:
        parts.append(f"{len(gpu_lines)} GPUs")
    # Check NVLink issues
    if "inactive" in result.lower() or "0/18" in result:
        parts.append("NVLink issues")
    # Check IB issues
    if "PortDown" in result or "Down" in result:
        parts.append("IB issues")
    # Check Xid / ECC / AER errors
    if "Xid" in result:
        parts.append("Xid errors")
    if "ECC" in result:
        parts.append("ECC errors")
    if "AER" in result:
        parts.append("AER errors")
    return ", ".join(parts)


def _mock_alert_summary(mock: str) -> str:
    """Build summary from mock alert JSON string."""
    try:
        rows = json.loads(mock)
        return _alert_summary(rows)
    except Exception:
        return "alerts (mock)"


def _mock_job_summary(mock: str) -> str:
    """Build summary from mock job list JSON string."""
    try:
        rows = json.loads(mock)
        return _job_list_summary(rows)
    except Exception:
        return "recent jobs (mock)"


def _bmc_summary(command: str, result: str) -> str:
    """Build summary for BMC query evidence."""
    labels = {
        "sel_list": "SEL log",
        "sel_elist": "SEL extended log",
        "chassis_status": "chassis status",
        "sensor_list": "sensor readings",
        "fru": "FRU info",
    }
    n = len([l for l in result.splitlines() if l.strip()])
    label = labels.get(command, command)
    return f"{n} lines {label}"


# ---------------------------------------------------------------------------
# Mock replay support
# ---------------------------------------------------------------------------

def _mock_lookup(tool_name: str, key: str) -> str | None:
    """Look up a mock response for a tool call. Returns None if no mock found."""
    if not REPLAY_MOCK_DIR:
        return None
    safe_key = key.replace("/", "_").replace(":", "_").replace(" ", "_").replace(".", "_")
    path = os.path.join(REPLAY_MOCK_DIR, f"{tool_name}__{safe_key}.json")
    if os.path.isfile(path):
        with open(path) as f:
            data = json.load(f)
        if "error" in data:
            raise RuntimeError(data["error"])
        return data.get("result", "")
    if REPLAY_MOCK_STRICT:
        raise RuntimeError(f"No mock data for {tool_name}({key}). Check {REPLAY_MOCK_DIR}/")
    return None


def create_server(role: str) -> FastMCP:
    allowed = ROLE_TOOLS.get(role)
    mcp = FastMCP("node-operations")

    # ── Global tool failure tracking (circuit breaker) ────────────
    _original_tool = mcp.tool

    def _tracked_tool(*args, **kwargs):
        """Wrap mcp.tool() to catch exceptions and feed circuit breaker."""
        decorator = _original_tool(*args, **kwargs)

        def wrapper(fn):
            import functools

            @functools.wraps(fn)
            def instrumented(*a, **kw):
                try:
                    result = fn(*a, **kw)
                    return result
                except Exception as exc:
                    from node_operations.blast_radius import record_failure as _cb_record_failure
                    _cb_record_failure(fn.__name__)
                    logger.warning("Tool %s failed (circuit breaker recorded): %s", fn.__name__, exc)
                    raise

            return decorator(instrumented)

        return wrapper

    mcp.tool = _tracked_tool

    # --- credentials from env ---
    ssh_user = os.environ.get("SSH_USER", "operator")
    ssh_password = os.environ.get("SSH_PASSWORD", "")
    ssh_timeout = int(os.environ.get("SSH_TIMEOUT", "30"))
    reset_ssh_user = os.environ.get("RESET_SSH_USER", "ubuntu")
    reset_ssh_password = os.environ.get("RESET_SSH_PASSWORD", "")
    bmc_user = os.environ.get("BMC_USER", "root")

    if REPLAY_MOCK_DIR:
        logger.info(f"REPLAY MOCK MODE active: {REPLAY_MOCK_DIR} (strict={REPLAY_MOCK_STRICT})")
    if EVIDENCE_DB_URL:
        logger.info(f"Evidence auto-save enabled: agent={AGENT_NAME}")
        _ensure_evidence_table()

    def _register(name: str):
        return allowed is None or name in allowed

    # ====================================================================
    # READONLY TOOLS
    # ====================================================================

    # ---- get_nodes_by_status ----
    if _register("get_nodes_by_status"):
        @mcp.tool()
        def get_nodes_by_status(status: str, include_alerts: bool = False, category: str = None) -> str:
            """List nodes in a given status.

            When include_alerts=True, enriches each node with alert context
            from alert_records in the validating→triaged time window.

            Args:
                status: Node status to filter by (e.g. triaged_unknown, triaged_hardware, cordoned).
                include_alerts: If True, join with alert_records for alert context.
                category: Filter by node category (e.g. b300, h200, ctrl, storage).
            """
            mock = _mock_lookup("get_nodes_by_status", status)
            if mock is not None:
                return mock
            if include_alerts or category:
                from node_operations.db_read import create_clients, get_nodes_by_status_with_alerts
                _, _, pc = create_clients()
                rows = get_nodes_by_status_with_alerts(pc, status, category=category)
            else:
                from node_operations.db_read import create_clients, get_nodes_by_status as _get
                sc, _, _ = create_clients()
                rows = _get(sc, status)
            return json.dumps(rows, default=str, indent=2)

    # ---- resolve_ip (IP → hostname lookup) ----
    if _register("resolve_ip"):
        @mcp.tool()
        def resolve_ip(ip: str) -> str:
            """Resolve an IP address to its node hostname.

            Searches physical_node_onboard_records for any record where the IP
            matches (checks both ip[] and mgmt_ip[] arrays). Returns the latest
            matching hostname, IP list, BMC IP, and onboard_id.

            Use this when you have an IP but don't know the node name.
            Works for both management IPs and BMC IPs.
            """
            from node_operations.db_read import create_clients, resolve_ip
            _, _, pc = create_clients()
            try:
                result = resolve_ip(pc, ip)
                if not result:
                    return f"No node found with IP {ip}"
                return json.dumps(result, default=str, indent=2)
            except Exception as e:
                return f"Error resolving IP {ip}: {e}"

    # ---- get_node_detail ----
    if _register("get_node_detail"):
        @mcp.tool()
        def get_node_detail(hostname: str = "", onboard_id: int = 0, brief: bool = True) -> str:
            """Get SN, SKU, IPs, category, and hardware summary for a node.

            By default (brief=True), returns a compact version (~28KB vs ~225KB raw).
            ip and mgmt_ip are JSON arrays; use ip[0] for SSH IP.

            Args:
                hostname: Node hostname (preferred lookup).
                onboard_id: Onboard record ID (alternative lookup).
                brief: If True (default), return summarized hardware info.
            """
            mock_key = hostname or str(onboard_id)
            mock = _mock_lookup("get_node_detail", mock_key)
            if mock is not None:
                # Auto-save mock result too
                if hostname:
                    _auto_save_evidence(hostname, "node_detail", mock, summary="node detail (mock)")
                return mock
            from node_operations.db_read import create_clients, get_node_detail as _get, get_node_detail_by_onboard_id
            _, _, pc = create_clients()
            if onboard_id:
                result = get_node_detail_by_onboard_id(pc, onboard_id, brief=brief)
            elif hostname:
                result = _get(pc, hostname, brief=brief)
            else:
                _input_error("provide either hostname or onboard_id")
            result_str = json.dumps(result, default=str, indent=2)
            # Auto-save evidence
            if hostname:
                _auto_save_evidence(hostname, "node_detail", result_str,
                                    summary=f"SN={result.get('sn','?')} category={result.get('category','?')}")
            return result_str

    # ---- get_node_history ----
    if _register("get_node_history"):
        @mcp.tool()
        def get_node_history(hostname: str, include_alerts: bool = False,
                             start_ts: str = "", end_ts: str = "",
                             hours: int = 72, days: int = 90) -> str:
            """Get action/status history for a node.

            When include_alerts=True, also returns alert_records for the time window.

            Args:
                hostname: Node hostname.
                include_alerts: If True, also return alert_records.
                start_ts: Start timestamp (ISO). Use validating_timestamp from
                    get_nodes_by_status for precise triage window.
                end_ts: End timestamp (ISO). Use triaged_timestamp from
                    get_nodes_by_status for precise triage window.
                hours: Lookback hours for alerts when start_ts is empty (default 72).
                days: Lookback days for actions when start_ts is empty (default 30).
            """
            mock = _mock_lookup("get_node_history", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "node_history", mock,
                                    summary="node history (mock)",
                                    category="history")
                return mock
            if include_alerts:
                from node_operations.db_read import create_clients, get_node_history_with_alerts
                _, _, pc = create_clients()
                result = get_node_history_with_alerts(pc, hostname, hours=hours,
                                                       start_ts=start_ts, end_ts=end_ts)
            else:
                from node_operations.db_read import create_clients, get_node_history as _get
                _, ac, _ = create_clients()
                result = _get(ac, hostname, days=days, start_ts=start_ts, end_ts=end_ts)
            result_str = json.dumps(result, default=str, indent=2)
            # Auto-save evidence
            _auto_save_evidence(hostname, "node_history", result_str,
                                summary=_history_summary(result))
            return result_str

    # ---- get_node_status_history ----
    if _register("get_node_status_history"):
        @mcp.tool()
        def get_node_status_history(hostname: str, days: int = 90, limit: int = 20) -> str:
            """Get recent status transitions for a node (available → validating → triaged_unknown etc.).

            Unlike get_node_history (which returns node_actions), this returns
            the raw status timeline from node_status — showing when the node
            moved between states.

            Args:
                hostname: Node hostname.
                days: Lookback window in days (default 30).
                limit: Max rows to return (default 20).
            """
            mock = _mock_lookup("get_node_status_history", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "status_history", mock,
                                    summary="status history (mock)",
                                    category="history")
                return mock
            from node_operations.db_read import create_clients, get_node_status_history as _get
            _, _, pc = create_clients()
            rows = _get(pc, hostname, days=days, limit=limit)
            result_str = json.dumps(rows, default=str, indent=2)
            _auto_save_evidence(hostname, "status_history", result_str,
                                summary=f"status transitions (last {days}d, {len(rows)} rows)",
                                category="history")
            return result_str

    # ---- get_node_alerts ----
    if _register("get_node_alerts"):
        @mcp.tool()
        def get_node_alerts(hostname: str, start_ts: str = "", end_ts: str = "",
                            hours: int = 72, alertname: str = "",
                            include_details: bool = False,
                            deduplicate: bool = True, limit: int = 200) -> str:
            """Get alert_records for a node. Returns deduplicated alerts by default.

            IMPORTANT: Always specify alertname filter when you know which alert
            type you need. Common values:
            - NotifyUnvalidatedNodes (validation failure details with benchmark data)
            - CordonValidationFailedNodes
            - NodeNotReady, NodeUnschedulable, PaiServicePodNotReady

            By default returns one row per unique (alertname, severity, summary)
            with first_seen/last_seen/count — reduces 800+ raw rows to ~20 rows.

            Args:
                hostname: Node hostname.
                start_ts: Start timestamp (ISO). If empty, uses NOW() - hours.
                end_ts: End timestamp (ISO). If empty, uses NOW().
                hours: Lookback hours (default 72). Use 24 for recent, 168 for weekly.
                alertname: Filter by exact alert name. Empty = all types.
                include_details: Include labels/annotations JSONB (3x larger output).
                    Only use when you need structured data from specific alerts.
                deduplicate: Group identical alerts into one row (default True).
                    Set False for raw rows with exact timestamps.
                limit: Max rows to return (default 200).
            """
            mock = _mock_lookup("get_node_alerts", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "alert", mock,
                                    summary=_mock_alert_summary(mock))
                return mock
            from node_operations.db_read import create_clients, get_node_alerts as _get
            _, _, pc = create_clients()
            rows = _get(pc, hostname, start_ts=start_ts, end_ts=end_ts,
                        hours=hours, alertname=alertname,
                        include_details=include_details,
                        deduplicate=deduplicate, limit=limit)
            result_str = json.dumps(rows, default=str, indent=2)
            # Auto-save evidence
            _auto_save_evidence(hostname, "alert", result_str,
                                summary=_alert_summary(rows))
            return result_str

    # ---- get_node_recent_jobs ----
    if _register("get_node_recent_jobs"):
        @mcp.tool()
        def get_node_recent_jobs(hostname: str, start_ts: str = "",
                                 end_ts: str = "", days: int = 14,
                                 limit: int = 20) -> str:
            """Get recent jobs that ran on a specific node.

            Returns jobs discovered via framework_events.sourceHost, joined with
            frameworks and job_summary for exit details.

            IMPORTANT: Use end_ts=triaged_timestamp to get jobs that ran BEFORE
            the node was triaged — more precise than days for investigation.
            Phase 1 (get_nodes_by_status with include_alerts=True) returns
            triaged_timestamp for each node.

            Args:
                hostname: Node hostname.
                start_ts: Start timestamp (ISO). If empty, uses end_ts - days.
                end_ts: End timestamp (ISO). If empty, uses NOW().
                    Set to triaged_timestamp for triage investigation.
                days: Lookback days when start_ts is empty (default 14).
                limit: Max jobs to return (default 20).
            """
            mock = _mock_lookup("get_node_recent_jobs", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "job_list", mock,
                                    summary=_mock_job_summary(mock))
                return mock
            from node_operations.db_read import create_clients, get_node_recent_jobs as _get
            _, _, pc = create_clients()
            rows = _get(pc, hostname, start_ts=start_ts, end_ts=end_ts,
                        days=days, limit=limit)
            result_str = json.dumps(rows, default=str, indent=2)
            # Auto-save evidence
            _auto_save_evidence(hostname, "job_list", result_str,
                                summary=_job_list_summary(rows))
            return result_str

    # ---- get_job_details ----
    if _register("get_job_details"):
        @mcp.tool()
        def get_job_details(jobname: str, limit: int = 5) -> str:
            """Get job details by job name (supports partial match).

            Args:
                jobname: Job name (supports partial match via LIKE).
                limit: Max results (default 5).
            """
            mock = _mock_lookup("get_job_details", jobname)
            if mock is not None:
                return mock
            from node_operations.db_read import create_clients, get_job_details as _get
            _, _, pc = create_clients()
            rows = _get(pc, jobname, limit=limit)
            return json.dumps(rows, default=str, indent=2)

    # ---- get_job_events ----
    if _register("get_job_events"):
        @mcp.tool()
        def get_job_events(job_name: str, limit: int = 20) -> str:
            """Get events for a specific job by job name.

            Args:
                job_name: Human-readable job name (e.g. 'glm-infer_tp32_c7358368').
                    Use the job_name returned by get_node_recent_jobs.
                    NOT the internal frameworkName hash.
                limit: Max events to return (default 20).
            """
            mock = _mock_lookup("get_job_events", job_name)
            if mock is not None:
                return mock
            from node_operations.db_read import create_clients, get_job_events as _get
            _, _, pc = create_clients()
            rows = _get(pc, job_name, limit=limit)
            return json.dumps(rows, default=str, indent=2)

    # ---- get_validation_job ----
    if _register("get_validation_job"):
        @mcp.tool()
        def get_validation_job(hostname: str, limit: int = 5) -> str:
            """Get validation (superbench) jobs targeting a specific node.

            Finds jobs by searching jobConfig for forceNodes=hostname. This finds
            BOTH scheduled AND unscheduled jobs (WAITING/FailedScheduling), unlike
            sourceHost-based queries which only find jobs that already ran.

            Use this to find the validation job name, then inspect with get_job_details,
            get_job_events, or ltp.sh logs.

            Common states:
            - SUCCEEDED: validation passed
            - FAILED: validation ran but benchmarks failed
            - WAITING: never scheduled — check get_job_events for FailedScheduling reason
            - STOPPED: manually stopped

            Args:
                hostname: Node hostname.
                limit: Max validation jobs to return (default 5, most recent first).
            """
            mock = _mock_lookup("get_validation_job", hostname)
            if mock is not None:
                return mock
            from node_operations.db_read import create_clients, get_validation_job as _get
            _, _, pc = create_clients()
            rows = _get(pc, hostname, limit=limit)
            return json.dumps(rows, default=str, indent=2)

    # ---- get_existing_reasons ----
    if _register("get_existing_reasons"):
        @mcp.tool()
        def get_existing_reasons() -> str:
            """Get all existing triage reason values from node_actions."""
            mock = _mock_lookup("get_existing_reasons", "all")
            if mock is not None:
                return mock
            from node_operations.db_read import create_clients, get_existing_reasons as _get
            _, _, pc = create_clients()
            rows = _get(pc)
            return json.dumps(rows, default=str, indent=2)

    # ---- probe_ssh ----
    if _register("probe_ssh"):
        @mcp.tool()
        def probe_ssh(ip: str, hostname: str = None) -> str:
            """Check if a node is SSH-reachable and run basic diagnostics.

            When reachable, also collects:
              - nvidia-smi -L (GPU inventory)
              - nvidia-smi nvlink -s (NVLink status summary)
              - ibstat | head (IB port states)
              - sudo dmesg | tail -30 (recent kernel messages)
              - sudo dmesg | grep -i 'xid|ecc|aer|nvlink|gpu|nvidia|pcie|nvswitch' (GPU/NVLink/PCIe errors)

            When unreachable, returns "SSH unreachable: <ip>".

            Output is automatically saved as evidence if hostname is provided.

            Args:
                ip: Node IP address.
                hostname: Node hostname. If provided, output is auto-saved as evidence.
            """
            mock = _mock_lookup("probe_ssh", ip)
            if mock is not None:
                _auto_save_evidence(hostname or ip, "probe_ssh", mock,
                                    summary="probe_ssh (mock)")
                return mock
            from node_operations.ssh import probe_ssh as _probe, run_remote_command_capture
            reachable = _probe(ip, ssh_user, ssh_timeout)
            if not reachable:
                result = f"SSH unreachable: {ip}"
                _auto_save_evidence(hostname or ip, "probe_ssh", result,
                                    summary="SSH unreachable")
                return result

            parts = [f"SSH reachable: {ip}"]
            diag_commands = [
                ("timeout 120 nvidia-smi -L || echo 'nvidia-smi timed out or failed'", "GPU Inventory"),
                ("timeout 120 nvidia-smi nvlink -s || echo 'nvidia-smi nvlink timed out or failed'", "NVLink Status"),
                ("ibstat | head -20", "IB Port States"),
                ("sudo dmesg | tail -30", "Kernel Messages (last 30)"),
                ("sudo dmesg | grep -i 'xid\\|ecc\\|aer\\|nvlink\\|gpu\\|nvidia\\|pcie\\|nvswitch' | tail -30", "Kernel GPU/NVLink/PCIe Errors"),
            ]
            for cmd, label in diag_commands:
                try:
                    rc, output = run_remote_command_capture(ip, ssh_user, cmd, timeout=15)
                    if output.strip():
                        trimmed = output.strip()[:2000]
                        parts.append(f"\n--- {label} ---\n{trimmed}")
                except Exception as e:
                    parts.append(f"\n--- {label} ---\n[error: {e}]")

            result = "\n".join(parts)
            # Auto-save evidence
            _auto_save_evidence(hostname or ip, "probe_ssh", result,
                                summary=_probe_ssh_summary(result))
            return result

    # ---- run_database_query (safe alternative to raw psql, hides credentials) ----
    if _register("run_database_query"):
        @mcp.tool()
        def run_database_query(query: str, database: str = "platform", params: list = None) -> str:
            """Run a read-only SQL query. Connection strings stay server-side — never exposed.

            Only SELECT queries are allowed. Use params for parameterized queries to prevent injection.
            database: "platform" (POSTGRES_CONNECTION_STR) or "evidence" (EVIDENCE_DB_URL).
            """
            if params is None:
                params = []

            q = query.strip()
            if not q.upper().startswith("SELECT"):
                _input_error("only SELECT queries allowed")

            blocked = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "COPY"]
            for kw in blocked:
                if kw in q.upper():
                    _input_error(f"blocked keyword {kw}")

            import psycopg2
            conn_str = os.environ.get("EVIDENCE_DB_URL" if database == "evidence" else "POSTGRES_CONNECTION_STR", "")
            if not conn_str:
                _input_error(f"no connection string for '{database}'")

            try:
                conn = psycopg2.connect(conn_str)
                # Set search_path so both public and ltp_sdk tables are accessible without prefix
                cur = conn.cursor()
                cur.execute("SET search_path TO public, ltp_sdk")
                cur.execute(query, params)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()
                result = [dict(zip(cols, [str(v) if v is not None else None for v in r])) for r in rows]
                cur.close(); conn.close()
                return json.dumps({"columns": cols, "row_count": len(result), "rows": result}, default=str)
            except Exception as e:
                return f"Query error: {e}"

    # ---- query_rma_cases ----
    if _register("query_rma_cases"):
        @mcp.tool()
        def query_rma_cases(days: int = 60, limit: int = 50, hostname: str = "", ticket_id: str = "") -> str:
            """Query historical InitiateRMA cases with ticket details.

            Args:
                days: Look back N days (default 60)
                limit: Max cases to return (default 50)
                hostname: Filter by specific hostname (optional)
                ticket_id: Filter by specific ticket ID (optional)
            """
            mock_key = hostname or ticket_id or f"{days}d"
            mock = _mock_lookup("query_rma_cases", mock_key)
            if mock is not None:
                return mock
            from node_operations.db_read import create_clients
            _, _, pc = create_clients()
            conditions = ["pna.op_type = 'InitiateRMA'"]
            if days:
                conditions.append(f"pna.\"timestamp\" >= NOW() - INTERVAL '{int(days)} days'")
            if hostname:
                conditions.append(f"pnor.hostname = '{hostname}'")
            if ticket_id:
                conditions.append(f"pna.ticket_id = '{ticket_id}'")
            where = " AND ".join(conditions)
            sql = (
                f"SELECT pna.ticket_id, pna.\"timestamp\" AS triage_timestamp, pna.onboard_id, "
                f"pnor.hostname, pnor.category, pnor.sn, pna.metainfo->>'details' AS details "
                f"FROM ltp_sdk.physical_node_actions pna "
                f"JOIN ltp_sdk.physical_node_onboard_records pnor ON pna.onboard_id = pnor.id "
                f"WHERE {where} "
                f"ORDER BY pna.\"timestamp\" DESC LIMIT {int(limit)}"
            )
            rows = pc.execute_query(sql)
            return json.dumps(rows, default=str, indent=2)

    # ---- query_completed_rmas helpers ----
    def _enrich_with_session_ids(rows):
        """Add sessions to each row via Chat UI DB + meta.json lookup.

        For repair/recycler: Chat UI DB has hostname in title → direct lookup.
        For triage: follow parent_task_id chain back to find the triage session.
        """
        import os as _os
        chat_ui_url = os.environ.get("CHAT_UI_DB_URL", "")
        if not chat_ui_url:
            for row in rows:
                row["sessions"] = []
            return
        try:
            import psycopg2 as _pg2
            conn = _pg2.connect(chat_ui_url)
        except Exception as e:
            logger.warning(f"Cannot connect to Chat UI DB: {e}")
            for row in rows:
                row["sessions"] = []
            return

        def _read_claude_sid(agent_id, gw_id):
            # Map Chat UI agent_id to mount path (triage-unknown → triage)
            mount_agent = "triage" if agent_id in ("triage-unknown", "triage") else agent_id
            meta_path = f"/mnt/sessions/{mount_agent}/{gw_id}/meta.json"
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                return meta.get("claudeSessionId") or meta.get("claude_session_id", "")
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return ""

        def _make_session(gw_id, agent_id, created_at):
            return {
                "agent": agent_id,
                "gateway_session_id": gw_id,
                "claude_session_id": _read_claude_sid(agent_id, gw_id),
                "created_at": str(created_at),
            }

        try:
            cur = conn.cursor()
            for row in rows:
                hostname = row.get("hostname", "")
                if not hostname:
                    row["sessions"] = []
                    continue
                # 1. Find sessions with hostname in title (repair, recycler)
                cur.execute(
                    "SELECT gateway_session_id, agent_id, created_at FROM sessions "
                    "WHERE title LIKE %s ORDER BY created_at DESC",
                    (f"%{hostname}%",)
                )
                sessions = []
                seen_gw_ids = set()
                earliest_repair_time = None
                for gw_id, agent_id, created_at in cur.fetchall():
                    sessions.append(_make_session(gw_id, agent_id, created_at))
                    seen_gw_ids.add(gw_id)
                    if earliest_repair_time is None or created_at < earliest_repair_time:
                        earliest_repair_time = created_at

                # 2. Follow parent_task_id chain (fast path)
                needs_scan = True
                if sessions:
                    gw_ids = [s["gateway_session_id"] for s in sessions]
                    cur.execute(
                        "SELECT t.id, t.parent_task_id, pt.session_id, "
                        "  ps.gateway_session_id, ps.agent_id, ps.created_at "
                        "FROM tasks t "
                        "JOIN sessions s ON t.session_id = s.id "
                        "LEFT JOIN tasks pt ON t.parent_task_id = pt.id "
                        "LEFT JOIN sessions ps ON pt.session_id = ps.id "
                        "WHERE s.gateway_session_id = ANY(%s) AND t.parent_task_id IS NOT NULL",
                        (gw_ids,)
                    )
                    parent_rows = cur.fetchall()
                    for _, _, _, parent_gw_id, parent_agent_id, parent_created_at in parent_rows:
                        if parent_gw_id and parent_gw_id not in seen_gw_ids:
                            sessions.append(_make_session(parent_gw_id, parent_agent_id, parent_created_at))
                            seen_gw_ids.add(parent_gw_id)
                    if parent_rows:
                        needs_scan = False

                # 3. Fallback: scan triage transcripts before earliest repair time
                #    Only keep the CLOSEST triage session before repair (most likely
                #    the one that actually triaged this node and led to delegation).
                if needs_scan and hostname:
                    short = ""
                    for part in hostname.split("-"):
                        if part.startswith(("h200", "b300", "storage", "ctrl")):
                            short = "-".join(hostname.split("-")[-2:])
                            break
                    search_terms = [hostname] + ([short] if short else [])
                    if earliest_repair_time:
                        cur.execute(
                            "SELECT gateway_session_id, agent_id, created_at FROM sessions "
                            "WHERE agent_id IN ('triage-unknown', 'triage') "
                            "AND created_at < %s ORDER BY created_at DESC",
                            (earliest_repair_time,)
                        )
                    else:
                        cur.execute(
                            "SELECT gateway_session_id, agent_id, created_at FROM sessions "
                            "WHERE agent_id IN ('triage-unknown', 'triage') ORDER BY created_at DESC"
                        )
                    for gw_id, agent_id, created_at in cur.fetchall():
                        if gw_id in seen_gw_ids:
                            continue
                        claude_sid = _read_claude_sid(agent_id, gw_id)
                        if not claude_sid:
                            continue
                        # Map Chat UI agent_id to mount path (triage-unknown → triage)
                        mount_agent = "triage" if agent_id in ("triage-unknown", "triage") else agent_id
                        # Transcript may be directly or under a workspace subdirectory
                        transcript_base = f"/mnt/transcripts/{mount_agent}"
                        candidates = [
                            f"{transcript_base}/{claude_sid}.jsonl",
                            f"{transcript_base}/-app-workspace/{claude_sid}.jsonl",
                        ]
                        transcript_path = None
                        for c in candidates:
                            if _os.path.isfile(c):
                                transcript_path = c
                                break
                        if not transcript_path:
                            continue
                        try:
                            with open(transcript_path, errors="replace") as f:
                                for line in f:
                                    if any(t in line for t in search_terms):
                                        sessions.append(_make_session(gw_id, agent_id, created_at))
                                        seen_gw_ids.add(gw_id)
                                        break  # found in this file
                        except OSError:
                            pass
                        # Only keep the closest (most recent) triage session
                        if seen_gw_ids and any(s["agent"] in ("triage-unknown", "triage") for s in sessions if s["gateway_session_id"] == gw_id):
                            break  # stop scanning older triage sessions

                row["sessions"] = sessions
        finally:
            conn.close()

    # ---- query_completed_rmas ----
    if _register("query_completed_rmas"):
        @mcp.tool()
        def query_completed_rmas(days: int = 60, hostname: str = "", ticket_id: str = "", skip_existing: bool = True) -> str:
            """Query completed RMAs with ALL data needed for case_memory insert.

            Returns vendor repair info + our triage classification + alert types + SKU
            + claude_session_id in a single query. No additional tool calls needed.

            Args:
                days: Look back N days (default 60)
                hostname: Filter by specific hostname (optional)
                ticket_id: Filter by specific ticket ID (optional)
                skip_existing: Exclude ticket_ids already in case_memory (default True).
                    Set to False to re-fetch for re-classification.
            """
            mock_key = hostname or ticket_id or f"{days}d_completed"
            mock = _mock_lookup("query_completed_rmas", mock_key)
            if mock is not None:
                return mock
            from node_operations.db_read import create_clients
            _, _, pc = create_clients()
            conditions = ["comp.op_type = 'CompleteRMA'"]
            if days:
                conditions.append(f"comp.\"timestamp\" >= NOW() - INTERVAL '{int(days)} days'")
            if hostname:
                conditions.append(f"pnor.hostname = '{hostname}'")
            if ticket_id:
                conditions.append(f"comp.ticket_id = '{ticket_id}'")
            where = " AND ".join(conditions)
            sql = (
                f"SELECT comp.ticket_id, comp.\"timestamp\" AS rma_completed_at, comp.onboard_id, "
                f"pnor.hostname, pnor.category, pnor.sn, "
                # Vendor fields
                f"comp.metainfo->'details'->'data'->>'repairStatus' AS repair_status, "
                f"comp.metainfo->'details'->'data'->>'machine_replace_sn' AS replace_sn, "
                f"comp.metainfo->'details'->'data'->>'remark' AS remark, "
                f"(SELECT jsonb_agg(elem->>'op') FROM jsonb_array_elements(CASE WHEN jsonb_typeof(comp.metainfo->'details'->'data'->'processingInfos') = 'array' THEN comp.metainfo->'details'->'data'->'processingInfos' ELSE '[]'::jsonb END) elem) AS vendor_ops, "
                f"(SELECT jsonb_agg(elem->>'solution') FROM jsonb_array_elements(CASE WHEN jsonb_typeof(comp.metainfo->'details'->'data'->'processingInfos') = 'array' THEN comp.metainfo->'details'->'data'->'processingInfos' ELSE '[]'::jsonb END) elem) AS vendor_solutions, "
                # Our classification: the transition INTO triaged_hardware/unknown (not the exit to deallocated)
                f"tri.action AS our_action, "
                f"tri.reason AS our_reason, "
                f"tri.detail AS our_detail, "
                # Alert types from the triage window
                f"alert_types.types AS alert_types, "
                # SKU from node onboard record category
                f"pnor.category AS sku "
                f"FROM ltp_sdk.physical_node_actions comp "
                f"JOIN ltp_sdk.physical_node_onboard_records pnor ON comp.onboard_id = pnor.id "
                # Our triage classification
                f"LEFT JOIN LATERAL ("
                f"  SELECT na.action, na.reason, na.detail "
                f"  FROM ltp_sdk.node_actions na "
                f"  WHERE na.hostname = pnor.hostname "
                f"    AND na.\"timestamp\" < comp.\"timestamp\" "
                f"    AND (na.action LIKE '%%triaged_hardware%%' OR na.action LIKE '%%triaged_unknown%%') "
                f"    AND na.action NOT LIKE '%%deallocated%%' "
                f"  ORDER BY na.\"timestamp\" DESC LIMIT 1"
                f") tri ON TRUE "
                # Alert types during triage window
                f"LEFT JOIN LATERAL ("
                f"  SELECT jsonb_agg(DISTINCT ar.alertname) AS types "
                f"  FROM ltp_sdk.alert_records ar "
                f"  WHERE ar.node_name = pnor.hostname "
                f"    AND ar.\"timestamp\" BETWEEN (comp.\"timestamp\" - INTERVAL '7 days') AND comp.\"timestamp\""
                f") alert_types ON TRUE "
                f"WHERE {where} "
                f"ORDER BY comp.\"timestamp\" DESC"
            )
            rows = pc.execute_query(sql)
            # Enrich with claude_session_id from session metas
            _enrich_with_session_ids(rows)
            # Dedup: exclude ticket_ids already in case_memory
            if skip_existing and EVIDENCE_DB_URL:
                existing = _get_existing_ticket_ids()
                before = len(rows)
                rows = [r for r in rows if r.get("ticket_id") not in existing]
                if before != len(rows):
                    logger.info(f"query_completed_rmas: skipped {before - len(rows)} already-inserted tickets")
            return json.dumps(rows, default=str, indent=2)


    # ====================================================================
    # DIAGNOSIS TOOLS
    # ====================================================================

    # ---- check_fabricmanager ----
    if _register("check_fabricmanager"):
        @mcp.tool()
        def check_fabricmanager(ip: str, hostname: str = None) -> str:
            """Check nvidia-fabricmanager status on a node.

            Output is automatically saved as evidence if hostname is provided.

            Args:
                ip: Node IP address.
                hostname: Node hostname. If provided, output is auto-saved as evidence.
            """
            mock = _mock_lookup("check_fabricmanager", ip)
            if mock is not None:
                _auto_save_evidence(hostname or ip, "fabricmanager", mock,
                                    category="gpu", summary="fabricmanager (mock)")
                return mock
            from node_operations.bmc import check_fabricmanager as _check
            ok = _check(ip, ssh_user, ssh_timeout)
            result = f"fabricmanager {'running' if ok else 'NOT running'} on {ip}"
            _auto_save_evidence(hostname or ip, "fabricmanager", result,
                                category="gpu", summary=result)
            return result

    # ---- bmc_query ----
    if _register("bmc_query"):
        @mcp.tool()
        def bmc_query(bmc_ip: str, hostname: str = None,
                      command: str = "sel_list", record_id: str = None,
                      since: str = None, until: str = None,
                      grep: str = None) -> str:
            """Query BMC (Baseboard Management Controller) remotely via ipmitool.

            Uses remote ipmitool (lanplus) to query the BMC — does NOT require
            SSH access to the node. Critical for investigating unreachable/crashed nodes.

            Commands:
              - sel_list: IPMI System Event Log (SEL) — hardware error events
                (memory ECC, PCIe AER, thermal, power, fan). Most useful for RMA evidence.
              - sel_elist: SEL with extended timestamps + sensor data
              - sel_info: SEL metadata (entry count, last add/delete time, overflow)
              - sel_time_get: BMC SEL clock, usually UTC
              - sel_get: Detailed single SEL record. Requires record_id.
              - chassis_status: Power state, boot flags, watchdog
              - chassis_poh: Power-on-hours counter
              - mc_watchdog_get: BMC watchdog configuration/status
              - sensor_list: Current sensor readings (temperatures, voltages, fan speeds)
              - fru: Field Replaceable Unit info (serial numbers, part numbers)

            Optional filters:
              - since/until: UTC time window for sel_list/sel_elist, e.g.
                "2026-06-21 16:00:00 UTC" or "2026-06-21".
              - grep: Case-insensitive regex applied to output, e.g. "Power|Unknown|PS".

            Requires BMC_PASSWORD env var. Output is auto-saved as evidence.

            Args:
                bmc_ip: BMC management IP address (e.g. <bmc-ip>).
                hostname: Node hostname. If provided, output is auto-saved as evidence.
                command: Query type: sel_list, sel_elist, sel_info, sel_time_get,
                    sel_get, chassis_status, chassis_poh, mc_watchdog_get,
                    sensor_list, fru.
                record_id: SEL record ID for command=sel_get, for example "84".
                since: Optional UTC start time for filtering SEL output.
                until: Optional UTC end time for filtering SEL output.
                grep: Optional case-insensitive regex filter for output.
            """
            mock = _mock_lookup("bmc_query", bmc_ip)
            if mock is not None:
                _auto_save_evidence(hostname or bmc_ip, "bmc", mock,
                                    category="bmc", summary="bmc_query (mock)")
                return mock

            bmc_pw = os.environ.get("BMC_PASSWORD", "")
            bmc_reset_pw = os.environ.get("RESET_BMC_PASSWORD", "")
            bmc_user = os.environ.get("BMC_USER", "root")
            if not bmc_pw and not bmc_reset_pw:
                return "BMC_PASSWORD not set — cannot query BMC remotely."

            from node_operations.bmc import bmc_sel_query
            # Try combinations of user × password.
            # AMI MegaRAC (b300) uses 'admin' for IPMI; Supermicro uses 'root'.
            # After reset, password may be RESET_BMC_PASSWORD instead of BMC_PASSWORD.
            users = list(dict.fromkeys([bmc_user, "admin", "root"]))  # dedup, preserve order
            passwords = [p for p in [bmc_pw, bmc_reset_pw] if p]
            last_error = None
            for user in users:
                for pw in passwords:
                    try:
                        result = bmc_sel_query(
                            bmc_ip, user, pw, command=command, timeout=15,
                            record_id=record_id, since=since, until=until, grep=grep,
                        )
                        if "Error" not in result and "Unable" not in result:
                            result_text = result or "No BMC output matched the query."
                            result_text = result_text[:10000]  # Cap at 10KB
                            _auto_save_evidence(hostname or bmc_ip, "bmc", result_text,
                                                category="bmc",
                                                summary=_bmc_summary(command, result_text))
                            return result_text
                        last_error = result
                    except FileNotFoundError as e:
                        raise FileNotFoundError(
                            "ipmitool not installed; cannot query BMC. Install ipmitool package."
                        ) from e
                    except (RuntimeError, Exception) as e:
                        last_error = str(e)
                        continue
            raise RuntimeError(f"BMC query failed: tried all user/password combinations. Last error: {last_error}")

    # ---- bmc_screenshot ----
    if _register("bmc_screenshot"):
        @mcp.tool()
        def bmc_screenshot(bmc_ip: str, hostname: str = None) -> str:
            """Capture a BMC KVM console screenshot (remote, no SSH needed).

            Uses AMI BMC REST API to capture the current KVM console display.
            Essential for crashed/unresponsive nodes — shows kernel panic,
            BIOS errors, POST failures, or other console output.

            The screenshot is saved to the evidence DB as base64.
            Requires BMC_PASSWORD env var. Output is auto-saved as evidence.

            Args:
                bmc_ip: BMC management IP address (e.g. <bmc-ip>).
                hostname: Node hostname. If provided, output is auto-saved as evidence.
            """
            mock = _mock_lookup("bmc_screenshot", bmc_ip)
            if mock is not None:
                _auto_save_evidence(hostname or bmc_ip, "bmc_screenshot", mock,
                                    category="bmc", summary="bmc_screenshot (mock)")
                return mock

            bmc_pw = os.environ.get("BMC_PASSWORD", "")
            bmc_reset_pw = os.environ.get("RESET_BMC_PASSWORD", "")
            bmc_user = os.environ.get("BMC_USER", "root")
            if not bmc_pw and not bmc_reset_pw:
                return "BMC_PASSWORD not set — cannot capture screenshot. Set BMC_PASSWORD env var."

            from node_operations.bmc import get_kvm_screenshot
            import base64
            # Try combinations of user × password.
            # get_kvm_screenshot internally tries admin+root for AMI BMCs,
            # but we also vary passwords (BMC_PASSWORD / RESET_BMC_PASSWORD).
            passwords = [p for p in [bmc_pw, bmc_reset_pw] if p]
            users = list(dict.fromkeys([bmc_user, "admin", "root"]))
            last_error = None
            for user in users:
                for pw in passwords:
                    try:
                        path = get_kvm_screenshot(bmc_ip, user, pw,
                                                  output_path="/tmp/bmc_kvm.jpg")
                        with open(path, "rb") as f:
                            img_data = f.read()
                        b64 = base64.b64encode(img_data).decode()
                        _auto_save_evidence(hostname or bmc_ip, "bmc_screenshot", b64,
                                            category="bmc",
                                            summary=f"BMC screenshot from {bmc_ip} ({len(img_data)} bytes)")
                        return f"BMC screenshot captured from {bmc_ip} ({len(img_data)} bytes)"
                    except Exception as e:
                        last_error = str(e)
                        continue
            raise RuntimeError(f"BMC screenshot failed: tried all user/password combinations. Last error: {last_error}")

    # ---- bmc_health_log ----
    if _register("bmc_health_log"):
        @mcp.tool()
        def bmc_health_log(bmc_ip: str, category: str = "h200",
                           severity: str = "all", limit: int = 100,
                           hostname: str = None) -> str:
            """Fetch BMC Health Event Log via Redfish API (remote, no SSH needed).

            Returns structured hardware events from the BMC: GPU not present,
            NVSwitch errors, NIC temperature critical, PCIe errors, power supply
            events, LAN link down, etc. More human-readable than raw ipmitool SEL.

            IMPORTANT: Check BOTH bmc_health_log AND bmc_query(sel_list) — they
            cover different event types. Redfish Health Log has GPU/NVSwitch/NIC
            events; IPMI SEL has memory ECC, PCIe AER, thermal thresholds.

            Endpoint varies by BMC type:
            - Supermicro (h200): /redfish/v1/Systems/1/LogServices/Log1/Entries
              Auth: root:password (Basic auth)
            - AMI MegaRAC (b300): /redfish/v1/Managers/Self/LogServices/SEL/Entries
              + GPUEventLog. Auth: admin:password (Basic auth)

            Output is auto-saved as evidence.

            Args:
                bmc_ip: BMC management IP address (e.g. <bmc-ip>).
                category: Node category — "h200" (Supermicro) or "b300" (AMI MegaRAC).
                    Determines which Redfish endpoint and auth to use.
                severity: Filter — "all" (default), "Critical", "Warning", or "OK".
                limit: Maximum entries to return (default 100, max 500).
                hostname: Node hostname. If provided, output is auto-saved as evidence.
            """
            mock = _mock_lookup("bmc_health_log", bmc_ip)
            if mock is not None:
                _auto_save_evidence(hostname or bmc_ip, "bmc_health_log", mock,
                                    category="bmc", summary="bmc_health_log (mock)")
                return mock

            bmc_pw = os.environ.get("BMC_PASSWORD", "")
            bmc_reset_pw = os.environ.get("RESET_BMC_PASSWORD", "")
            if not bmc_pw and not bmc_reset_pw:
                return "BMC_PASSWORD not set — cannot query health log. Set BMC_PASSWORD env var."

            # Select user based on BMC type
            if category == "b300":
                bmc_user = os.environ.get("BMC_USER", "admin")
            else:
                bmc_user = os.environ.get("BMC_USER", "root")

            from node_operations.bmc import bmc_health_log as _bmc_health_log
            limit = min(limit, 500)
            # Try combinations of user × password.
            # AMI MegaRAC (b300) uses 'admin'; Supermicro uses 'root'.
            # After reset, password may be RESET_BMC_PASSWORD instead of BMC_PASSWORD.
            users = list(dict.fromkeys([bmc_user, "admin", "root"]))
            passwords = [p for p in [bmc_pw, bmc_reset_pw] if p]
            last_error = None
            for user in users:
                for pw in passwords:
                    try:
                        entries = _bmc_health_log(bmc_ip, user, pw,
                                                  category=category, severity=severity,
                                                  limit=limit)
                        # bmc_health_log swallows HTTP 401 and returns [].
                        # Treat empty results as auth failure and try next password.
                        if not entries:
                            last_error = f"empty result (likely auth failure) for {user}:{pw[:3]}***"
                            continue
                        result = json.dumps(entries, indent=2, default=str)[:20000]
                        _auto_save_evidence(hostname or bmc_ip, "bmc_health_log", result,
                                            category="bmc",
                                            summary=f"BMC health log ({len(entries)} entries, {severity})")
                        return result
                    except Exception as e:
                        last_error = str(e)
                        continue
            raise RuntimeError(f"BMC health log failed: tried all user/password combinations. Last error: {last_error}")

    # ---- move_node_status ----
    if _register("move_node_status"):
        @mcp.tool()
        def move_node_status(hostname: str, node_id: str,
                             from_status: str, to_status: str,
                             reason: str, detail: str) -> str:
            """Move a node between statuses. Records transition metadata.
            Rejects transitions FROM 'available' — nodes must go through
            alert-manager (cordon) before status changes."""
            if from_status == "available":
                return _domain_result(
                    "transition_denied",
                    "Cannot move from 'available'. Use cordon via alert-manager first.",
                    hostname=hostname,
                    node_id=node_id,
                    from_status=from_status,
                    to_status=to_status,
                )
            mock = _mock_lookup("move_node_status", hostname)
            if mock is not None:
                return mock
            from node_operations.db import create_clients, insert_status_transition
            sc, ac, _ = create_clients()
            category = "hardware" if "hardware" in to_status or to_status == "ua" else "platform"
            insert_status_transition(
                status_client=sc, action_client=ac,
                hostname=hostname, node_id=node_id,
                from_status=from_status, to_status=to_status,
                reason=reason, detail=detail, category=category,
            )
            return f"moved {hostname}: {from_status} -> {to_status}"

    # ---- submit_validation ----
    if _register("submit_validation"):
        @mcp.tool()
        def submit_validation(hostname: str, summary: str = "",
                              dry_run: bool = False) -> str:
            """Submit a revalidation request for a node via Alert Manager API.

            Sends an admin-validate-node alert which triggers the platform to
            schedule and run a validation (superbench) job on the node.
            This is the correct way to trigger revalidation after a platform
            repair fix — it both creates the validation job AND moves the
            node to 'validating' status through the platform pipeline.

            Use AFTER confirming the fix is working (probe_ssh, run_ssh_command).
            The platform will schedule a superbench job on the node and
            automatically transition the node to 'validating' -> 'cordoned'/'allocated'.

            Args:
                hostname: Node hostname to revalidate.
                summary: Optional reason for revalidation (e.g. 'Revalidation after daemonset pod deletion').
                dry_run: If True, show what would be sent without actually sending.
            """
            mock = _mock_lookup("submit_validation", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "submit_validation", mock,
                                    category="action", summary="submit_validation (mock)")
                return mock
            from node_operations.alert_manager import submit_validation as _submit
            result = _submit(hostname, summary=summary, dry_run=dry_run)
            _auto_save_evidence(hostname, "submit_validation",
                                json.dumps(result),
                                category="action",
                                summary=f"Validation request: status={result.get('status')}, "
                                        f"alerts={result.get('alert_count', 0)}")
            return json.dumps(result, indent=2)

    # ---- execute_node_action ----
    if _register("execute_node_action"):
        @mcp.tool()
        def execute_node_action(hostname: str, action: str,
                                triaged_label: str = "triaged_hardware",
                                summary: str = "") -> str:
            """Execute a physical node action via the Alert Manager pipeline.

            Use this to explicitly cordon or drain a node — independent of
            classification. The alert-handler receives the action label and
            executes the corresponding k8s operation (NoSchedule taint, pod eviction).

            This is the single authoritative path for cordon/drain — do NOT use
            kubectl cordon/drain directly, as that bypasses the platform pipeline
            and leaves the status DB out of sync.

            Args:
                hostname: Node hostname.
                action: 'cordon' (NoSchedule taint, stop new workloads),
                        'drain' (evict existing pods off the node), or
                        'alert' (notify only, no k8s action).
                triaged_label: Target triage label for the alert
                               (e.g. 'triaged_hardware', 'triaged_platform').
                summary: Human-readable reason for this action (shown in logs).
            """
            if action not in ("cordon", "drain", "alert"):
                _input_error(f"Invalid action '{action}'. Must be cordon/drain/alert.")

            # ── Circuit breaker check (D1) ──────────────────────────
            from node_operations.blast_radius import check_circuit as _cb_check
            cb_ok, cb_reason = _cb_check(action)
            if not cb_ok:
                return json.dumps({
                    "blocked": True,
                    "reason": cb_reason,
                    "action": action,
                    "hostname": hostname,
                    "instruction": "Circuit breaker is open for this action type. "
                                   "Wait for cooldown or escalate to a human."
                })

            # ── Blast-radius check (C1) ──────────────────────────────
            from node_operations.blast_radius import check_action as _blast_check
            permitted, reason = _blast_check(action, hostnames=[hostname])
            if not permitted:
                return json.dumps({
                    "blocked": True,
                    "reason": reason,
                    "action": action,
                    "hostname": hostname,
                    "instruction": "This action exceeds blast-radius policy limits. "
                                   "Escalate to a human with elevated permissions or "
                                   "reduce the scope of the action."
                })

            mock = _mock_lookup("execute_node_action", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "execute_node_action", mock,
                                    category="action",
                                    summary=f"execute_node_action {action} (mock)")
                return mock

            from node_operations.alert_manager import submit_triage_alert as _submit
            result = _submit(hostname, triaged_label=triaged_label,
                             alert_name=f"agent_node_action_{action}",
                             summary=summary or f"Agent requested {action} on {hostname}")

            from node_operations.blast_radius import record_action as _blast_record
            from node_operations.blast_radius import start_effect_window as _start_window
            _blast_record(action, hostname)
            _start_window(action, hostname)  # C3: 10-min post-action monitor

            _auto_save_evidence(hostname, "execute_node_action",
                                json.dumps(result),
                                category="action",
                                summary=f"execute_node_action {action}: status={result.get('status')}")

            # ── Command-executed check instruction (C4) ─────────────
            output = {
                "status": result.get("status", "unknown"),
                "action": action,
                "hostname": hostname,
                "blast_radius_checked": True,
                "verify_instruction": (
                    f"Action dispatched via Alert Manager. After 30-60 seconds, "
                    f"call verify_node_action('{hostname}', 'cordon') to confirm "
                    f"the cordon took effect (node SchedulingDisabled, pods drained)."
                )
            }
            output.update(result)
            return json.dumps(output, indent=2)

    # ---- verify_node_action (C4) ----
    if _register("verify_node_action"):
        @mcp.tool()
        def verify_node_action(hostname: str, expected_action: str = "cordon") -> str:
            """Verify that a node action (cordon/drain) actually took effect.

            After dispatching execute_node_action, the platform handles the k8s
            operation asynchronously via Alert Manager. Call this tool after
            30-60 seconds to confirm the action landed correctly.

            For cordon: checks that the node has SchedulingDisabled taint.
            For drain: checks that no non-DaemonSet pods remain on the node.

            Args:
                hostname: Node hostname to verify.
                expected_action: The action that was dispatched ('cordon' or 'drain').
            """
            import subprocess
            mock = _mock_lookup("verify_node_action", hostname)
            if mock is not None:
                return mock

            # Check via kubectl: get node status
            try:
                node_json = subprocess.run(
                    ["kubectl", "get", "node", hostname, "-o", "json"],
                    capture_output=True, text=True, timeout=30
                )
                if node_json.returncode != 0:
                    raise RuntimeError(f"kubectl failed: {node_json.stderr.strip()}")

                node_info = json.loads(node_json.stdout)
                spec = node_info.get("spec", {})
                status = node_info.get("status", {})
                conditions = status.get("conditions", [])

                # Check unschedulable (cordon taint)
                unschedulable = spec.get("unschedulable", False)

                # Check for remaining non-DaemonSet pods
                # (We can't get pods from kubectl get node, but we check conditions)
                ready_condition = next(
                    (c for c in conditions if c.get("type") == "Ready"),
                    {}
                )

                verified = unschedulable
                evidence = {
                    "hostname": hostname,
                    "unschedulable": unschedulable,
                    "ready_status": ready_condition.get("status", "unknown"),
                    "conditions": [
                        {"type": c.get("type"), "status": c.get("status")}
                        for c in conditions
                        if c.get("type") in ("Ready", "MemoryPressure", "DiskPressure", "PIDPressure")
                    ],
                }

                # ── Circuit breaker: record outcome (D1) ────────────────
                from node_operations.blast_radius import record_failure as _cb_fail, record_success as _cb_ok

                if verified:
                    _cb_ok(expected_action)
                    summary = f"CORDON VERIFIED: {hostname} is SchedulingDisabled"
                else:
                    _cb_fail(expected_action)
                    summary = f"CORDON NOT VERIFIED: {hostname} is still schedulable. Action may not have taken effect."

                _auto_save_evidence(hostname, "verify_node_action",
                                    json.dumps(evidence),
                                    category="action",
                                    summary=summary)

                return json.dumps({
                    "verified": verified,
                    "expected_action": expected_action,
                    "evidence": evidence,
                    "summary": summary,
                    "instruction": (
                        "Cordon confirmed. Node is SchedulingDisabled."
                        if verified else
                        "Cordon did NOT take effect. Check alert-manager logs. "
                        "Do NOT proceed with further actions on this node."
                    )
                }, indent=2)

            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("kubectl timed out after 30s") from exc
            except Exception as exc:
                _raise_tool_error(exc)

    # ---- submit_triage_alert ----
    if _register("submit_triage_alert"):
        @mcp.tool()
        def submit_triage_alert(hostname: str, triaged_label: str,
                                alert_name: str, summary: str = "",
                                dry_run: bool = False) -> str:
            """Submit a triage transition alert via Alert Manager API.

            This is the Alert Manager path for status transitions — an alternative
            to move_node_status (which writes directly to DB). The Alert Manager
            path also triggers the platform's automated response (cordon, etc.).

            Prefer this over move_node_status when you want the platform to
            handle the full transition pipeline (cordon + revalidation scheduling).

            Args:
                hostname: Node hostname.
                triaged_label: Target triage label (e.g. 'triaged_hardware').
                alert_name: Alert name / reason (e.g. 'PCIeBandwidthDegradation').
                summary: Optional summary annotation.
                dry_run: If True, show what would be sent without actually sending.
            """
            mock = _mock_lookup("submit_triage_alert", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "submit_triage_alert", mock,
                                    category="action", summary="submit_triage_alert (mock)")
                return mock
            from node_operations.alert_manager import submit_triage_alert as _submit
            result = _submit(hostname, triaged_label=triaged_label,
                             alert_name=alert_name, summary=summary, dry_run=dry_run)
            _auto_save_evidence(hostname, "submit_triage_alert",
                                json.dumps(result),
                                category="action",
                                summary=f"Triage alert: {triaged_label}/{alert_name}, "
                                        f"status={result.get('status')}")
            return json.dumps(result, indent=2)

    # ---- get_agent_active_tasks ----
    if _register("get_agent_active_tasks"):
        @mcp.tool()
        def get_agent_active_tasks(agent_id: str = "repair") -> str:
            """List active (running / waiting_input / busy) tasks for a target agent.

            Use this before delegating to check if the target agent already has
            an active task for a specific node. Extract hostnames from the returned
            task titles and skip those nodes in your triage workflow — they are
            already being handled.

            Typical usage in triage:
              1. Call get_agent_active_tasks(agent_id="repair")
              2. Extract hostnames from task titles
              3. Remove those hostnames from your to-triage list
              4. Investigate and delegate only the remaining nodes

            Args:
                agent_id: Target agent ID (default: "repair").
            """
            from node_operations.delegation import get_agent_active_tasks as _get_tasks
            try:
                tasks = _get_tasks(agent_id)
                # Extract hostnames from titles for convenience
                from node_operations.delegation import _extract_hostname
                hostnames = set()
                for t in tasks:
                    h = _extract_hostname(t.get("title", "") or "")
                    if h:
                        hostnames.add(h)
                return json.dumps({
                    "ok": True,
                    "agent_id": agent_id,
                    "active_task_count": len(tasks),
                    "hostnames_with_active_tasks": sorted(hostnames),
                    "tasks": [
                        {
                            "id": t.get("id"),
                            "title": t.get("title"),
                            "status": t.get("status"),
                            "session_id": t.get("session_id"),
                            "completion_mode": t.get("completion_mode"),
                        }
                        for t in tasks
                    ],
                }, indent=2)
            except Exception as e:
                _raise_tool_error(e)

    # ---- delegate_to_agent ----
    if _register("delegate_to_agent"):
        @mcp.tool()
        def delegate_to_agent(agent_id: str, prompt: str, title: str = "", completion_mode: str = "manual") -> str:
            """Delegate work to another agent via incidara-console gateway.

            Creates a new session + task on the target agent and sends the prompt
            to the gateway. The target agent picks it up and starts working.

            Auto-dedup: if a hostname is detected in the title/prompt and the
            target agent already has an active task (running / waiting_input / busy)
            for that same hostname, the call returns the existing task info instead
            of creating a duplicate. The response will have "duplicate": true.

            Use this to hand off confirmed triaged_hardware nodes to the repair agent,
            or to escalate work to any other agent in the system.

            Fire-and-forget: the target agent works independently. Check progress
            via the Chat UI or by asking the user.

            Requires CHAT_UI_URL, CHAT_UI_USER, CHAT_UI_PASSWORD env vars.

            Args:
                agent_id: Target agent ID (e.g. "repair", "ticket-replay").
                prompt: The full prompt/task to delegate. Should include --hostname
                    so auto-dedup can detect duplicates.
                title: Optional session title (e.g. "Hardware: h200-001157 GPU ECC").
                    Should include the hostname for auto-dedup to work.
                completion_mode: "manual" (task stays waiting_input until user dismisses — default,
                    recommended for hardware repair which requires user approval before destructive
                    actions) or "auto" (task auto-completes when agent finishes — use only for
                    read-only or non-destructive tasks like ticket drafting).
            """
            from node_operations.delegation import delegate_to_agent as _delegate
            try:
                result = _delegate(agent_id, prompt, title=title, completion_mode=completion_mode)
                session_id = result.get("session", {}).get("id", "?")
                task_id = result.get("task", {}).get("id", "?")
                is_duplicate = result.get("duplicate", False)

                if is_duplicate:
                    existing_status = result.get("existing_task", {}).get("status", "?")
                    existing_title = result.get("existing_task", {}).get("title", "?")
                    return json.dumps({
                        "ok": True,
                        "duplicate": True,
                        "agent_id": agent_id,
                        "session_id": session_id,
                        "task_id": task_id,
                        "existing_task_status": existing_status,
                        "title": existing_title,
                        "message": (
                            f"Skipped delegation to {agent_id}: already has an active "
                            f"task (id={task_id}, status={existing_status}) for this hostname. "
                            f"Title: {existing_title}"
                        ),
                    }, indent=2)

                summary = f"Delegated to {agent_id}: session={session_id} task={task_id}"
                # Auto-save delegation as evidence if we can determine hostname
                # (prompt typically contains hostname)
                _auto_save_evidence(agent_id, "delegate_to_agent",
                                    json.dumps(result),
                                    category="action",
                                    summary=summary)
                return json.dumps({
                    "ok": True,
                    "duplicate": False,
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "task_id": task_id,
                    "title": title,
                    "completion_mode": completion_mode,
                    "message": f"Delegated to {agent_id}. Session {session_id}, task {task_id}. "
                               f"The agent will work independently. Check Chat UI for progress.",
                }, indent=2)
            except Exception as e:
                _raise_tool_error(e)

    # ====================================================================
    # OPS TOOLS
    # ====================================================================

    # ---- reset_node ----
    if _register("reset_node"):
        @mcp.tool(timeout=660)
        def reset_node(hostname: str, ip: str,
                        mgmt_ips: str = "", category: str = "h200", ctx: Context = None) -> str:
            """Reset a node: if SSH reachable, run full 3-stage reset (teardown, create_debug_user, clear).
            If SSH unreachable, reset BMC password remotely so the vendor can access BMC when they
            receive the node for physical repair.

            Before resetting, stops any stale validation jobs (WAITING/RUNNING)
            for this node via PAI API. This prevents stuck jobs from interfering
            with the node after it comes back online.

            When SSH is reachable: runs teardown.sh → create_debug_user.sh → clear_node.sh.
            When SSH is unreachable: resets BMC user password remotely via set_bmc_password_remote.sh
            to the vendor-access password (RESET_BMC_PASSWORD). The vendor needs BMC access to
            diagnose and repair the node. On-box reset is skipped since the vendor will handle
            the node physically.

            Args:
                hostname: Node hostname.
                ip: Node IP address.
                mgmt_ips: Comma-separated BMC IP addresses (needed for remote BMC password reset
                    when SSH is unreachable). From get_node_detail's mgmt_ip field.
                category: Node category (h200, b300, etc.). Used to determine BMC user
                    (admin for b300, root for h200).
            """
            mock = _mock_lookup("reset_node", hostname)
            if mock is not None:
                return mock
            # Stop stale validation jobs before reset (for reset, stop ALL non-terminal jobs)
            from node_operations.alert_manager import _check_active_validation
            check = _check_active_validation(hostname)
            # For reset, also stop any active running job (< 6h) since we're about to wipe the node
            all_stopped = list(check["stopped_jobs"])
            if check["has_active_running"]:
                # Force-stop the active job too — reset will invalidate it anyway
                from node_operations.alert_manager import _get_token
                token = _get_token()
                rest_base = os.environ.get("LTP_HOST", "").rstrip("/") + "/rest-server/api/v2"
                if not rest_base or rest_base == "/rest-server/api/v2":
                    _input_error("LTP_HOST env var not set")
                fw_id = check.get("active_job_fw_id", "")
                if fw_id:
                    try:
                        stop_url = f"{rest_base}/jobs/{fw_id}/executionType"
                        payload = json.dumps({"value": "STOP"}).encode()
                        req = urllib.request.Request(stop_url, data=payload, method="PUT",
                            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            resp.read()
                        all_stopped.append(fw_id)
                    except Exception:
                        pass
            stopped_info = f" (stopped {len(all_stopped)} validation jobs)" if all_stopped else ""
            from node_operations.reset import run_reset
            from node_operations.bmc import reset_bmc_password
            from node_operations.ssh import probe_ssh
            reset_timeout = int(os.environ.get("RESET_SSH_TIMEOUT", "600"))
            bmc_password = os.environ.get("BMC_PASSWORD", "")
            reset_bmc_pw = os.environ.get("RESET_BMC_PASSWORD", "")

            # Check SSH reachability first
            if probe_ssh(ip, ssh_user, timeout=15, password=os.environ.get("SSH_PASSWORD", "")):
                # SSH reachable — run full on-box reset
                run_reset(
                    hostname=hostname, ip=ip,
                    ssh_user=ssh_user, ssh_password=os.environ.get("SSH_PASSWORD", ""),
                    reset_ssh_user=os.environ.get("RESET_SSH_USER", "ubuntu"),
                    reset_ssh_password=os.environ.get("RESET_SSH_PASSWORD", ""),
                    bmc_password=bmc_password,
                    reset_bmc_password=reset_bmc_pw,
                    timeout=reset_timeout,
                )
                return f"reset complete: {hostname}{stopped_info}"
            else:
                # SSH unreachable — reset BMC password remotely
                if not mgmt_ips:
                    return (f"SSH unreachable and no mgmt_ips provided for {hostname}. "
                            f"Cannot reset BMC password remotely. Provide mgmt_ips from get_node_detail.")
                bmc_ip_list = [m.strip() for m in mgmt_ips.split(",") if m.strip()]
                if not bmc_ip_list:
                    return f"SSH unreachable and mgmt_ips empty for {hostname}. Cannot reset BMC password remotely."
                bmc_user = "admin" if category == "b300" else "root"
                try:
                    reset_bmc_password(bmc_ip_list, bmc_user, bmc_password, reset_bmc_pw)
                    return (f"SSH unreachable: BMC password reset to vendor-access password for {hostname} "
                            f"(BMC IPs: {', '.join(bmc_ip_list)}, user: {bmc_user}). On-box reset skipped — "
                            f"vendor will handle physically.{stopped_info}")
                except Exception as e:
                    raise RuntimeError(
                        f"SSH unreachable and BMC password reset also failed for {hostname}: {e}. "
                        "Node may need physical intervention."
                    ) from e

    # ---- submit_rma_ticket ----
    if _register("submit_rma_ticket"):
        @mcp.tool()
        def submit_rma_ticket(hostname: str, sn: str,
                              mgmt_ip: str, ip: str, summary: str, reproducer: str) -> str:
            """Submit an RMA ticket and move node to ua status."""
            mock = _mock_lookup("submit_rma_ticket", hostname)
            if mock is not None:
                return mock
            from node_operations.ticket import build_ticket_description, submit_ticket
            from node_operations.db import create_clients, insert_rma_transition
            from node_operations.types import TicketConfig
            config = TicketConfig(
                base_url=os.environ.get("TICKET_BASE_URL", ""),
                auth_znsl=os.environ.get("TICKET_AUTH_ZNSL", ""),
                timeout=int(os.environ.get("TICKET_TIMEOUT", "15")),
            )
            desc = build_ticket_description(sn=sn, mgmt_ip=mgmt_ip, ip=ip,
                                             hostname=hostname, summary=summary, reproducer=reproducer)
            tid, payload = submit_ticket(sn=sn, description=desc, config=config)
            sc, ac, pc = create_clients()
            # Always look up the LATEST onboard_id and node_id from DB.
            # get_ticket_id_for_node returns stale onboard_id from prior RMAs
            # after node re-onboarding. node_id in node_status/node_actions
            # must match the latest physical_node_onboard_records.id.
            from node_operations.db import get_node_detail
            detail = get_node_detail(pc, hostname)
            if detail is None:
                return _domain_result(
                    "not_found",
                    f"no onboard record found for {hostname}",
                    hostname=hostname,
                    entity="onboard_record",
                )
            onboard_id = detail["id"]
            node_id = str(onboard_id)  # node_id in node_actions/node_status IS the onboard_id
            insert_rma_transition(
                status_client=sc, action_client=ac, physical_node_client=pc,
                hostname=hostname, onboard_id=onboard_id,
                ticket_id=tid, description=desc, detail=f"{summary}\n{reproducer}",
            )
            return f"ticket {tid} submitted for {hostname}, moved to ua"

    # ---- run_config_stage ----
    if _register("run_config_stage"):
        @mcp.tool()
        def run_config_stage(hostname: str, bundle: str, script: str,
                             ip: str = "", ssh_user: str = "",
                             ssh_password: str = "", bmc_password: str = "",
                             timeout: int = 0) -> str:
            """Run a SINGLE bundle/script stage on a node.

            This is the building block of run_full_config. Use it to:
            - Manually retry a failed stage without re-running the whole pipeline
            - Debug a stage by running it individually and inspecting output
            - Run custom/experimental stages for new node types
            - Step through the pipeline one stage at a time

            Steps: SCP bundle to /tmp/ → run script → cleanup /tmp/bundle.
            The script receives 3 positional args: ssh_password, bmc_password, hostname.

            Available bundles and scripts (use list_stages to see per-category pipelines):
            - init_bundle: create_user.sh, harden_node.sh, clear_node.sh
            - software_bundle: install_general.sh, install_h200.sh, install_b300.sh, install_cpu.sh, install_storage.sh
            - config_bundle: config_apt.sh, config_raid.sh, config_h200.sh, config_b300.sh, config_cpu.sh, config_storage.sh, check_raid.sh

            After reset_node, only ubuntu exists — this tool auto-detects which user can SSH.
            """
            mock = _mock_lookup("run_config_stage", hostname)
            if mock is not None:
                return mock
            from node_operations.init_stage import run_init_stage as _run_stage
            from node_operations.ssh import probe_ssh
            from node_operations.db import create_clients as _cc

            _ssh_user = ssh_user or os.environ.get("SSH_USER", "operator")
            _ssh_password = ssh_password or os.environ.get("SSH_PASSWORD", "")
            _reset_ssh_user = os.environ.get("RESET_SSH_USER", "ubuntu")
            _reset_ssh_password = os.environ.get("RESET_SSH_PASSWORD", "")
            _bmc_password = bmc_password or os.environ.get("BMC_PASSWORD", "")
            _timeout = timeout or int(os.environ.get("SSH_TIMEOUT", "300"))

            # Resolve IP if not provided
            _ip = ip
            if not _ip:
                try:
                    _sc, _ac, _pc = _cc()
                    from node_operations.db_read import get_node_detail
                    record = get_node_detail(_pc, hostname)
                    if record:
                        ip_list = record.get("ip", [])
                        _ip = ip_list[0] if isinstance(ip_list, list) else ip_list
                except Exception:
                    pass
            if not _ip:
                return _domain_result(
                    "not_found",
                    f"no IP provided and could not resolve for {hostname}",
                    hostname=hostname,
                    entity="ip_address",
                )

            # Auto-detect available SSH user (after reset_node, only ubuntu exists)
            if not ssh_user:
                if probe_ssh(_ip, _reset_ssh_user, timeout=15, password=_reset_ssh_password):
                    _ssh_user = _reset_ssh_user
                    _ssh_password = _reset_ssh_password
                    logger.info("run_config_stage: using %s (reset user)", _reset_ssh_user)
                elif probe_ssh(_ip, os.environ.get("SSH_USER", "operator"), timeout=15,
                               password=os.environ.get("SSH_PASSWORD", "")):
                    _ssh_user = os.environ.get("SSH_USER", "operator")
                    _ssh_password = os.environ.get("SSH_PASSWORD", "")
                    logger.info("run_config_stage: using %s (ssh user)", _ssh_user)
                else:
                    raise RuntimeError(f"neither operator nor ubuntu can SSH to {_ip}")

            try:
                _run_stage(hostname=hostname, ip=_ip, ssh_user=_ssh_user,
                           ssh_password=_ssh_password, bmc_password=_bmc_password,
                           timeout=_timeout, bundle=bundle, script=script)
                return f"Stage {bundle}/{script} completed on {hostname}"
            except Exception as e:
                raise RuntimeError(f"Stage {bundle}/{script} failed on {hostname}: {e}") from e

    # ---- get_ticket_status ----
    if _register("get_ticket_status"):
        @mcp.tool()
        def get_ticket_status(ticket_id: str) -> str:
            """Check the status of an RMA ticket."""
            mock = _mock_lookup("get_ticket_status", ticket_id)
            if mock is not None:
                return mock
            from node_operations.ticket import get_ticket_status as _get
            from node_operations.types import TicketConfig
            config = TicketConfig(
                base_url=os.environ.get("TICKET_BASE_URL", ""),
                auth_znsl=os.environ.get("TICKET_AUTH_ZNSL", ""),
                timeout=int(os.environ.get("TICKET_TIMEOUT", "15")),
            )
            return json.dumps(_get(ticket_id, config), default=str, indent=2)

    # ---- complete_rma ----
    if _register("complete_rma"):
        @mcp.tool()
        def complete_rma(hostname: str, ticket_id: str) -> str:
            """Complete an RMA: fetch vendor ticket data, insert CompleteRMA record, move node ua -> ready_ua.

            Call when the vendor ticket shows terminal repair status (completed/repaired).
            Automatically fetches the full vendor API response and stores it in
            physical_node_actions.metainfo.details — consistent with the original
            node_pipeline.py insert_complete_rma.

            All three DB writes (physical_node_actions, node_actions, node_status)
            are executed in a single transaction.

            Args:
                hostname: Node hostname.
                ticket_id: Vendor ticket ID (from check_completed_tickets or query_rma_cases).
            """
            mock = _mock_lookup("complete_rma", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "complete_rma", mock,
                                    category="action", summary=f"RMA completed (mock)")
                return mock
            from node_operations.db import create_clients, insert_complete_rma, get_latest_action_by_state
            from node_operations.db_read import get_node_detail
            from node_operations.ticket import get_ticket_status as _get_ticket_status
            from node_operations.types import TicketConfig

            # Fetch full vendor API response — same as original node_pipeline get_ticket()
            config = TicketConfig(
                base_url=os.environ.get("TICKET_BASE_URL", ""),
                auth_znsl=os.environ.get("TICKET_AUTH_ZNSL", ""),
                timeout=int(os.environ.get("TICKET_TIMEOUT", "15")),
            )
            ticket_payload = _get_ticket_status(ticket_id, config)
            repair_status = ticket_payload.get("data", {}).get("repairStatus", "")

            sc, ac, pc = create_clients()
            # Look up latest onboard_id (stale IDs from prior RMAs are invalid)
            detail = get_node_detail(pc, hostname)
            if detail is None:
                return _domain_result(
                    "not_found",
                    f"no onboard record found for {hostname}",
                    hostname=hostname,
                    entity="onboard_record",
                )
            onboard_id = detail["id"]
            node_id = str(onboard_id)
            # Get the latest ua action detail for the transition record
            latest = get_latest_action_by_state(ac, hostname, node_id, "ua")
            detail_str = ""
            if latest:
                detail_str = latest.Detail if hasattr(latest, "Detail") else str(latest.get("Detail", ""))
            # db_write.py wraps as {"version": 1, "details": ticket_payload} — same as original pipeline
            insert_complete_rma(
                status_client=sc, action_client=ac, physical_node_client=pc,
                hostname=hostname, node_id=node_id, onboard_id=onboard_id,
                ticket_id=ticket_id, ticket_payload=ticket_payload, detail=detail_str,
            )
            _auto_save_evidence(hostname, "complete_rma",
                                json.dumps({"ticket_id": ticket_id, "repair_status": repair_status,
                                            "onboard_id": onboard_id}),
                                category="action",
                                summary=f"RMA completed: ticket={ticket_id} status={repair_status}")
            return f"RMA completed for {hostname}: ticket={ticket_id}, status={repair_status}, moved ua -> ready_ua"

    # ---- scale_k8s_node ----
    if _register("scale_k8s_node"):
        @mcp.tool(timeout=1800)
        def scale_k8s_node(hostname: str, category: str = "h200") -> str:
            """Scale a node into the k8s cluster via kubespray-service.
            This calls the kubespray-service API to run scale.yml, which registers
            the node in k8s, configures kubelet, and joins the cluster.
            Skips if the node is already Ready.
            After scale-up, restarts containerd and deletes nvidia-device-plugin /
            job-exporter pods so they respawn and re-detect GPUs.
            This is a standalone version of the k8s section in run_full_config.
            """
            from node_operations.config import _k8s_scale_node, _k8s_restart_device_pods
            try:
                _k8s_scale_node(hostname)
                # Restart device pods after scale-up (same as run_full_config pipeline)
                if category in ("h200", "b300"):
                    try:
                        _k8s_restart_device_pods(hostname)
                    except Exception as e:
                        logger.warning("k8s restart device pods failed for %s (non-fatal): %s", hostname, e)
                return json.dumps({
                    "status": "succeeded",
                    "hostname": hostname,
                    "message": f"Node {hostname} scaled into k8s cluster, device pods restarted",
                })
            except RuntimeError as e:
                _raise_tool_error(e)

    # ---- run_full_config ----
    if _register("run_full_config"):
        @mcp.tool(timeout=10800)
        def run_full_config(hostname: str, ip: str, category: str = "h200", ctx: Context = None) -> str:
            """Run the full node configuration pipeline (7 stages + k8s).

            Stages: create_user -> install -> harden -> config_apt (collect sysinfo)
            -> config_raid (reboot) -> install_category -> config_category -> k8s scale.
            This is the reallocation pipeline — run after reset_node for nodes
            in ready_ua status that need full re-provisioning.

            Args:
                hostname: Node hostname.
                ip: Node IP address.
                category: Node category: h200 or b300 (default h200).
            """
            mock = _mock_lookup("run_full_config", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "run_full_config", mock,
                                    category="action", summary="full config (mock)")
                return mock
            from node_operations.config import run_full_config as _run
            from node_operations.db import create_clients as _cc
            from node_operations.ssh import probe_ssh
            _bmc_ip = ""
            try:
                _sc, _ac, _pc = _cc()
                _record = _pc.get_node_id_by_hostname(hostname)
                if _record:
                    _node_rows = _pc.execute_query(
                        f"SELECT bmc_ip, mgmt_ip FROM ltp_sdk.physical_node_onboard_records WHERE hostname = '{hostname}' ORDER BY id DESC LIMIT 1"
                    )
                    if _node_rows:
                        _raw = _node_rows[0].get("bmc_ip") or _node_rows[0].get("mgmt_ip", "")
                        _bmc_ip = _raw[0] if isinstance(_raw, list) else _raw
            except Exception:
                pass
            try:
                # After reset_node, only ubuntu exists — probe which user is reachable
                _available_user = ""
                _available_password = ""
                if probe_ssh(ip, reset_ssh_user, timeout=15, password=reset_ssh_password):
                    _available_user = reset_ssh_user
                    _available_password = reset_ssh_password
                    logger.info("run_full_config: using %s (reset user) for first stage", reset_ssh_user)
                elif probe_ssh(ip, ssh_user, timeout=15, password=ssh_password):
                    _available_user = ssh_user
                    _available_password = ssh_password
                    logger.info("run_full_config: using %s (ssh user) for first stage", ssh_user)
                else:
                    raise RuntimeError(
                        f"Full config failed for {hostname}: neither {ssh_user} nor {reset_ssh_user} can SSH to {ip}"
                    )

                _run(
                    hostname=hostname, ip=ip, category=category,
                    ssh_user=ssh_user, ssh_password=ssh_password,
                    reset_ssh_user=reset_ssh_user, reset_ssh_password=reset_ssh_password,
                    bmc_password=os.environ.get("BMC_PASSWORD", ""),
                    timeout=int(os.environ.get("SSH_TIMEOUT", "300")),
                    k8s_master_user=os.environ.get("K8S_MASTER_USER", ""),
                    k8s_master_ip=os.environ.get("K8S_MASTER_IP", ""),
                    bmc_ip=_bmc_ip, bmc_user=os.environ.get("BMC_USER", "admin"),
                    bmc_pass=os.environ.get("BMC_PASSWORD", ""),
                    available_user=_available_user,
                    available_password=_available_password,
                )
                _auto_save_evidence(hostname, "run_full_config",
                                    json.dumps({"category": category, "status": "success"}),
                                    category="action",
                                    summary=f"Full config pipeline completed for {hostname}")
                return f"Full config pipeline completed for {hostname}"
            except Exception as e:
                _auto_save_evidence(hostname, "run_full_config",
                                    json.dumps({"category": category, "status": "failed",
                                                "error": str(e)}),
                                    category="action",
                                    summary=f"Full config FAILED for {hostname}: {str(e)[:200]}")
                raise RuntimeError(f"Full config failed for {hostname}: {e}") from e

    # ---- list_stages ----
    if _register("list_stages"):
        @mcp.tool()
        def list_stages(category: str = "") -> str:
            """List available init stages and their full pipeline order.

            Without a category: lists ALL available bundles and scripts.
            With a category (h200, b300, cpu, ctrl, storage): shows the
            complete ordered pipeline that run_full_config would execute,
            including auto-triggered steps (sync_node_time, check_sku,
            reboot_and_wait) and which standalone tool to use for each
            when stepping manually.

            Use this to plan manual stage-by-stage execution or to discover
            scripts available for new node types.
            """
            from node_operations.init_stage import _BUNDLES_ROOT
            from node_operations.config import _CATEGORY_PIPELINES, _STAGE_TRIGGERS

            lines = []

            if category:
                if category not in _CATEGORY_PIPELINES:
                    return f"Unknown category '{category}'. Available: {', '.join(sorted(_CATEGORY_PIPELINES.keys()))}"
                pipeline = _CATEGORY_PIPELINES[category]
                lines.append(f"## Pipeline for category: {category}")
                lines.append("")

                # Build the full stage list from pipeline data + triggers
                rows = []
                step = 1
                for bundle, script in pipeline["stages"]:
                    rows.append((str(step), bundle, script, "", ""))
                    # Check for auto-triggered steps after this stage
                    triggers = _STAGE_TRIGGERS.get((bundle, script), [])
                    for i, (auto_name, auto_tool) in enumerate(triggers):
                        sub = chr(ord('a') + i)
                        tool_resolved = auto_tool.replace("{category}", category)
                        rows.append((f"{step}{sub}", "", f"{auto_name} (auto)", tool_resolved, "yes"))
                    step += 1

                # K8s steps (always present)
                k8s_steps = [
                    ("k8s-a", "", "delete_pods (force)", "scale_k8s_node", ""),
                    ("k8s-b", "", "scale-up (kubespray)", "scale_k8s_node", ""),
                ]
                if pipeline.get("k8s_device_pods"):
                    k8s_steps.append(("k8s-c", "", "restart_device_pods", "run_kubectl", ""))
                rows.extend(k8s_steps)

                lines.append("| Step | Bundle | Script / Action | Standalone Tool | Auto? |")
                lines.append("|------|--------|-----------------|-----------------|-------|")
                for step_num, b, s, tool, auto in rows:
                    lines.append(f"| {step_num} | {b} | {s} | {tool} | {auto} |")
                lines.append("")
                lines.append("Auto steps run inside run_full_config but NOT inside run_config_stage.")
                lines.append("When stepping manually, use the Standalone Tool column.")

            else:
                # List ALL bundles and scripts
                lines.append("## All Available Bundles and Scripts")
                lines.append("")
                if _BUNDLES_ROOT.is_dir():
                    for bundle_dir in sorted(_BUNDLES_ROOT.iterdir()):
                        if bundle_dir.is_dir():
                            scripts = sorted(s.name for s in bundle_dir.iterdir()
                                             if s.is_file() and not s.name.startswith('.'))
                            lines.append(f"### {bundle_dir.name}/")
                            for s in scripts:
                                lines.append(f"  - {s}")
                            lines.append("")
                cats = ', '.join(sorted(_CATEGORY_PIPELINES.keys()))
                lines.append(f"Categories with defined pipelines: {cats}")
                lines.append("Use list_stages(category='h200') to see the ordered pipeline.")

            return "\n".join(lines)

    # ---- check_sku ----
    if _register("check_sku"):
        @mcp.tool()
        def check_sku(hostname: str, ip: str = "", category: str = "h200") -> str:
            """Collect system info from node and validate against SKU spec.

            Runs on the node: ltp_bundle/collect_system_info.sh (installs deps, collects
            hardware info into /tmp/sbsysinfo/), then downloads results and runs
            gen_sku.py to match against the SKU spec for the node's category.

            Returns:
            - On success: the matched SKU string (e.g. 'h200_48c2_128g16_h200x8_ib400g8_rc400g2')
            - On mismatch: the full failure detail showing which asserts failed for each
              candidate SKU, so you can see exactly what hardware is different

            This is what run_full_config does automatically after config_apt.sh.
            Use this tool when stepping through stages manually with run_config_stage,
            or to re-check SKU after hardware changes without re-running the whole pipeline.

            Prerequisites: config_apt.sh must have run (needs pip/python installed on node).
            """
            mock = _mock_lookup("check_sku", hostname)
            if mock is not None:
                return mock
            from node_operations.sysinfo import collect_sbsysinfo, read_sku, read_sys_info
            from node_operations.ssh import probe_ssh
            from node_operations.db import create_clients as _cc
            import tempfile

            # Resolve IP if not provided
            _ip = ip
            if not _ip:
                try:
                    _sc, _ac, _pc = _cc()
                    from node_operations.db_read import get_node_detail
                    record = get_node_detail(_pc, hostname)
                    if record:
                        ip_list = record.get("ip", [])
                        _ip = ip_list[0] if isinstance(ip_list, list) else ip_list
                except Exception:
                    pass
            if not _ip:
                return _domain_result(
                    "not_found",
                    f"no IP provided and could not resolve for {hostname}",
                    hostname=hostname,
                    entity="ip_address",
                )

            # Auto-detect SSH user
            _ssh_user = ssh_user
            _ssh_password = os.environ.get("SSH_PASSWORD", "")
            _reset_ssh_user = os.environ.get("RESET_SSH_USER", "ubuntu")
            _reset_ssh_password = os.environ.get("RESET_SSH_PASSWORD", "")
            if probe_ssh(_ip, _ssh_user, timeout=15, password=_ssh_password):
                pass  # use default operator
            elif probe_ssh(_ip, _reset_ssh_user, timeout=15, password=_reset_ssh_password):
                _ssh_user = _reset_ssh_user
                _ssh_password = _reset_ssh_password
            else:
                raise RuntimeError(f"neither operator nor ubuntu can SSH to {_ip}")

            base_dir = tempfile.mkdtemp(prefix="sku_check_")
            try:
                # Step 1: Collect sbsysinfo from node
                collect_sbsysinfo(
                    hostname=hostname, ip=_ip, category=category,
                    ssh_user=_ssh_user, ssh_password=_ssh_password,
                    bmc_password=os.environ.get("BMC_PASSWORD", ""),
                    timeout=int(os.environ.get("SSH_TIMEOUT", "300")),
                    base_dir=base_dir,
                )
                # Step 2: Read SKU result
                try:
                    sku = read_sku(_ip, base_dir=base_dir)
                    _auto_save_evidence(hostname, "check_sku",
                                        json.dumps({"status": "match", "sku": sku}),
                                        category="action",
                                        summary=f"SKU check passed: {sku}")
                    return f"SKU check PASSED for {hostname}: {sku}"
                except RuntimeError as e:
                    # SKU mismatch — return the full detail so agent can diagnose
                    detail = str(e)
                    _auto_save_evidence(hostname, "check_sku",
                                        json.dumps({"status": "mismatch", "detail": detail}),
                                        category="action",
                                        summary=f"SKU check FAILED for {hostname}")
                    return f"SKU check FAILED for {hostname}:\n{detail}"
            except Exception as e:
                raise RuntimeError(f"SKU check error for {hostname}: collection failed: {e}") from e

    # ---- collect_sysinfo ----
    if _register("collect_sysinfo"):
        @mcp.tool()
        def collect_sysinfo(hostname: str, ip: str,
                            category: str = "h200") -> str:
            """Collect system info (sbsysinfo) and read SKU, hardware details, and serial.

            Runs collect_system_info.sh on the node, downloads results,
            and reads SKU, hardware info, and BMC serial number.
            Used after full config to gather fresh hardware data for onboard record cloning.

            Args:
                hostname: Node hostname.
                ip: Node IP address.
                category: Node category: h200 or b300 (default h200).
            """
            mock = _mock_lookup("collect_sysinfo", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "collect_sysinfo", mock,
                                    category="action", summary="sysinfo (mock)")
                return mock
            from node_operations.sysinfo import collect_sbsysinfo, read_sku, read_sys_info
            from node_operations.bmc import get_sn_from_ipmi
            import tempfile
            base_dir = tempfile.mkdtemp(prefix="sbsysinfo_")
            try:
                collect_sbsysinfo(
                    hostname=hostname, ip=ip, category=category,
                    ssh_user=ssh_user, ssh_password=ssh_password,
                    bmc_password=os.environ.get("BMC_PASSWORD", ""),
                    timeout=int(os.environ.get("SSH_TIMEOUT", "30")),
                    base_dir=base_dir,
                )
                sku = read_sku(ip, base_dir=base_dir)
                sys_info = read_sys_info(ip, base_dir=base_dir)
            except Exception as e:
                raise RuntimeError(f"sysinfo collection failed for {hostname}: {e}") from e
            # Serial from BMC FRU (best effort)
            sn = ""
            try:
                sn = get_sn_from_ipmi(ip, user=ssh_user)
            except Exception:
                pass
            result = {"hostname": hostname, "sn": sn, "sku": sku, "sys_info": sys_info}
            _auto_save_evidence(hostname, "collect_sysinfo",
                                json.dumps(result, default=str),
                                category="action",
                                summary=f"sysinfo: sn={sn} sku={sku}")
            return json.dumps(result, default=str, indent=2)

    # ---- clone_and_allocate ----
    if _register("clone_and_allocate"):
        @mcp.tool()
        def clone_and_allocate(hostname: str, sn: str,
                               sku: str, sys_info: str) -> str:
            """Clone onboard record with fresh hardware info and move node ready_ua -> allocated_ua.

            Creates a new physical_node_onboard_records entry with updated SN/SKU/sysinfo,
            then transitions the node from ready_ua to allocated_ua.
            All DB writes are transactional.

            Args:
                hostname: Node hostname.
                sn: Serial number (from collect_sysinfo or bmc_query fru).
                sku: SKU string (from collect_sysinfo).
                sys_info: JSON string of hardware info (from collect_sysinfo).
            """
            mock = _mock_lookup("clone_and_allocate", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "clone_and_allocate", mock,
                                    category="action", summary="clone+allocate (mock)")
                return mock
            from node_operations.db import create_clients, clone_onboard_record, insert_allocated_ua
            sc, ac, pc = create_clients()
            try:
                sys_info_dict = json.loads(sys_info) if isinstance(sys_info, str) else sys_info
            except json.JSONDecodeError:
                _input_error(f"sys_info is not valid JSON: {sys_info[:200]}")
            try:
                new_id = clone_onboard_record(pc, hostname, sn, sku, sys_info_dict)
                insert_allocated_ua(
                    status_client=sc, action_client=ac, physical_node_client=pc,
                    hostname=hostname, new_onboard_id=new_id,
                )
                _auto_save_evidence(hostname, "clone_and_allocate",
                                    json.dumps({"new_onboard_id": new_id, "sn": sn, "sku": sku}),
                                    category="action",
                                    summary=f"Cloned onboard + allocated: new_id={new_id}")
                return f"Cloned onboard record (id={new_id}) and moved {hostname}: ready_ua -> allocated_ua"
            except Exception as e:
                _auto_save_evidence(hostname, "clone_and_allocate",
                                    json.dumps({"error": str(e)}),
                                    category="action",
                                    summary=f"clone+allocate FAILED: {str(e)[:200]}")
                raise RuntimeError(f"Clone and allocate failed for {hostname}: {e}") from e

    # ====================================================================
    # PIPELINE WRAPPERS (recycler-agent: batch operations)
    # ====================================================================

    # ---- get_ua_nodes_with_tickets ----
    if _register("get_ua_nodes_with_tickets"):
        @mcp.tool()
        def get_ua_nodes_with_tickets() -> str:
            """Fetch all 'ua' nodes with their InitiateRMA ticket IDs.

            Returns a list of {hostname, node_id, onboard_id, ticket_id} for each
            ua node that has an InitiateRMA record. Nodes without a ticket are
            included with ticket_id=null.

            Step 1 of ticket-check workflow. Next: call get_ticket_status() per node,
            then complete_rma() or move_node_status() based on the result.
            """
            mock = _mock_lookup("get_ua_nodes_with_tickets", "all")
            if mock is not None:
                return mock
            from node_operations.db import create_clients, get_nodes_by_status
            from node_operations.db_read import get_ticket_id_for_node

            sc, _, pc = create_clients()
            nodes = get_nodes_by_status(sc, "ua")
            if not nodes:
                return json.dumps({"nodes": [], "count": 0})

            result = []
            for node_rec in nodes:
                hostname = node_rec.HostName
                node_id = node_rec.NodeId
                try:
                    ticket_id, onboard_id = get_ticket_id_for_node(pc, hostname)
                    result.append({
                        "hostname": hostname,
                        "node_id": node_id,
                        "onboard_id": onboard_id,
                        "ticket_id": ticket_id,
                    })
                except Exception as e:
                    result.append({
                        "hostname": hostname,
                        "node_id": node_id,
                        "onboard_id": None,
                        "ticket_id": None,
                        "error": str(e),
                    })
            return json.dumps({"nodes": result, "count": len(result)}, default=str, indent=2)

    # ---- check_completed_tickets ----
    if _register("check_completed_tickets"):
        @mcp.tool()
        def check_completed_tickets() -> str:
            """Check all ua nodes for completed or anomalous RMA tickets.

            Fetches all ua nodes, looks up their RMA ticket IDs, then polls the
            vendor ticket API for each. Returns ONLY actionable nodes:

            - **completed**: ticket repairStatus is terminal (completed/repaired).
              Call complete_rma(hostname, ticket_id, repair_status) to close.
            - **anomalous**: ticket repairStatus is abnormal (rejected/cancelled/on-hold).
              Call move_node_status to escalate to triaged_unknown.
            - **no_ticket**: node is ua but has no RMA ticket record.
              Call move_node_status to escalate to triaged_unknown.

            In-progress tickets (repairing/pending/etc.) are NOT returned — they
            will be checked again on the next run.
            """
            mock = _mock_lookup("check_completed_tickets", "all")
            if mock is not None:
                return mock
            from node_operations.db import create_clients, get_nodes_by_status
            from node_operations.db_read import get_ticket_id_for_node
            from node_operations.ticket import get_ticket_status as _get_ticket_status
            from node_operations.types import TicketConfig

            config = TicketConfig(
                base_url=os.environ.get("TICKET_BASE_URL", ""),
                auth_znsl=os.environ.get("TICKET_AUTH_ZNSL", ""),
                timeout=int(os.environ.get("TICKET_TIMEOUT", "15")),
            )

            sc, _, pc = create_clients()
            nodes = get_nodes_by_status(sc, "ua")
            if not nodes:
                return json.dumps({"completed": [], "anomalous": [], "no_ticket": [],
                                   "total_ua": 0, "in_progress": 0, "errors": []})

            completed = []
            anomalous = []
            no_ticket = []
            errors = []
            in_progress = 0

            for node_rec in nodes:
                hostname = node_rec.HostName
                node_id = node_rec.NodeId
                try:
                    ticket_id, onboard_id = get_ticket_id_for_node(pc, hostname)
                except Exception as e:
                    no_ticket.append({"hostname": hostname, "node_id": node_id,
                                      "reason": f"no ticket record: {e}"})
                    continue

                if not ticket_id:
                    no_ticket.append({"hostname": hostname, "node_id": node_id,
                                      "reason": "ticket_id is null"})
                    continue

                try:
                    ticket_data = _get_ticket_status(ticket_id, config)
                    status_info = ticket_data.get("data", {})
                    repair_status = status_info.get("repairStatus", "unknown")
                except Exception as e:
                    errors.append({"hostname": hostname, "ticket_id": ticket_id,
                                   "error": str(e)})
                    continue

                repair_lower = repair_status.lower() if repair_status else "unknown"
                logger.info(f"check_completed_tickets: {hostname} ticket={ticket_id} "
                            f"repair_status=|{repair_status}| lower=|{repair_lower}|")

                if repair_lower in ("completed", "repaired", "done",
                                    "已完成", "已修复", "已撤销", "已撤回", "已拒绝"):
                    completed.append({
                        "hostname": hostname,
                        "node_id": node_id,
                        "ticket_id": ticket_id,
                        "repair_status": repair_status,
                    })
                elif repair_lower in ("rejected", "cancelled", "canceled", "on-hold", "onhold"):
                    anomalous.append({
                        "hostname": hostname,
                        "node_id": node_id,
                        "ticket_id": ticket_id,
                        "repair_status": repair_status,
                    })
                else:
                    in_progress += 1

            return json.dumps({
                "completed": completed,
                "anomalous": anomalous,
                "no_ticket": no_ticket,
                "total_ua": len(nodes),
                "in_progress": in_progress,
                "errors": errors,
            }, default=str, indent=2)

    # ---- reallocate_node ----
    if _register("reallocate_node"):
        @mcp.tool(timeout=14400)
        def reallocate_node(hostname: str, ctx: Context) -> str:
            """Reallocate a single 'ready_ua' node: reset, full config, sysinfo, allocate.

            Orchestrates multiple sub-steps. If any step fails, you can retry just
            that step using the standalone tools:
              - bmc_power_cycle: BMC check → power off → power on → cold reset
              - wait_for_boot: Poll SSH after power cycle
              - reset_node: Run clear_node.sh
              - run_full_config: 7 init stages + k8s scale
              - collect_sysinfo: sbsysinfo, SKU, serial
              - clone_and_allocate: Clone onboard + move to allocated_ua

            Steps:
            1. Get node detail (IP, category from onboard record)
            2. Probe SSH — if unreachable, bmc_power_cycle + wait_for_boot + reset_node
            3. Run full config (7 stages + k8s) — takes ~20-30 minutes
            4. Collect sysinfo (sbsysinfo, SKU, serial)
            5. Clone onboard record + move to allocated_ua
            6. On failure: move to triaged_unknown with error detail

            This is a LONG-RUNNING operation (~20-30 min per node).
            Call once per node. Use a working file to track progress across nodes.
            Progress is written to /app/workspace/reallocate_<hostname>.log after each step.
            """
            import time as _time
            import tempfile

            progress_log = []  # accumulate step results for LLM return value

            def _step(step_name: str, status: str, detail: str = ""):
                """Log a step for both container logs and final return value."""
                entry = f"{step_name}: {status}" + (f" — {detail}" if detail else "")
                progress_log.append(entry)
                logger.info("reallocate %s: %s", hostname, entry)

            mock = _mock_lookup("reallocate_node", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "reallocate_node", mock,
                                    category="action", summary="reallocate (mock)")
                return mock
            from node_operations.db import create_clients, get_node_detail, insert_allocated_ua, insert_triaged_unknown, clone_onboard_record
            from node_operations.ssh import probe_ssh
            from node_operations.reset import run_reset
            from node_operations.config import run_full_config as _run_full_config, _ipmitool
            from node_operations.sysinfo import collect_sbsysinfo, read_sku, read_sys_info
            from node_operations.bmc import get_sn_from_ipmi

            _step("start", "in_progress", f"hostname={hostname}")

            sc, ac, pc = create_clients()
            _bmc_password = os.environ.get("BMC_PASSWORD", "")
            _reset_bmc_password = os.environ.get("RESET_BMC_PASSWORD", "")
            _timeout = int(os.environ.get("SSH_TIMEOUT", "300"))

            try:
                # Step 1: Get node detail
                record = get_node_detail(pc, hostname)
                if not record:
                    _step("get_node_detail", "failed", "no onboard record found")
                    return _domain_result(
                        "not_found",
                        f"no onboard record found for {hostname}",
                        hostname=hostname,
                        entity="onboard_record",
                    )
                ip_list = record.get("ip", [])
                if not ip_list:
                    _step("get_node_detail", "failed", "no IP address found")
                    return _domain_result(
                        "not_found",
                        f"no IP address found for {hostname}",
                        hostname=hostname,
                        entity="ip_address",
                    )
                ip = ip_list[0] if isinstance(ip_list, list) else ip_list
                category = record.get("category", "h200")
                node_id = str(record.get("id", ""))
                bmc_ip_raw = record.get("bmc_ip") or record.get("mgmt_ip", "")
                bmc_ip = bmc_ip_raw[0] if isinstance(bmc_ip_raw, list) else bmc_ip_raw
                _step("get_node_detail", "ok", f"ip={ip}, category={category}, bmc_ip={bmc_ip}, node_id={node_id}")

                # Step 2: Probe SSH reachability (operator first, ubuntu fallback)
                _step("probe_ssh", "in_progress", f"testing {ssh_user}@{ip} then {reset_ssh_user}@{ip}")
                reachable_user = None
                if probe_ssh(ip, ssh_user, timeout=_timeout, password=ssh_password):
                    reachable_user = ssh_user
                elif probe_ssh(ip, reset_ssh_user, timeout=_timeout, password=reset_ssh_password):
                    reachable_user = reset_ssh_user

                if not reachable_user:
                    # Both unreachable — try BMC power cycle
                    if not (bmc_ip and bmc_user and _bmc_password):
                        _step("probe_ssh", "failed", "unreachable and no BMC IP for power cycle")
                        raise RuntimeError(f"Node {hostname} unreachable (SSH to {ip} failed) and no BMC IP available for power cycle")

                    # Step 2a-2c: BMC power cycle (uses shared function in bmc.py)
                    _step("bmc_power_cycle", "in_progress", f"BMC={bmc_ip}")
                    from node_operations.bmc import bmc_power_cycle as _bmc_power_cycle_fn
                    _pc_result = _bmc_power_cycle_fn(
                        hostname=hostname, bmc_ip=bmc_ip, bmc_user=bmc_user,
                        bmc_password=_bmc_password, reset_bmc_password=_reset_bmc_password,
                    )
                    _active_bmc_password = _pc_result["active_bmc_password"]
                    for _pc_step in _pc_result["steps"]:
                        logger.info("reallocate %s: %s", hostname, _pc_step)
                    if not _pc_result["success"]:
                        _step("bmc_power_cycle", "failed", _pc_result["error"])
                        raise RuntimeError(f"Node {hostname} BMC power cycle failed: {_pc_result['error']}")
                    _step("bmc_power_cycle", "ok", "power cycle confirmed, waiting for boot")

                    # Step 2d: Wait for node to boot (uses shared function in bmc.py)
                    _step("wait_for_boot", "in_progress", "polling SSH reachability")
                    from node_operations.bmc import wait_for_boot as _wait_for_boot_fn
                    _boot_result = _wait_for_boot_fn(
                        hostname=hostname, ip=ip,
                        ssh_user=ssh_user, ssh_password=ssh_password,
                        reset_ssh_user=reset_ssh_user, reset_ssh_password=reset_ssh_password,
                        bmc_ip=bmc_ip, bmc_user=bmc_user, bmc_password=_active_bmc_password,
                        category=category,
                    )
                    for _boot_step in _boot_result["steps"]:
                        logger.info("reallocate %s: %s", hostname, _boot_step)
                    if not _boot_result["success"]:
                        _step("wait_for_boot", "failed", _boot_result["error"])
                        if _boot_result.get("screenshot_path"):
                            _step("bmc_screenshot", "ok", f"saved to {_boot_result['screenshot_path']}")
                        raise RuntimeError(_boot_result["error"])
                    reachable_user = _boot_result["reachable_user"]
                    _step("wait_for_boot", "ok", f"up as {reachable_user} after {_boot_result['boot_time_seconds']}s")

                    # After BMC power cycle, run reset to clean node state
                    _step("reset_node", "in_progress", "running clear_node.sh via SSH")
                    _auto_save_evidence(hostname, "reallocate_node",
                                        json.dumps({"step": "post_power_cycle", "action": "run_reset"}),
                                        category="action", summary="Node booted after power cycle, running reset")
                    run_reset(hostname=hostname, ip=ip,
                              ssh_user=ssh_user, ssh_password=ssh_password,
                              reset_ssh_user=reset_ssh_user, reset_ssh_password=reset_ssh_password,
                              bmc_password=_bmc_password, reset_bmc_password=_reset_bmc_password,
                              timeout=_timeout)
                    _step("reset_node", "ok", "clear_node.sh completed")

                    # After reset, only ubuntu is available
                    reachable_user = reset_ssh_user
                    _step("probe_ssh_after_reset", "ok", f"reachable as {reset_ssh_user}")

                _auto_save_evidence(hostname, "reallocate_node",
                                    json.dumps({"step": "probe_ssh", "reachable": True, "user": reachable_user}),
                                    category="action", summary=f"Node reachable via {reachable_user}")

                # Determine available_user for config pipeline
                _avail_user = reachable_user
                _avail_pw = reset_ssh_password if reachable_user == reset_ssh_user else ssh_password

                # Step 3: Full config pipeline (~20 min)
                _step("full_config", "in_progress", "7 stages + k8s scale (~20 min)")
                config_log = _run_full_config(
                    hostname=hostname, ip=ip, category=category,
                    ssh_user=ssh_user, ssh_password=ssh_password,
                    reset_ssh_user=reset_ssh_user, reset_ssh_password=reset_ssh_password,
                    bmc_password=_bmc_password, timeout=_timeout,
                    k8s_master_user=os.environ.get("K8S_MASTER_USER", ""),
                    k8s_master_ip=os.environ.get("K8S_MASTER_IP", ""),
                    bmc_ip=bmc_ip, bmc_user=os.environ.get("BMC_USER", "admin"),
                    bmc_pass=_bmc_password,
                    available_user=_avail_user, available_password=_avail_pw,
                )
                if config_log:
                    progress_log.extend(config_log)
                _step("full_config", "ok", "all stages + k8s scale completed")

                # Step 4: Collect sysinfo
                _step("sysinfo", "in_progress", "collecting sbsysinfo, SKU, serial")
                base_dir = tempfile.mkdtemp(prefix="sbsysinfo_")
                collect_sbsysinfo(hostname=hostname, ip=ip, category=category,
                                  ssh_user=ssh_user, ssh_password=ssh_password,
                                  bmc_password=_bmc_password, timeout=_timeout,
                                  base_dir=base_dir)
                sku = read_sku(ip, base_dir=base_dir)
                sys_info = read_sys_info(ip, base_dir=base_dir)
                sn = ""
                try:
                    sn = get_sn_from_ipmi(ip, user=ssh_user)
                except Exception:
                    pass
                _step("sysinfo", "ok", f"sn={sn}, sku={sku}")

                # Step 5: Clone onboard + allocate
                _step("allocate", "in_progress", "cloning onboard record + moving to allocated_ua")
                new_id = clone_onboard_record(pc, hostname, sn, sku, sys_info)
                node_id = str(new_id)
                insert_allocated_ua(
                    status_client=sc, action_client=ac, physical_node_client=pc,
                    hostname=hostname, new_onboard_id=new_id,
                )
                _step("allocate", "ok", f"onboard_id={new_id}")
                _auto_save_evidence(hostname, "reallocate_node",
                                    json.dumps({"step": "allocated", "onboard_id": new_id, "sn": sn, "sku": sku}),
                                    category="action", summary=f"Reallocated: new_id={new_id}")

                _step("done", "ok", f"Reallocated {hostname}: onboard_id={new_id}, sn={sn}, sku={sku}")
                return "\n".join(progress_log)

            except Exception as e:
                _step("failed", "error", str(e)[:300])
                _auto_save_evidence(hostname, "reallocate_node",
                                    json.dumps({"step": "failed", "error": str(e)}),
                                    category="action", summary=f"Reallocation FAILED: {str(e)[:200]}")
                # Move to triaged_unknown for re-diagnosis
                try:
                    insert_triaged_unknown(
                        status_client=sc, action_client=ac, physical_node_client=pc,
                        hostname=hostname, node_id=node_id,
                        reason=f"reallocation_failed: {str(e)[:200]}",
                        detail=f"reallocate_node failure: {str(e)}",
                    )
                except Exception:
                    pass
                return "\n".join(progress_log) + f"\n\nREALLOCATION FAILED: {e}"

    # ---- bmc_power_cycle ----
    if _register("bmc_power_cycle"):
        @mcp.tool(timeout=1860)
        def bmc_power_cycle(hostname: str, bmc_ip: str,
                            bmc_password: str = "",
                            reset_bmc_password: str = "",
                            poll_interval: int = 15,
                            poll_timeout: int = 900,
                            cold_reset_retry: bool = True) -> str:
            """BMC power cycle: verify BMC → power off → always-on policy → power on → poll.
            If power stays off after poll_timeout, optionally does BMC cold reset to clear
            latched fault state (e.g. VRM sensor spikes on Supermicro), then retries.

            Use this standalone tool when:
            - A node has a latched BMC fault and just needs power recovery
            - reallocate_node failed at the BMC step and you want to retry just that step
            - You need to force-power-cycle a node without running the full reallocation

            This is a LONG-RUNNING operation (up to 30 min with cold reset retry).
            Does NOT run clear_node.sh or any SSH operations — BMC-level only.

            Args:
                hostname: Node hostname.
                bmc_ip: BMC management IP address.
                bmc_password: Primary BMC password (or set BMC_PASSWORD env var).
                reset_bmc_password: Vendor/RMA BMC password fallback (or set RESET_BMC_PASSWORD env var).
                poll_interval: Seconds between power-status polls (default 15s).
                poll_timeout: Seconds to wait per power-on attempt (default 900s = 15min).
                cold_reset_retry: If True, do mc reset cold + retry when power stays off.
            """
            mock = _mock_lookup("bmc_power_cycle", hostname)
            if mock is not None:
                return mock
            from node_operations.bmc import bmc_power_cycle as _bmc_power_cycle
            _bmc_pw = bmc_password or os.environ.get("BMC_PASSWORD", "")
            _reset_bmc_pw = reset_bmc_password or os.environ.get("RESET_BMC_PASSWORD", "")
            _bmc_user = os.environ.get("BMC_USER", "admin")
            result = _bmc_power_cycle(
                hostname=hostname, bmc_ip=bmc_ip, bmc_user=_bmc_user,
                bmc_password=_bmc_pw, reset_bmc_password=_reset_bmc_pw,
                poll_interval=poll_interval, poll_timeout=poll_timeout,
                cold_reset_retry=cold_reset_retry,
            )
            _auto_save_evidence(hostname, "bmc_power_cycle",
                                json.dumps(result, default=str),
                                category="action",
                                summary=f"BMC power cycle: {'OK' if result['success'] else 'FAILED'}")
            return json.dumps(result, indent=2)

    # ---- wait_for_boot ----
    if _register("wait_for_boot"):
        @mcp.tool(timeout=1860)
        def wait_for_boot(hostname: str, ip: str,
                          ssh_user: str = "",
                          ssh_password: str = "",
                          reset_ssh_user: str = "ubuntu",
                          reset_ssh_password: str = "",
                          poll_interval: int = 30,
                          poll_timeout: int = 1800,
                          bmc_ip: str = "",
                          bmc_password: str = "",
                          category: str = "h200") -> str:
            """Wait for a node to boot by polling SSH reachability.

            After a BMC power cycle or reboot, poll SSH until the node is reachable.
            On failure, captures a BMC KVM screenshot for diagnosis.

            Use this standalone tool when:
            - bmc_power_cycle succeeded but you need to wait for the node to fully boot
            - reallocate_node failed at the boot wait step and you want to retry
            - You rebooted a node manually and need to confirm it came back

            This is a LONG-RUNNING operation (up to 30 min default).

            Args:
                hostname: Node hostname.
                ip: Node IP address.
                ssh_user: Primary SSH user (defaults to env SSH_USER).
                ssh_password: Primary SSH password (defaults to env SSH_PASSWORD).
                reset_ssh_user: Fallback SSH user after reset (default ubuntu).
                reset_ssh_password: Fallback SSH password (defaults to env RESET_SSH_PASSWORD).
                poll_interval: Seconds between SSH probes (default 30s).
                poll_timeout: Max seconds to wait (default 1800s = 30min).
                bmc_ip: Optional BMC IP for screenshot on failure.
                bmc_password: BMC password for screenshot (defaults to env BMC_PASSWORD).
                category: Node category for screenshot path (default h200).
            """
            mock = _mock_lookup("wait_for_boot", hostname)
            if mock is not None:
                return mock
            from node_operations.bmc import wait_for_boot as _wait_for_boot
            _ssh_user = ssh_user or os.environ.get("SSH_USER", "operator")
            _ssh_password = ssh_password or os.environ.get("SSH_PASSWORD", "")
            _reset_ssh_password = reset_ssh_password or os.environ.get("RESET_SSH_PASSWORD", "")
            _bmc_pw = bmc_password or os.environ.get("BMC_PASSWORD", "")
            _bmc_user = os.environ.get("BMC_USER", "admin")
            result = _wait_for_boot(
                hostname=hostname, ip=ip,
                ssh_user=_ssh_user, ssh_password=_ssh_password,
                reset_ssh_user=reset_ssh_user, reset_ssh_password=_reset_ssh_password,
                poll_interval=poll_interval, poll_timeout=poll_timeout,
                bmc_ip=bmc_ip, bmc_user=_bmc_user, bmc_password=_bmc_pw,
                category=category,
            )
            _auto_save_evidence(hostname, "wait_for_boot",
                                json.dumps(result, default=str),
                                category="action",
                                summary=f"Boot wait: {'OK' if result['success'] else 'FAILED'} user={result.get('reachable_user')}")
            return json.dumps(result, indent=2)

    # ---- run_ssh_command ----
    if _register("run_ssh_command"):
        @mcp.tool()
        def run_ssh_command(hostname: str, ip: str, command: str,
                            timeout: int = 60, sudo: bool = False) -> str:
            """Execute a command on a remote node via SSH. Returns exit code, stdout, and stderr.
            Use for platform repair actions: killing ghost containers (crictl stop),
            restarting services (systemctl), checking process state (ps), etc.
            DANGEROUS — only use for confirmed repair actions. Always confirm with user
            before destructive commands (crictl stop, systemctl restart, rm)."""
            mock = _mock_lookup("run_ssh_command", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "run_ssh_command",
                                    json.dumps({"command": command, "mock": True}),
                                    category="platform_repair",
                                    summary=f"SSH command (mock): {command[:120]}")
                return mock
            from node_operations.ssh import run_ssh_command as _run
            result = _run(ip=ip, user=ssh_user,
                          command=command, timeout=timeout, sudo=sudo)
            _auto_save_evidence(hostname, "run_ssh_command",
                                json.dumps({"command": command, "sudo": sudo,
                                            "exit_code": result["exit_code"],
                                            "stdout": result["stdout"][:2000],
                                            "error": result.get("error")}),
                                category="platform_repair",
                                summary=f"SSH: {command[:120]} (exit={result['exit_code']})",
                                metadata={"exit_code": result["exit_code"]})
            return json.dumps(result, indent=2)

    # ---- run_kubectl (read-only) ----
    if _register("run_kubectl"):
        @mcp.tool()
        def run_kubectl(command: str, hostname: str = "",
                        timeout: int = 60) -> str:
            """Execute a READ-ONLY kubectl command on the LTP management host via SSH.
            The agent container does not have kubectl; it SSH's to the LTP host
            (LTP_HOST_ADDR) where kubectl is configured.
            Pass the subcommand only — "kubectl" is prepended automatically.
            Example: command="get pods -n kube-system", NOT "kubectl get pods -n kube-system".

            ALLOWED: get, describe, logs, top, exec, explain, api-resources, diff, delete pod.
            BLOCKED: patch, apply, taint, rollout, uncordon — use run_kubectl_dangerous instead.
            """
            cmd_lower = command.strip().lower()

            # Dangerous commands must use run_kubectl_dangerous
            dangerous_patterns = ["patch", "taint", "apply ", "rollout", "uncordon"]
            is_dangerous = any(p in cmd_lower for p in dangerous_patterns)
            if is_dangerous:
                return _domain_result(
                    "command_blocked",
                    "This command modifies cluster state and requires user approval. "
                    "Use run_kubectl_dangerous instead.",
                    command=command,
                )

            mock = _mock_lookup("run_kubectl", command[:50])
            if mock is not None:
                if hostname:
                    _auto_save_evidence(hostname, "run_kubectl",
                                        json.dumps({"command": command, "mock": True}),
                                        category="platform_repair",
                                        summary=f"kubectl (mock): kubectl {command[:80]}")
                return mock
            from node_operations.ssh import run_kubectl_via_ssh
            ltp_host = os.environ.get("LTP_HOST_ADDR", "")
            result = run_kubectl_via_ssh(
                host_ip=ltp_host, user=ssh_user,
                command=command, timeout=timeout,
            )
            if hostname:
                _auto_save_evidence(hostname, "run_kubectl",
                                    json.dumps({"command": command,
                                                "exit_code": result["exit_code"],
                                                "stdout": result["stdout"][:2000],
                                                "error": result.get("error")}),
                                    category="platform_repair",
                                    summary=f"kubectl {command[:80]} (exit={result['exit_code']})",
                                    metadata={"exit_code": result["exit_code"]})
            return json.dumps(result, indent=2)

    # ---- run_kubectl_dangerous (requires SDK user approval) ----
    if _register("run_kubectl_dangerous"):
        @mcp.tool()
        def run_kubectl_dangerous(command: str, hostname: str = "",
                                  timeout: int = 60) -> str:
            """Execute a CLUSTER-MODIFYING kubectl command that requires user approval.
            The SDK permission system will prompt the user for approval before this tool executes.

            Use for: kubectl patch, kubectl apply, kubectl taint, kubectl rollout, kubectl uncordon,
            and any other cluster-wide modifications.
            Pass the subcommand only — "kubectl" is prepended automatically.
            """
            cmd_lower = command.strip().lower()

            mock = _mock_lookup("run_kubectl_dangerous", command[:50])
            if mock is not None:
                if hostname:
                    _auto_save_evidence(hostname, "run_kubectl_dangerous",
                                        json.dumps({"command": command, "mock": True}),
                                        category="platform_repair",
                                        summary=f"kubectl dangerous (mock): kubectl {command[:80]}")
                return mock
            from node_operations.ssh import run_kubectl_via_ssh
            ltp_host = os.environ.get("LTP_HOST_ADDR", "")
            result = run_kubectl_via_ssh(
                host_ip=ltp_host, user=ssh_user,
                command=command, timeout=timeout,
            )
            if hostname:
                _auto_save_evidence(hostname, "run_kubectl_dangerous",
                                    json.dumps({"command": command,
                                                "exit_code": result["exit_code"],
                                                "stdout": result["stdout"][:2000],
                                                "error": result.get("error")}),
                                    category="platform_repair",
                                    summary=f"kubectl dangerous: kubectl {command[:80]} (exit={result['exit_code']})",
                                    metadata={"exit_code": result["exit_code"]})
            return json.dumps(result, indent=2)

    # ---- check_kubelet ----
    if _register("check_kubelet"):
        @mcp.tool()
        def check_kubelet(hostname: str, ip: str = "",
                          include_certs: bool = False,
                          include_logs: bool = False,
                          log_lines: int = 20) -> str:
            """Check kubelet service health on a node.

            Combines systemd status, PKI certificate existence, and recent journal
            entries into one diagnostic call. Use for kubelet crash, cert expiry,
            and node-not-ready investigations.

            Args:
                hostname: Target node hostname.
                ip: Node IP address. If empty, resolved from hostname via node DB.
                include_certs: Check /etc/kubernetes/bootstrap-kubelet.conf and
                    /var/lib/kubelet/pki/kubelet-client-current.pem existence.
                include_logs: Include last N journal entries from kubelet.service.
                log_lines: Number of journal entries when include_logs is true.
            """
            mock = _mock_lookup("check_kubelet", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "check_kubelet", mock,
                                    category="platform", summary="kubelet check (mock)")
                return mock

            node_ip = ip or _resolve_ip_or_empty(hostname)
            if not node_ip:
                return f"Cannot resolve IP for {hostname}"

            from node_operations.ssh import run_remote_command_capture
            parts = []

            status_cmd = f"systemctl status kubelet --no-pager -l 2>&1 | head -30"
            parts.append(run_remote_command_capture(node_ip, ssh_user, status_cmd, ssh_timeout))

            if include_certs:
                cert_cmd = ("echo '---CERTS---'; "
                            "ls -la /etc/kubernetes/bootstrap-kubelet.conf 2>&1; "
                            "ls -la /var/lib/kubelet/pki/kubelet-client-current.pem 2>&1")
                parts.append(run_remote_command_capture(node_ip, ssh_user, cert_cmd, ssh_timeout))

            if include_logs:
                log_cmd = f"journalctl -u kubelet --no-pager -n {log_lines} 2>&1 | tail -{log_lines}"
                parts.append(run_remote_command_capture(node_ip, ssh_user, log_cmd, ssh_timeout))

            result = "\n".join(parts)
            _auto_save_evidence(hostname, "check_kubelet", result,
                                category="platform",
                                summary=f"kubelet check (certs={include_certs}, logs={include_logs})")
            return result

    # ---- check_ib_ports ----
    if _register("check_ib_ports"):
        @mcp.tool()
        def check_ib_ports(hostname: str, ip: str = "",
                           port: str = "",
                           include_counters: bool = False) -> str:
            """Check InfiniBand HCA port states and error counters.

            Sweeps all mlx5 ports for link state, or inspects a specific port
            with optional perfquery error/discard/drop counters. Use for IB link
            flapping, port down, and fabric connectivity investigations.

            Args:
                hostname: Target node hostname.
                ip: Node IP address. If empty, resolved from hostname via node DB.
                port: Specific HCA port name (e.g. "mlx5_2"). Empty = sweep all ports.
                include_counters: Include perfquery error counters for the specified port.
            """
            mock = _mock_lookup("check_ib_ports", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "check_ib_ports", mock,
                                    category="ib", summary="IB port check (mock)")
                return mock

            node_ip = ip or _resolve_ip_or_empty(hostname)
            if not node_ip:
                return f"Cannot resolve IP for {hostname}"

            from node_operations.ssh import run_remote_command_capture
            if port:
                cmd = f"ibstat {port} 2>/dev/null"
                if include_counters:
                    cmd += (f"; echo '---COUNTERS---'; "
                            f"sudo perfquery -x {port} 1 2>/dev/null | "
                            "grep -i 'error\\|discard\\|drop' || true")
            else:
                cmd = ("for p in $(ibstat -l 2>/dev/null); do "
                       "echo -n \"$p: \"; "
                       "ibstat $p | grep -E 'State:|Physical state:' | "
                       "head -2 | tr '\\n' ' '; echo; done")

            result = run_remote_command_capture(node_ip, ssh_user, cmd, ssh_timeout)
            _auto_save_evidence(hostname, "check_ib_ports", result,
                                category="ib",
                                summary=f"IB ports (port={port or 'all'}, counters={include_counters})")
            return result

    # ---- check_ip_routing ----
    if _register("check_ip_routing"):
        @mcp.tool()
        def check_ip_routing(hostname: str, ip: str = "",
                             table: int = 100) -> str:
            """Check IP policy routing rules, route table definitions, and routing table entries.

            Use for IPoIB routing misconfiguration, missing policy routes, and
            asymmetric traffic investigations.

            Args:
                hostname: Target node hostname.
                ip: Node IP address. If empty, resolved from hostname via node DB.
                table: Routing table number to inspect (default 100 for IB table).
            """
            mock = _mock_lookup("check_ip_routing", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "check_ip_routing", mock,
                                    category="network", summary="IP routing check (mock)")
                return mock

            node_ip = ip or _resolve_ip_or_empty(hostname)
            if not node_ip:
                return f"Cannot resolve IP for {hostname}"

            from node_operations.ssh import run_remote_command_capture
            cmd = (f"ip rule list | head -20; "
                   f"echo '==='; "
                   f"cat /etc/iproute2/rt_tables; "
                   f"echo '==='; "
                   f"ip route show table {table} 2>/dev/null; "
                   f"echo '===DONE==='")

            result = run_remote_command_capture(node_ip, ssh_user, cmd, ssh_timeout)
            _auto_save_evidence(hostname, "check_ip_routing", result,
                                category="network", summary=f"IP routing table {table}")
            return result

    # ---- check_gpu_clocks ----
    if _register("check_gpu_clocks"):
        @mcp.tool()
        def check_gpu_clocks(hostname: str, ip: str = "") -> str:
            """Check GPU clock frequencies, throttle reasons, and power consumption.

            Use for GPU performance degradation, clock throttling, and
            ModelPerformanceDegradation investigations.

            Args:
                hostname: Target node hostname.
                ip: Node IP address. If empty, resolved from hostname via node DB.
            """
            mock = _mock_lookup("check_gpu_clocks", hostname)
            if mock is not None:
                _auto_save_evidence(hostname, "check_gpu_clocks", mock,
                                    category="gpu", summary="GPU clocks check (mock)")
                return mock

            node_ip = ip or _resolve_ip_or_empty(hostname)
            if not node_ip:
                return f"Cannot resolve IP for {hostname}"

            from node_operations.ssh import run_remote_command_capture
            cmd = "nvidia-smi -q -d CLOCK,POWER | head -120"

            result = run_remote_command_capture(node_ip, ssh_user, cmd, ssh_timeout)
            _auto_save_evidence(hostname, "check_gpu_clocks", result,
                                category="gpu", summary="GPU clocks and power")
            return result

    return mcp


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", default="readonly", choices=list(ROLE_TOOLS.keys()))
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable-http", "http"])
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = args.port
    server = create_server(args.role)

    transport = args.transport
    if transport == "streamable-http":
        transport = "http"

    logger.info(
        f"Starting node-operations MCP server "
        f"(transport={transport}, role={args.role}, "
        f"host={host}, port={port})"
    )
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport=transport, host=host, port=port)
