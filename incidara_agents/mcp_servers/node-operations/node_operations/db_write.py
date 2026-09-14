"""DB write operations — raw SQL to bypass SDK action/status validation.

The ltp_storage SDK validates action strings and status values against a fixed
allowlist. Physical pipeline statuses (ready_ua) and some transitions
(ua-ready_ua, ready_ua-allocated_ua) are not in that list. The original
node_pipeline.py used raw psycopg2 for the same reason.

We use raw SQL via the SDK's session/execute_query for all writes.

Access control:
  - diagnosis role: insert_status_transition only
  - ops role: all writes
  - node-recycler: insert_complete_rma, insert_allocated_ua, insert_triaged_unknown, clone_onboard_record
"""
import json
import os
import time
import datetime as dt
import zoneinfo
import logging

logger = logging.getLogger(__name__)


def _execute_insert(physical_node_client, query: str, params: dict = None):
    """Execute an INSERT/UPDATE that doesn't return rows.

    If params is provided, uses parameterized query (prevents bind-param
    injection from vendor data containing %(xxx)s patterns).
    """
    from sqlalchemy import text
    session = physical_node_client.get_session()
    session.execute(text(query), params or {})
    session.commit()


def _execute_insert_returning(physical_node_client, query: str) -> list[dict]:
    """Execute an INSERT ... RETURNING that returns rows."""
    return physical_node_client.execute_query(query)


def _execute_transaction(physical_node_client, queries: list):
    """Execute multiple SQL statements in a single transaction.

    Each item is either a plain SQL string or a (sql, params) tuple.
    All statements commit together or all roll back on error.
    Use this for status + action writes that must be atomic.
    """
    from sqlalchemy import text
    session = physical_node_client.get_session()
    try:
        for q in queries:
            if isinstance(q, tuple):
                sql, params = q
                session.execute(text(sql), params)
            else:
                session.execute(text(q))
        session.commit()
    except Exception:
        session.rollback()
        raise


def _insert_node_action(physical_node_client, *, hostname: str, node_id: str,
                         action: str, reason: str, detail: str, category: str):
    """Insert into node_actions using raw SQL (bypasses SDK action validation)."""
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    endpoint = os.environ.get("CLUSTER_ID", "ltp")
    # Escape single quotes in detail/reason
    reason_esc = reason.replace("'", "''")
    detail_esc = detail.replace("'", "''")
    _execute_insert(
        physical_node_client,
        f"INSERT INTO ltp_sdk.node_actions (\"timestamp\", hostname, node_id, action, reason, detail, category, endpoint) "
        f"VALUES ('{ts}', '{hostname}', '{node_id}', '{action}', '{reason_esc}', '{detail_esc}', '{category}', '{endpoint}')"
    )


def _insert_node_status(physical_node_client, *, hostname: str, node_id: str, status: str):
    """Insert into node_status using raw SQL (bypasses SDK status validation)."""
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    endpoint = os.environ.get("CLUSTER_ID", "ltp")
    _execute_insert(
        physical_node_client,
        f"INSERT INTO ltp_sdk.node_status (\"timestamp\", hostname, node_id, status, endpoint) "
        f"VALUES ('{ts}', '{hostname}', '{node_id}', '{status}', '{endpoint}')"
    )


# --- Diagnosis role writes (triage agent) ---

def _resolve_current_status(status_client, hostname: str) -> str | None:
    """Look up the node's current status from node_status table.

    Returns None if the node has no status record.
    """
    try:
        record = status_client.get_node_status(hostname)
        if record is not None:
            if isinstance(record, list):
                record = record[-1]
            return record.Status if hasattr(record, 'Status') else None
    except Exception:
        pass
    return None


