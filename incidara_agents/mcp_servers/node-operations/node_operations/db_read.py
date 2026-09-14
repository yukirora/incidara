"""DB read operations via ltp_storage SDK.

Used by: readonly role, diagnosis role, ops role, node-recycler.
All functions are read-only — safe for any agent.
"""
import logging

logger = logging.getLogger(__name__)
_TERMINAL_REPAIR_STATUSES = {"已完成", "已撤销", "已撤回", "已拒绝"}


def create_clients():
    """Create SDK clients. Requires ltp_storage[db] installed.

    All clients are created with endpoint='ltp' to satisfy the NOT NULL
    constraint on node_status.endpoint and node_actions.endpoint columns.
    Override via CLUSTER_ID env var if needed.
    """
    from ltp_storage.factory import (
        create_node_status_client, create_node_action_client, create_physical_node_onboard_client,
    )
    import os
    endpoint = os.environ.get("CLUSTER_ID", "ltp")
    return (create_node_status_client(endpoint),
            create_node_action_client(endpoint),
            create_physical_node_onboard_client())


def is_ticket_completed(status: str) -> bool:
    return status in _TERMINAL_REPAIR_STATUSES


# ---------------------------------------------------------------------------
# Node status / actions
# ---------------------------------------------------------------------------

def get_nodes_by_status(status_client, status: str):
    return status_client.get_nodes_by_status(status)


def get_nodes_by_status_with_alerts(physical_node_client, status: str, category: str = None):
    """Get nodes in a given status with compact alert context.

    Returns: hostname, triaged_timestamp, status, validating_timestamp,
             alert_count, alert_names (comma-separated, max 10),
             alert_summaries (truncated, max 5 unique summaries).

    Alert summaries are aggressively truncated to keep output small —
    this tool is for Phase 1 discovery, not deep investigation.
    Use get_node_alerts(hostname, alertname="...") for detailed alert data.
    """
    sql = f"""
    WITH latest_triaged_nodes AS (
        SELECT DISTINCT ON (hostname)
            hostname,
            timestamp AS triaged_timestamp,
            status
        FROM ltp_sdk.node_status
        ORDER BY hostname, timestamp DESC
    ),
    validating_times AS (
        SELECT
            lt.hostname,
            lt.triaged_timestamp,
            lt.status,
            (
                SELECT MAX(timestamp)
                FROM ltp_sdk.node_status ns
                WHERE ns.hostname = lt.hostname
                    AND ns.status = 'validating'
                    AND ns.timestamp < lt.triaged_timestamp
            ) AS validating_timestamp
        FROM latest_triaged_nodes lt
        WHERE lt.status = '{status}'
    )
    SELECT
        att.hostname,
        att.triaged_timestamp,
        att.status,
        att.validating_timestamp,
        pn.category,
        COUNT(ar.id) AS alert_count,
        (SELECT string_agg(DISTINCT ar2.alertname, ', ' ORDER BY ar2.alertname)
         FROM ltp_sdk.alert_records ar2
         WHERE ar2.node_name = att.hostname
           AND ar2.timestamp >= COALESCE(att.validating_timestamp, att.triaged_timestamp - INTERVAL '7 days')
           AND ar2.timestamp <= att.triaged_timestamp
        ) AS alert_names,
        (SELECT string_agg(DISTINCT LEFT(sub.summary, 200), ' ||| ')
         FROM (
             SELECT DISTINCT ON (summary) summary
             FROM ltp_sdk.alert_records
             WHERE node_name = att.hostname
               AND timestamp >= COALESCE(att.validating_timestamp, att.triaged_timestamp - INTERVAL '7 days')
               AND timestamp <= att.triaged_timestamp
             ORDER BY summary, timestamp DESC
             LIMIT 5
         ) sub
        ) AS alert_summaries
    FROM validating_times att
    LEFT JOIN ltp_sdk.alert_records ar
        ON ar.node_name = att.hostname
        AND ar.timestamp >= COALESCE(att.validating_timestamp, att.triaged_timestamp - INTERVAL '7 days')
        AND ar.timestamp <= att.triaged_timestamp
    GROUP BY att.hostname, att.validating_timestamp, att.triaged_timestamp, att.status, pn.category
    ORDER BY att.triaged_timestamp DESC
    """
    if category:
        sql = sql.replace("FROM validating_times att",
                          "FROM validating_times att JOIN ltp_sdk.physical_node_onboard_records pn ON pn.hostname = att.hostname")
        sql = sql.replace("GROUP BY att.hostname",
                          f"WHERE pn.category = '{category}' GROUP BY att.hostname")
    else:
        sql = sql.replace("FROM validating_times att",
                          "FROM validating_times att LEFT JOIN ltp_sdk.physical_node_onboard_records pn ON pn.hostname = att.hostname")
    return physical_node_client.execute_query(sql)


def get_node_history(action_client, hostname: str, days: int = 30,
                     start_ts: str = "", end_ts: str = ""):
    """Get node action history.

    Args:
        hostname: Node hostname.
        days: Lookback days when start_ts is empty (default 30).
        start_ts: Start timestamp (ISO). If empty, uses NOW() - days.
        end_ts: End timestamp (ISO). If empty, uses NOW().
    """
    import datetime as dt
    if start_ts and end_ts:
        start = start_ts
        end = end_ts
    elif start_ts:
        start = start_ts
        end = dt.datetime.now(dt.timezone.utc).isoformat()
    else:
        start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
        end = dt.datetime.now(dt.timezone.utc).isoformat()
    return action_client.get_node_actions(hostname, start, end)


def get_node_history_with_alerts(physical_node_client, hostname: str,
                                  hours: int = 72, start_ts: str = "",
                                  end_ts: str = ""):
    """Get node action history enriched with alert_records.

    Returns a dict with:
      - "actions": raw node_actions rows (same as get_node_history)
      - "alerts": alert_records rows in the same time window, providing
        richer/different data than what's embedded in node_actions.detail.
        Notably, NotifyUnvalidatedNodes in alert_records contains actual
        benchmark failure details (baseline/actual/variance) that the
        node_actions.detail version often summarizes as "reason: undefined".

    Args:
        hostname: Node hostname.
        hours: Lookback hours when start_ts is empty (default 72).
        start_ts: Start timestamp (ISO). If empty, uses NOW() - hours.
        end_ts: End timestamp (ISO). If empty, uses NOW().
    """
    # Build time conditions
    if start_ts and end_ts:
        actions_where = f"timestamp >= '{start_ts}'::timestamptz AND timestamp <= '{end_ts}'::timestamptz"
        alerts_start = f"'{start_ts}'::timestamptz"
    elif start_ts:
        actions_where = f"timestamp >= '{start_ts}'::timestamptz"
        alerts_start = f"'{start_ts}'::timestamptz"
    else:
        actions_where = f"timestamp >= NOW() - INTERVAL '{int(hours)} hours'"
        alerts_start = f"NOW() - INTERVAL '{int(hours)} hours'"

    # Get node_actions
    actions = physical_node_client.execute_query(
        f"SELECT * FROM ltp_sdk.node_actions "
        f"WHERE hostname = '{hostname}' "
        f"AND {actions_where} "
        f"ORDER BY timestamp DESC LIMIT 50"
    )

    # Determine alert time window from actions
    if actions:
        import datetime as dt
        latest_ts = actions[0]["timestamp"]
        if isinstance(latest_ts, str):
            latest_ts = dt.datetime.fromisoformat(latest_ts.replace("Z", "+00:00"))
        if not start_ts:
            computed_start = latest_ts - dt.timedelta(hours=hours)
            alerts_start = f"'{computed_start.isoformat()}'::timestamptz"
        else:
            alerts_start = f"'{start_ts}'::timestamptz"
    # else: use alerts_start as computed above

    # Get alert_records in the same time window
    alerts = physical_node_client.execute_query(
        f"SELECT alertname, severity, timestamp, summary, labels, annotations "
        f"FROM ltp_sdk.alert_records "
        f"WHERE node_name = '{hostname}' "
        f"AND timestamp >= {alerts_start} "
        f"ORDER BY timestamp DESC"
    )

    return {"actions": actions, "alerts": alerts}