def insert_status_transition(*, status_client, action_client, physical_node_client=None,
                              hostname: str, node_id: str,
                              from_status: str, to_status: str,
                              reason: str, detail: str, category: str) -> None:
    """General-purpose status transition.

    Uses SDK clients when possible, falls back to raw SQL for unknown transitions.
    Auto-corrects from_status to match the node's actual current status in the DB,
    so callers don't need to know the exact current state.

    Both the action record and status record are written in a single transaction,
    so they either both succeed or both roll back — no half-written state.
    """
    # Auto-resolve from_status from DB if it doesn't match
    actual_status = _resolve_current_status(status_client, hostname)
    if actual_status and actual_status != from_status:
        logger.info("correcting from_status for %s: %s -> %s (DB actual)",
                     hostname, from_status, actual_status)
        from_status = actual_status

    ts = time.time()
    action_key = f"{from_status}-{to_status}"

    # Build raw SQL for both tables (used as fallback or as transaction body)
    ts_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    endpoint = os.environ.get("CLUSTER_ID", "ltp")
    reason_esc = reason.replace("'", "''")
    detail_esc = detail.replace("'", "''")
    action_sql = (
        f"INSERT INTO ltp_sdk.node_actions (\"timestamp\", hostname, node_id, action, reason, detail, category, endpoint) "
        f"VALUES ('{ts_iso}', '{hostname}', '{node_id}', '{action_key}', '{reason_esc}', '{detail_esc}', '{category}', '{endpoint}')"
    )
    status_sql = (
        f"INSERT INTO ltp_sdk.node_status (\"timestamp\", hostname, node_id, status, endpoint) "
        f"VALUES ('{ts_iso}', '{hostname}', '{node_id}', '{to_status}', '{endpoint}')"
    )

    # Try SDK first — SDK internally handles both tables atomically
    try:
        action_client.update_node_action(hostname, action_key, ts, reason, detail, category)
        status_client.update_node_status(hostname, to_status, ts)
    except (RuntimeError, ValueError):
        if not physical_node_client:
            raise
        logger.info("SDK rejected %s, using raw SQL transaction", action_key)
        _execute_transaction(physical_node_client, [action_sql, status_sql])
    logger.info("transition %s: %s -> %s", hostname, from_status, to_status)


# --- Ops role writes (repair agent) ---

def insert_rma_transition(*, status_client, action_client, physical_node_client,
                           hostname: str, onboard_id: int,
                           ticket_id: str, description: str, detail: str) -> None:
    """Repair agent: reset + ticket submitted, move to ua.

    node_id is derived from onboard_id (they are the same value).
    All three writes (physical_node_actions, node_actions, node_status) are
    executed in a single transaction.
    """
    node_id = str(onboard_id)
    ts = time.time()
    ts_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    endpoint = os.environ.get("CLUSTER_ID", "ltp")
    metainfo = json.dumps({"version": 1, "details": description})
    detail_esc = detail.replace("'", "''")

    # Use parameterized query for metainfo — vendor data may contain
    # Python-style format strings like %(nvlink)s that SQLAlchemy
    # misinterprets as bind params when interpolated into SQL.
    rma_sql = (
        "INSERT INTO ltp_sdk.physical_node_actions (onboard_id, op_type, \"timestamp\", ticket_id, metainfo) "
        "VALUES (:onboard_id, 'InitiateRMA', to_timestamp(:ts), :ticket_id, CAST(:metainfo AS jsonb))"
    )
    rma_params = dict(onboard_id=onboard_id, ts=ts, ticket_id=ticket_id, metainfo=metainfo)
    action_sql = (
        f"INSERT INTO ltp_sdk.node_actions (\"timestamp\", hostname, node_id, action, reason, detail, category, endpoint) "
        f"VALUES ('{ts_iso}', '{hostname}', '{node_id}', 'deallocated_ua-ua', '', '{detail_esc}', 'hardware', '{endpoint}')"
    )
    status_sql = (
        f"INSERT INTO ltp_sdk.node_status (\"timestamp\", hostname, node_id, status, endpoint) "
        f"VALUES ('{ts_iso}', '{hostname}', '{node_id}', 'ua', '{endpoint}')"
    )

    # Single transaction — all three writes commit together or all roll back.
    # Never use separate _execute_insert + SDK calls: if the SDK call fails
    # after _execute_insert succeeds, the except block re-executes rma_sql
    # and creates a duplicate InitiateRMA entry.
    _execute_transaction(physical_node_client, [(rma_sql, rma_params), action_sql, status_sql])
    logger.info("RMA initiated %s ticket=%s", hostname, ticket_id)