def get_node_status_history(physical_node_client, hostname: str, days: int = 30, limit: int = 20):
    """Get recent status transitions for a node (last N days)."""
    sql = (
        f"SELECT hostname, timestamp, status "
        f"FROM ltp_sdk.node_status "
        f"WHERE hostname = '{hostname}' "
        f"AND timestamp >= NOW() - INTERVAL '{int(days)} days' "
        f"ORDER BY timestamp DESC "
        f"LIMIT {int(limit)}"
    )
    return physical_node_client.execute_query(sql)


def get_latest_action_by_state(action_client, hostname: str, node_id: str, state: str):
    return action_client.get_latest_action_by_state(hostname, node_id, state)


# ---------------------------------------------------------------------------
# Node detail / onboard records
# ---------------------------------------------------------------------------


def _summarize_cpu(cpu: dict) -> str:
    """Summarize CPU: one line like '2x INTEL XEON Platinum 8558, 192 cores, 2 NUMA'."""
    if not isinstance(cpu, dict):
        return str(cpu)
    sockets = cpu.get("Socket(s)", "?")
    model = cpu.get("Model name", "?")
    cores = cpu.get("CPU(s)", "?")
    numa = cpu.get("NUMA node(s)", "?")
    return f"{sockets}x {model}, {cores} cores, {numa} NUMA"


def _summarize_memory(memory: dict) -> str:
    """Summarize memory: one line like '2T'."""
    if not isinstance(memory, dict):
        return str(memory)
    return memory.get("total_capacity", "?")


def _summarize_gpu(gpu: dict) -> dict:
    """Summarize GPU: one-line overview + per-GPU ECC status only.
    
    Drops full topo matrix (~2KB), full nvidia-smi -q dump (~76KB).
    Keeps: count, driver, cuda, fabricmanager, per-GPU model/serial/ECC counts.
    """
    if not isinstance(gpu, dict):
        return gpu
    # Extract driver/cuda from different possible locations
    nvidia = gpu.get("nvidia_info", {})
    driver = nvidia.get("driver_version") or gpu.get("driver_version", "?")
    cuda = nvidia.get("cuda_version") or gpu.get("cuda_version", "?")
    fm = gpu.get("nvidia-fabricmanager_version") or gpu.get("fabricmanager_version", "")

    gpu_list = nvidia.get("gpu", []) if isinstance(nvidia, dict) else []
    count = gpu.get("gpu_count", len(gpu_list))

    # Build per-GPU summary: model, serial, ECC errors only
    gpu_summaries = []
    for g in (gpu_list or []):
        pci = g.get("@id") or g.get("pci_id", "?")
        model = g.get("product_name", "?")
        serial = g.get("serial", "?")
        # Extract volatile ECC counts
        vol_raw = g.get("ecc_errors", {}).get("volatile", {}) if isinstance(g.get("ecc_errors"), dict) else {}
        agg_raw = g.get("ecc_errors", {}).get("aggregate", {}) if isinstance(g.get("ecc_errors"), dict) else {}
        # Also try flat format (from onboard record)
        if not vol_raw:
            vol_raw = g.get("volatile_ecc", {})
        if not agg_raw:
            agg_raw = g.get("aggregate_ecc", {})
        vol_dram = vol_raw.get("dram_correctable", "0") if isinstance(vol_raw, dict) else "0"
        vol_sram = vol_raw.get("sram_correctable", "0") if isinstance(vol_raw, dict) else "0"
        vol_dram_unc = vol_raw.get("dram_uncorrectable", "0") if isinstance(vol_raw, dict) else "0"
        agg_dram = agg_raw.get("dram_correctable", "0") if isinstance(agg_raw, dict) else "0"
        agg_sram = agg_raw.get("sram_correctable", "0") if isinstance(agg_raw, dict) else "0"
        agg_dram_unc = agg_raw.get("dram_uncorrectable", "0") if isinstance(agg_raw, dict) else "0"
        gpu_summaries.append({
            "pci": pci,
            "model": model,
            "serial": serial,
            "ecc_volatile": f"dram={vol_dram},sram={vol_sram},dram_unc={vol_dram_unc}",
            "ecc_aggregate": f"dram={agg_dram},sram={agg_sram},dram_unc={agg_dram_unc}",
        })

    result = {
        "gpu_count": count,
        "driver_version": driver,
        "cuda_version": cuda,
    }
    if fm:
        result["fabricmanager_version"] = fm
    if gpu_summaries:
        result["gpus"] = gpu_summaries
    return result


def _summarize_nic(nic: dict) -> dict:
    """Summarize NIC: one-line IB overview + IP addresses only.
    
    Drops full ibstat dump (~34KB), full nic list with MAC/MTU/driver/firmware.
    Keeps: OFED version, IB port states (rate + state only), IB IP addresses.
    """
    if not isinstance(nic, dict):
        return nic
    summary = {
        "ofed_version": nic.get("ofed_version"),
    }
    # Compact IB status: just device, rate, state per port
    ib = nic.get("ib", {})
    if isinstance(ib, dict):
        ib_status = ib.get("ib_device_status", {})
        if isinstance(ib_status, dict):
            ports = {}
            for dev_name, dev_info in ib_status.items():
                if not isinstance(dev_info, dict):
                    continue
                port_info = dev_info.get("Port 1:", {})
                if isinstance(port_info, dict):
                    ports[dev_name] = {
                        "rate": port_info.get("Rate"),
                        "state": port_info.get("State"),
                        "link_layer": port_info.get("Link layer"),
                    }
            summary["ib_ports"] = ports

    # Compact NIC list: just logical_name, ipv4, speed, state
    nic_list = nic.get("nic", [])
    if isinstance(nic_list, list):
        summary["interfaces"] = []
        for n in nic_list:
            if not isinstance(n, dict):
                continue
            summary["interfaces"].append({
                "name": n.get("logical_name", "?"),
                "ipv4": n.get("ipv4", []),
                "speed": n.get("speed"),
                "state": n.get("state"),
            })
    return summary


def _summarize_disk(disk: dict) -> dict:
    """Summarize disk: just key filesystems (root, data), drop container mounts.
    
    Drops the full mount list (40+ container overlay/tmpfs entries),
    drops the raw block device mapping string (~15KB).
    """
    if not isinstance(disk, dict):
        return disk
    fs_list = disk.get("file_system", [])
    key_fs = []
    if isinstance(fs_list, list):
        for fs in fs_list:
            if not isinstance(fs, dict):
                continue
            mounted = fs.get("Mounted", "")
            # Only keep real filesystems (not container overlays, tmpfs, shm)
            if mounted in ("/", "/mntsys", "/mntext", "/boot/efi"):
                key_fs.append({
                    "mount": mounted,
                    "size": fs.get("Size"),
                    "avail": fs.get("Avail"),
                    "type": fs.get("Type"),
                    "device": fs.get("Filesystem"),
                })
    return {"file_system": key_fs} if key_fs else {}


def _summarize_detail(record: dict) -> dict:
    """Produce a brief version of a node onboard record.
    
    Replaces large hardware inventory JSONB columns with compact summaries:
      - cpu: ~1.5KB → one-line string
      - memory: ~4.3KB → one-line string  
      - gpu: ~76KB → per-GPU ECC summary (~500B)
      - nic: ~34KB → port states + IPs (~500B)
      - disk: ~15KB → key filesystems only (~200B)
      - metainfo: dropped entirely
    Total ~225KB → ~2KB.
    """
    SKIP_KEYS = {"metainfo"}
    result = {}
    for k, v in record.items():
        if k in SKIP_KEYS:
            continue
        elif k == "cpu":
            result[k] = _summarize_cpu(v) if isinstance(v, dict) else v
        elif k == "memory":
            result[k] = _summarize_memory(v) if isinstance(v, dict) else v
        elif k == "gpu":
            result[k] = _summarize_gpu(v) if isinstance(v, dict) else v
        elif k == "nic":
            result[k] = _summarize_nic(v) if isinstance(v, dict) else v
        elif k == "disk":
            result[k] = _summarize_disk(v) if isinstance(v, dict) else v
        else:
            result[k] = v
    return result