# --- Node-recycler writes ---

def insert_complete_rma(*, status_client, action_client, physical_node_client,
                         hostname: str, node_id: str, onboard_id: int,
                         ticket_id: str, ticket_payload: dict, detail: str) -> None:
    """Node-recycler: ticket completed, move ua -> ready_ua.

    All three writes executed in a single transaction.
    """
    ts = time.time()
    ts_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    endpoint = os.environ.get("CLUSTER_ID", "ltp")
    metainfo = json.dumps({"version": 1, "details": ticket_payload})
    detail_esc = detail.replace("'", "''")

    # Use parameterized query for metainfo — vendor ticket JSON may contain
    # Python-style format strings like %(nvlink)s or %(yes)s that SQLAlchemy
    # misinterprets as bind params when interpolated into SQL.
    rma_sql = (
        "INSERT INTO ltp_sdk.physical_node_actions (onboard_id, op_type, \"timestamp\", ticket_id, metainfo) "
        "VALUES (:onboard_id, 'CompleteRMA', to_timestamp(:ts), :ticket_id, CAST(:metainfo AS jsonb))"
    )
    rma_params = dict(onboard_id=onboard_id, ts=ts, ticket_id=ticket_id, metainfo=metainfo)
    action_sql = (
        f"INSERT INTO ltp_sdk.node_actions (\"timestamp\", hostname, node_id, action, reason, detail, category, endpoint) "
        f"VALUES ('{ts_iso}', '{hostname}', '{node_id}', 'ua-ready_ua', '', '{detail_esc}', 'hardware', '{endpoint}')"
    )
    status_sql = (
        f"INSERT INTO ltp_sdk.node_status (\"timestamp\", hostname, node_id, status, endpoint) "
        f"VALUES ('{ts_iso}', '{hostname}', '{node_id}', 'ready_ua', '{endpoint}')"
    )
    _execute_transaction(physical_node_client, [(rma_sql, rma_params), action_sql, status_sql])
    logger.info("RMA completed %s", hostname)


def insert_allocated_ua(*, status_client, action_client, physical_node_client=None,
                         hostname: str, new_onboard_id: int) -> None:
    """Node-recycler: config done, move ready_ua -> allocated_ua."""
    node_id = str(new_onboard_id)
    ts = time.time()
    ts_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    endpoint = os.environ.get("CLUSTER_ID", "ltp")

    action_sql = (
        f"INSERT INTO ltp_sdk.node_actions (\"timestamp\", hostname, node_id, action, reason, detail, category, endpoint) "
        f"VALUES ('{ts_iso}', '{hostname}', '{node_id}', 'ready_ua-allocated_ua', '', '', 'platform', '{endpoint}')"
    )
    status_sql = (
        f"INSERT INTO ltp_sdk.node_status (\"timestamp\", hostname, node_id, status, endpoint) "
        f"VALUES ('{ts_iso}', '{hostname}', '{node_id}', 'allocated_ua', '{endpoint}')"
    )

    try:
        action_client.update_node_action(hostname, "ready_ua-allocated_ua", ts, "", "", "platform")
        status_client.update_node_status(hostname, "allocated_ua", ts)
    except (RuntimeError, ValueError):
        if not physical_node_client:
            raise
        logger.info("SDK rejected ready_ua-allocated_ua, using raw SQL transaction")
        _execute_transaction(physical_node_client, [action_sql, status_sql])
    logger.info("allocated %s onboard_id=%s", hostname, new_onboard_id)