def get_node_detail(physical_node_client, hostname: str, brief: bool = True) -> dict | None:
    """Get node onboard record.

    By default (brief=True), returns a compact version that summarizes the
    large hardware inventory JSONB columns.
    """
    records = physical_node_client.execute_query(
        f"SELECT * FROM ltp_sdk.physical_node_onboard_records "
        f"WHERE hostname = '{hostname}' ORDER BY \"timestamp\" DESC LIMIT 1"
    )
    if not records:
        return None
    return _summarize_detail(records[0]) if brief else records[0]


def resolve_ip(physical_node_client, ip: str) -> dict | None:
    """Resolve an IP address to a node hostname.

    Searches ip[] and mgmt_ip[] JSONB arrays for the IP.
    Returns the latest matching record with hostname, ip list, bmc_ip, onboard_id.
    Works for both management IPs and BMC IPs.
    """
    # Search ip[] array (management IPs)
    records = physical_node_client.execute_query(
        f"SELECT id, hostname, ip, mgmt_ip, category, sku "
        f"FROM ltp_sdk.physical_node_onboard_records "
        f"WHERE ip::text LIKE '%\"{ip}\"%' OR mgmt_ip::text LIKE '%\"{ip}\"%' "
        f"ORDER BY id DESC LIMIT 1"
    )
    if records:
        r = records[0]
        return {
            "hostname": r["hostname"],
            "onboard_id": r["id"],
            "ip": r["ip"],
            "mgmt_ip": r.get("mgmt_ip", []),
            "category": r.get("category", "?"),
            "sku": r.get("sku", "?"),
        }
    return None


def get_node_detail_by_onboard_id(physical_node_client, onboard_id: int, brief: bool = True) -> dict | None:
    """Get node onboard record by onboard_id.
    
    By default (brief=True), returns a compact version with summarized hardware info.
    Set brief=False for the complete raw record (~140KB).
    """
    records = physical_node_client.execute_query(
        f"SELECT * FROM ltp_sdk.physical_node_onboard_records "
        f"WHERE id = {int(onboard_id)}"
    )
    if not records:
        return None
    return _summarize_detail(records[0]) if brief else records[0]


def get_ticket_id_for_node(physical_node_client, hostname: str) -> tuple[str | None, int | None]:
    records = physical_node_client.execute_query(
        f"SELECT pna.ticket_id, pnor.id AS onboard_id "
        f"FROM ltp_sdk.physical_node_actions pna "
        f"JOIN ltp_sdk.physical_node_onboard_records pnor ON pna.onboard_id = pnor.id "
        f"WHERE pnor.hostname = '{hostname}' AND pna.op_type = 'InitiateRMA' "
        f"ORDER BY pna.\"timestamp\" DESC LIMIT 1"
    )
    if records:
        return records[0]["ticket_id"], records[0]["onboard_id"]
    return None, None


# ---------------------------------------------------------------------------
# Alert records
# ---------------------------------------------------------------------------

def get_node_alerts(physical_node_client, hostname: str,
                    start_ts: str = "", end_ts: str = "", hours: int = 72,
                    alertname: str = "", include_details: bool = False,
                    deduplicate: bool = True, limit: int = 200):
    """Get alert_records for a node within a time window.

    Returns deduplicated alerts by default — one row per unique
    (alertname, severity, summary) combination with first/last timestamps
    and occurrence count. This reduces hundreds of repeated alerts to a
    compact summary (~20 rows instead of 800+).

    Args:
        hostname: Node hostname.
        start_ts: Start timestamp (ISO format). If empty, uses NOW() - hours.
        end_ts: End timestamp (ISO format). If empty, uses NOW().
        hours: Lookback hours when start_ts is empty (default 72).
            Use 24 for very recent alerts, 168 (7d) for weekly context.
        alertname: Filter by alert name (exact match). Common values:
            NotifyUnvalidatedNodes, CordonValidationFailedNodes,
            NodeNotReady, NodeUnschedulable, PaiServicePodNotReady.
            Empty string = return all alert types.
        include_details: If True, include labels and annotations JSONB.
            WARNING: adds ~3x output size. Only use when you need the
            full structured data from specific alerts.
        deduplicate: If True (default), group identical alerts into one
            row with first_seen/last_seen/count. If False, return raw rows.
        limit: Maximum rows to return (default 200).
    """
    # Build WHERE clause
    conditions = [f"ar.node_name = '{hostname}'"]
    if alertname:
        conditions.append(f"ar.alertname = '{alertname}'")
    if start_ts and end_ts:
        conditions.append(f"ar.timestamp >= '{start_ts}'::timestamptz")
        conditions.append(f"ar.timestamp <= '{end_ts}'::timestamptz")
    elif start_ts:
        conditions.append(f"ar.timestamp >= '{start_ts}'::timestamptz")
    else:
        conditions.append(f"ar.timestamp >= NOW() - INTERVAL '{int(hours)} hours'")
    where = " AND ".join(conditions)

    if deduplicate:
        # Deduplicated: one row per unique (alertname, severity, summary)
        detail_cols = ""
        if include_details:
            detail_cols = (
                f", (SELECT ar2.labels FROM ltp_sdk.alert_records ar2 "
                f"WHERE ar2.node_name = '{hostname}' AND ar2.alertname = ar.alertname "
                f"AND ar2.severity = ar.severity AND ar2.summary = ar.summary "
                f"AND ar2.timestamp >= NOW() - INTERVAL '{int(hours)} hours' "
                "ORDER BY ar2.timestamp DESC LIMIT 1) AS labels, "
                f"(SELECT ar3.annotations FROM ltp_sdk.alert_records ar3 "
                f"WHERE ar3.node_name = '{hostname}' AND ar3.alertname = ar.alertname "
                f"AND ar3.severity = ar.severity AND ar3.summary = ar.summary "
                f"AND ar3.timestamp >= NOW() - INTERVAL '{int(hours)} hours' "
                "ORDER BY ar3.timestamp DESC LIMIT 1) AS annotations"
            )
        sql = (
            f"SELECT ar.alertname, ar.severity, "
            f"LEFT(ar.summary, 500) AS summary, "
            f"MIN(ar.timestamp) AS first_seen, "
            f"MAX(ar.timestamp) AS last_seen, "
            f"COUNT(*) AS count"
            f"{detail_cols} "
            f"FROM ltp_sdk.alert_records ar "
            f"WHERE {where} "
            f"GROUP BY ar.alertname, ar.severity, ar.summary "
            f"ORDER BY last_seen DESC "
            f"LIMIT {int(limit)}"
        )
    else:
        # Raw rows
        cols = "ar.alertname, ar.severity, ar.timestamp, LEFT(ar.summary, 500) AS summary"
        if include_details:
            cols += ", ar.labels, ar.annotations"
        sql = (
            f"SELECT {cols} "
            f"FROM ltp_sdk.alert_records ar "
            f"WHERE {where} "
            f"ORDER BY ar.timestamp DESC "
            f"LIMIT {int(limit)}"
        )
    return physical_node_client.execute_query(sql)


# ---------------------------------------------------------------------------
# Job / Framework queries
# ---------------------------------------------------------------------------