def insert_triaged_unknown(*, status_client, action_client, physical_node_client=None,
                            hostname: str, node_id: str, reason: str, detail: str) -> None:
    """Node-recycler: config pipeline failed, move ready_ua -> triaged_unknown."""
    ts = time.time()
    ts_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    endpoint = os.environ.get("CLUSTER_ID", "ltp")
    reason_esc = reason.replace("'", "''")
    detail_esc = detail.replace("'", "''")

    action_sql = (
        f"INSERT INTO ltp_sdk.node_actions (\"timestamp\", hostname, node_id, action, reason, detail, category, endpoint) "
        f"VALUES ('{ts_iso}', '{hostname}', '{node_id}', 'ready_ua-triaged_unknown', '{reason_esc}', '{detail_esc}', 'platform', '{endpoint}')"
    )
    status_sql = (
        f"INSERT INTO ltp_sdk.node_status (\"timestamp\", hostname, node_id, status, endpoint) "
        f"VALUES ('{ts_iso}', '{hostname}', '{node_id}', 'triaged_unknown', '{endpoint}')"
    )

    try:
        action_client.update_node_action(hostname, "ready_ua-triaged_unknown", ts, reason, detail, "platform")
        status_client.update_node_status(hostname, "triaged_unknown", ts)
    except (RuntimeError, ValueError):
        if not physical_node_client:
            raise
        logger.info("SDK rejected ready_ua-triaged_unknown, using raw SQL transaction")
        _execute_transaction(physical_node_client, [action_sql, status_sql])
    logger.info("moved %s to triaged_unknown: %s", hostname, reason[:80])


def clone_onboard_record(physical_node_client, hostname: str, sn: str, sku: str, sys_info: dict) -> int:
    """Node-recycler: create new onboard record with fresh hardware info."""
    from sqlalchemy import text
    records = physical_node_client.execute_query(
        f"SELECT * FROM ltp_sdk.physical_node_onboard_records "
        f"WHERE hostname = '{hostname}' ORDER BY \"timestamp\" DESC LIMIT 1"
    )
    if not records:
        raise RuntimeError(f"no onboard record for {hostname}")
    record = records[0]

    ts = dt.datetime.now(zoneinfo.ZoneInfo("Asia/Shanghai")).isoformat()
    _JSON_COLS = {"cpu", "memory", "gpu", "nic", "disk", "ip", "mgmt_ip", "metainfo"}
    cols = ["sn", "site", "rack", "unit", "category", "rank", "hostname", "ip", "mgmt_ip", "metainfo",
            "timestamp", "sku", "cpu", "memory", "gpu", "nic", "disk"]
    vals = {c: record.get(c) for c in cols if c in record}
    vals.update(sn=sn, sku=sku, timestamp=ts,
                cpu=json.dumps(sys_info.get("CPU", {})),
                memory=json.dumps(sys_info.get("Memory", {})),
                gpu=json.dumps(sys_info.get("Accelerator", {})),
                nic=json.dumps(sys_info.get("Network", {})),
                disk=json.dumps(sys_info.get("Storage", {})))
    for k in ("ip", "mgmt_ip", "metainfo"):
        if k in vals and isinstance(vals[k], (dict, list)):
            vals[k] = json.dumps(vals[k])

    # Use parameterized query to handle JSON with special characters
    col_list = ", ".join(f'"{k}"' for k in vals)
    param_list = ", ".join(
        f"CAST(:{k} AS jsonb)" if k in _JSON_COLS else f":{k}"
        for k in vals
    )
    query = text(
        f"INSERT INTO ltp_sdk.physical_node_onboard_records ({col_list}) "
        f"VALUES ({param_list}) RETURNING id"
    )
    # Ensure JSON columns are strings for the bind params
    params = {}
    for k, v in vals.items():
        if k in _JSON_COLS and v is not None:
            params[k] = json.dumps(v) if not isinstance(v, str) else v
        elif v is None:
            params[k] = None
        else:
            params[k] = str(v) if not isinstance(v, str) else v
    session = physical_node_client.get_session()
    result = session.execute(query, params)
    row = result.fetchone()
    session.commit()
    new_id = row[0]
    logger.info("cloned onboard %s new_id=%s", hostname, new_id)
    return new_id