def get_node_recent_jobs(physical_node_client, hostname: str,
                         start_ts: str = "", end_ts: str = "",
                         days: int = 14, limit: int = 20):
    """Get recent jobs that ran on a specific node (via framework_events.sourceHost).

    Returns: framework_name, user_name, job_name, job_state, exit_code, gpu_count,
             completion_time, exit_category, exit_reason

    Args:
        hostname: Node hostname.
        start_ts: Start timestamp (ISO format). If empty, uses NOW() - days.
        end_ts: End timestamp (ISO format). If empty, uses NOW().
            Use end_ts=triaged_timestamp to get jobs that ran BEFORE the node
            was triaged — more precise than days for investigation.
        days: Lookback days when start_ts is empty (default 14).
        limit: Max jobs to return (default 20).
    """
    conditions = [f"""fe."sourceHost" = '{hostname}'"""]
    if start_ts and end_ts:
        conditions.append(f"""fe."lastTimestamp" >= '{start_ts}'::timestamptz""")
        conditions.append(f"""fe."lastTimestamp" <= '{end_ts}'::timestamptz""")
    elif start_ts:
        conditions.append(f"""fe."lastTimestamp" >= '{start_ts}'::timestamptz""")
    elif end_ts:
        # Default start = end_ts - 14 days if only end_ts given
        conditions.append(f"""fe."lastTimestamp" >= ('{end_ts}'::timestamptz - INTERVAL '{int(days)} days')""")
        conditions.append(f"""fe."lastTimestamp" <= '{end_ts}'::timestamptz""")
    else:
        conditions.append(f"""fe."lastTimestamp" >= (NOW() - INTERVAL '{int(days)} days')""")
    where = " AND ".join(conditions)

    sql = f"""
    SELECT DISTINCT
        f.name AS framework_name,
        f."userName" AS user_name,
        f."jobName" AS job_name,
        f.state AS job_state,
        f."subState" AS sub_state,
        f."appExitCode" AS exit_code,
        f."totalGpuNumber" AS gpu_count,
        f."completionTime" AS completion_time,
        COALESCE(js.exit_category, '') AS exit_category,
        COALESCE(LEFT(js.exit_reason, 300), '') AS exit_reason
    FROM public.framework_events fe
    JOIN public.frameworks f ON f.name = fe."frameworkName"
    LEFT JOIN ltp_sdk.job_summary js ON js.job_name = f."jobName"
    WHERE {where}
    ORDER BY f."completionTime" DESC NULLS FIRST
    LIMIT {int(limit)}
    """
    return physical_node_client.execute_query(sql)


def get_job_details(physical_node_client, jobname: str, limit: int = 5):
    """Get job details by job name (supports partial match via LIKE).

    Returns job-level info + per-task details across ALL task roles.
    Each task includes: taskIndex, taskState, exitCode, containerNodeName,
    containerExitDiagnostics (error traceback), exitSpec (type, reason, solution).
    """
    # First get job-level info
    job_sql = f"""
    SELECT
        f.name AS framework_name,
        f."userName" AS user_name,
        f."jobName" AS job_name,
        f.state AS job_state,
        f."subState" AS sub_state,
        f."appExitCode" AS exit_code,
        f."totalGpuNumber" AS gpu_count,
        f."submissionTime" AS submitted,
        f."launchTime" AS launched,
        f."completionTime" AS completed,
        f.retries,
        f."platformRetries" AS platform_retries,
        f."userRetries" AS user_retries,
        COALESCE(js.exit_category, '') AS exit_category,
        COALESCE(LEFT(js.exit_reason, 500), '') AS exit_reason
    FROM public.frameworks f
    LEFT JOIN ltp_sdk.job_summary js ON js.job_name = f."jobName"
    WHERE f."jobName" LIKE '%{jobname}%'
    ORDER BY f."completionTime" DESC NULLS FIRST
    LIMIT {int(limit)}
    """
    jobs = physical_node_client.execute_query(job_sql)
    if not jobs:
        return []

    # For each job, extract per-task details from the snapshot
    results = []
    for job in jobs:
        framework_name = job.get("framework_name", "")
        # Get the full snapshot to extract task details
        snap_sql = f"""
        SELECT snapshot::text
        FROM public.frameworks
        WHERE name = '{framework_name}'
        LIMIT 1
        """
        snap_rows = physical_node_client.execute_query(snap_sql)
        task_details = []
        if snap_rows and snap_rows[0].get("snapshot"):
            try:
                import json as _json
                snapshot = _json.loads(snap_rows[0]["snapshot"])
                attempt_status = snapshot.get("status", {}).get("attemptStatus", {})
                task_role_statuses = attempt_status.get("taskRoleStatuses", [])
                for trs in task_role_statuses:
                    role_name = trs.get("name", "")
                    for ts in trs.get("taskStatuses", []):
                        attempt = ts.get("attemptStatus", {})
                        completion = attempt.get("completionStatus", {})
                        exit_spec = completion.get("type", {})
                        # DB snapshot uses different field names than API:
                        # index (not taskIndex), state (not taskState),
                        # podNodeName (not containerNodeName), podIP (not containerIp),
                        # completionStatus.code (not containerExitCode),
                        # completionStatus.diagnostics (not containerExitDiagnostics)
                        task_info = {
                            "task_role": role_name,
                            "task_index": ts.get("index"),
                            "task_state": ts.get("state", ""),
                            "container_node": attempt.get("podNodeName", ""),
                            "container_ip": attempt.get("podIP", ""),
                            "container_exit_code": completion.get("code"),
                            "exit_diagnostics": (completion.get("diagnostics", "") or "")[:2000],
                            "exit_spec_code": completion.get("code"),
                            "exit_spec_phrase": completion.get("phrase", ""),
                            "exit_spec_reason": exit_spec.get("reason", ""),
                            "exit_spec_type": exit_spec.get("name", ""),
                        }
                        task_details.append(task_info)
            except Exception:
                pass

        job_result = dict(job)
        job_result["tasks"] = task_details
        job_result["failed_tasks"] = [t for t in task_details if t.get("container_exit_code") is not None and t.get("container_exit_code") != 0]
        results.append(job_result)

    return results


def get_job_events(physical_node_client, job_name: str, limit: int = 20):
    """Get events for a specific job by job name.

    Accepts the human-readable job name (e.g. 'glm-infer_tp32_c7358368'),
    not the internal frameworkName hash. Matches via JOIN on frameworks table.

    Returns: timestamp, type, reason, message, source_host
    """
    sql = f"""
    SELECT
        fe."lastTimestamp" AS timestamp,
        fe.type,
        fe.reason,
        LEFT(fe.message, 300) AS message,
        fe."sourceHost" AS source_host
    FROM public.framework_events fe
    JOIN public.frameworks f ON f.name = fe."frameworkName"
    WHERE f."jobName" = '{job_name}'
    ORDER BY fe."lastTimestamp" DESC
    LIMIT {int(limit)}
    """
    return physical_node_client.execute_query(sql)


def get_validation_job(physical_node_client, hostname: str, limit: int = 5):
    """Get validation (superbench) jobs targeting a specific node.

    Finds superbench validation jobs by searching jobConfig for forceNodes
    containing the hostname. This finds BOTH scheduled and unscheduled jobs
    (WAITING/FailedScheduling) — the sourceHost-based query only finds jobs
    that actually ran on the node.

    Returns: job_name, framework_name, user_name, job_state, sub_state,
             submission_time, launch_time, completion_time
    """
    # Use jobConfig::text LIKE to match forceNodes: <hostname>
    # Also fallback: match snapshot::text for jobs where jobConfig is empty
    sql = f"""
    SELECT
        f."jobName" AS job_name,
        f.name AS framework_name,
        f."userName" AS user_name,
        f.state AS job_state,
        f."subState" AS sub_state,
        f."submissionTime" AS submission_time,
        f."launchTime" AS launch_time,
        f."completionTime" AS completion_time
    FROM public.frameworks f
    WHERE (f."jobName" LIKE '%superbench%' OR f."jobName" LIKE '%validation%')
      AND (
        f."jobConfig"::text LIKE '%forceNodes%{hostname}%'
        OR (f."jobConfig" IS NULL AND f.snapshot::text LIKE '%forceNodes%{hostname}%')
      )
    ORDER BY f."submissionTime" DESC
    LIMIT {int(limit)}
    """
    return physical_node_client.execute_query(sql)


# ---------------------------------------------------------------------------
# Triage reference data
# ---------------------------------------------------------------------------

def get_existing_reasons(physical_node_client):
    """Get all existing triage reasons from node_actions for reference.

    Returns rows with: status, reason, usage_count
    """
    sql = """
    SELECT 'triaged_hardware' AS status, reason, COUNT(*) AS usage_count
    FROM ltp_sdk.node_actions
    WHERE action LIKE '%triaged_hardware'
        AND reason IS NOT NULL AND reason != ''
    GROUP BY reason
    UNION ALL
    SELECT 'triaged_platform' AS status, reason, COUNT(*) AS usage_count
    FROM ltp_sdk.node_actions
    WHERE action LIKE '%triaged_platform'
        AND reason IS NOT NULL AND reason != ''
    GROUP BY reason
    ORDER BY status, usage_count DESC
    """
    return physical_node_client.execute_query(sql)
