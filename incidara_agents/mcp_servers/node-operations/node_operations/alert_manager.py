"""Alert Manager API client — send triage and validation alerts to LTP.

Used by both triage and repair agents to:
  - Submit validation (revalidation) requests via admin-validate-node alerts
  - Submit triage transitions via admin-abnormal-node alerts

Auth token resolution (in priority order):
  1. INTERNAL_SECRET — bypasses rest-server JWT validation, no rate limit
  2. LTP_TOKEN — pre-obtained JWT token
  3. Cached token file (~/.ltp_tokens/) — auto-refreshed via LTP_TOKEN
"""

import json
import os
import subprocess
import time
import urllib.request
import urllib.parse
import urllib.error
import logging

logger = logging.getLogger(__name__)

TOKEN_DIR = os.path.expanduser("~/.ltp_tokens")
TOKEN_MAX_AGE_SECS = 28800  # 8 hours


def _get_host_id() -> str:
    host = os.environ.get("LTP_HOST", "")
    if not host:
        return ""
    return host.replace("http://", "").replace("https://", "").replace("/", "_").rstrip("_")


def _token_file_path() -> str:
    return os.path.join(TOKEN_DIR, f"{_get_host_id()}_token")


def _token_is_fresh(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < TOKEN_MAX_AGE_SECS


def _get_token() -> str:
    # Priority 1: INTERNAL_SECRET (bypasses rest-server JWT validation, no rate limit)
    token = os.environ.get("INTERNAL_SECRET", "")
    if token:
        return token
    # Priority 2: LTP_TOKEN env var
    token = os.environ.get("LTP_TOKEN", "")
    if token:
        # Cache to file for future use
        os.makedirs(TOKEN_DIR, exist_ok=True)
        with open(_token_file_path(), "w") as f:
            f.write(token)
        return token
    # Priority 3: Fresh token file
    tf = _token_file_path()
    if _token_is_fresh(tf):
        with open(tf) as f:
            return f.read().strip()
    # Fallback: stale file token
    if os.path.isfile(tf):
        with open(tf) as f:
            return f.read().strip()
    return ""


def _send_alerts(alerts: list[dict], dry_run: bool = False) -> dict:
    """Send alerts to the Alert Manager API. Returns response info."""
    # ALERT_MANAGER_URL overrides LTP_HOST for alert-manager endpoint
    alert_url = os.environ.get("ALERT_MANAGER_URL", "") or os.environ.get("LTP_HOST", "")
    if not alert_url:
        raise RuntimeError("ALERT_MANAGER_URL or LTP_HOST not set")

    api_url = f"{alert_url.rstrip('/')}/alert-manager/api/v2/alerts"
    payload = json.dumps(alerts, indent=2)

    if dry_run:
        return {"dry_run": True, "alert_count": len(alerts), "url": api_url}

    token = _get_token()
    if not token:
        raise RuntimeError("No auth token. Set INTERNAL_SECRET or LTP_TOKEN")

    result = subprocess.run(
        [
            "curl", "-s", "-w", "\n%{http_code}",
            "-X", "POST",
            "-H", "Content-Type: application/json",
            "-H", f"Authorization: Bearer {token}",
            "-d", payload,
            "--connect-timeout", "10",
            "--max-time", "30",
            api_url,
        ],
        capture_output=True, text=True, timeout=60,
    )

    lines = result.stdout.strip().rsplit("\n", 1)
    body = lines[0] if len(lines) > 1 else ""
    status_code = int(lines[-1]) if lines[-1].isdigit() else 0

    if status_code not in (200, 201):
        raise RuntimeError(f"Alert Manager request failed with status {status_code}: {body}")

    return {"status": status_code, "body": body, "alert_count": len(alerts)}


def _check_active_validation(hostname: str) -> dict:
    """Check for active validation jobs on a node.

    Returns dict with:
      - has_active_running: bool — a RUNNING job younger than max_run_hours
      - active_job_name: str — name of the active running job (if any)
      - active_job_age_hours: float — age of the active running job
      - stale_jobs: list[str] — framework names of jobs to stop (WAITING, STOPPING,
        or RUNNING longer than max_run_hours)
      - stopped_jobs: list[str] — framework names that were actually stopped
    """
    from node_operations.db import create_clients
    from node_operations.db_read import get_validation_job
    from datetime import datetime, timezone

    max_run_hours = 6
    result = {
        "has_active_running": False,
        "active_job_name": "",
        "active_job_fw_id": "",
        "active_job_age_hours": 0.0,
        "stale_jobs": [],
        "stopped_jobs": [],
    }

    _, _, pc = create_clients()
    try:
        jobs = get_validation_job(pc, hostname, limit=10)
    except Exception:
        return result

    if not jobs:
        return result

    now = datetime.now(timezone.utc)

    for job in jobs:
        job_name = job.JobName if hasattr(job, "JobName") else job.get("job_name", "")
        framework_name = job.FrameworkName if hasattr(job, "FrameworkName") else job.get("framework_name", "")
        user_name = job.UserName if hasattr(job, "UserName") else job.get("user_name", "")
        job_state = job.JobState if hasattr(job, "JobState") else job.get("job_state", "")
        submission_time = job.SubmissionTime if hasattr(job, "SubmissionTime") else job.get("submission_time", None)
        launch_time = job.LaunchTime if hasattr(job, "LaunchTime") else job.get("launch_time", None)

        state_upper = job_state.upper()

        # Terminal states — skip
        if state_upper in ("SUCCEEDED", "FAILED", "STOPPED", "COMPLETED"):
            continue

        # Build framework identifier
        fw_id = framework_name if framework_name else f"{user_name}~{job_name}"

        if state_upper == "RUNNING":
            # Use launchTime (when job actually started running) not submissionTime
            # A job can sit in WAITING for hours before launching
            age_hours = 0.0
            time_ref = launch_time or submission_time  # fallback to submission if no launch
            if time_ref:
                try:
                    ref_dt = time_ref if isinstance(time_ref, datetime) else datetime.fromisoformat(str(time_ref))
                    if ref_dt.tzinfo is None:
                        ref_dt = ref_dt.replace(tzinfo=timezone.utc)
                    age_hours = (now - ref_dt).total_seconds() / 3600
                except Exception:
                    age_hours = 0.0

            if age_hours < max_run_hours:
                # Still actively running — don't stop, don't submit new one
                result["has_active_running"] = True
                result["active_job_name"] = job_name
                result["active_job_fw_id"] = fw_id
                result["active_job_age_hours"] = round(age_hours, 1)
                logger.info("Active validation job %s for %s running %.1fh (< %dh), keeping it",
                            fw_id, hostname, age_hours, max_run_hours)
            else:
                # Running too long — it's stuck, mark as stale
                result["stale_jobs"].append(fw_id)
                logger.info("Stale validation job %s for %s running %.1fh (>= %dh), will stop",
                            fw_id, hostname, age_hours, max_run_hours)
        elif state_upper in ("WAITING", "STOPPING"):
            # WAITING = stuck (FailedScheduling etc), STOPPING = transitional
            result["stale_jobs"].append(fw_id)

    # Actually stop the stale jobs
    if result["stale_jobs"]:
        token = _get_token()
        rest_base = os.environ.get("LTP_HOST", "").rstrip("/") + "/rest-server/api/v2"
        if not rest_base or rest_base == "/rest-server/api/v2":
            raise RuntimeError("LTP_HOST env var not set")
        for fw_id in result["stale_jobs"]:
            try:
                stop_url = f"{rest_base}/jobs/{fw_id}/executionType"
                payload = json.dumps({"value": "STOP"}).encode()
                req = urllib.request.Request(
                    stop_url, data=payload, method="PUT",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    resp.read()
                result["stopped_jobs"].append(fw_id)
                logger.info("Stopped stale validation job %s for %s", fw_id, hostname)
            except Exception as e:
                logger.warning("Failed to stop stale validation job %s: %s", fw_id, e)

    return result


def submit_validation(hostname: str, summary: str = "", dry_run: bool = False) -> dict:
    """Submit a revalidation request for a node via Alert Manager.

    Sends an admin-validate-node alert which triggers the platform to
    schedule and run a validation (superbench) job on the node.

    Smart cleanup before submitting:
    - If a validation job is RUNNING and < 6 hours old: keep it, skip submitting
      a new job, just move node status to 'validating' via move_node_status.
    - If a validation job is RUNNING >= 6 hours: it's stuck, stop it and submit new.
    - WAITING/STOPPING jobs are always stopped (stuck or transitional).

    Args:
        hostname: Node hostname to revalidate.
        summary: Optional summary annotation.
        dry_run: If True, show what would be sent without sending.

    Returns:
        dict with status, alert_count, stopped_jobs, active_job info, and any error info.
    """
    # Check for active validation jobs
    active_check = _check_active_validation(hostname) if not dry_run else {
        "has_active_running": False, "active_job_name": "", "active_job_fw_id": "",
        "active_job_age_hours": 0, "stale_jobs": [], "stopped_jobs": [],
    }

    # If there's an active running job < 6h, don't submit a new one
    if active_check["has_active_running"]:
        logger.info("submit_validation %s: active job %s running %.1fh, skipping new submission, moving to validating",
                    hostname, active_check["active_job_name"], active_check["active_job_age_hours"])
        # Move node status to validating without submitting a new alert
        from node_operations.db import create_clients
        from node_operations.db_write import insert_status_transition
        from node_operations.db_read import get_node_detail
        sc, ac, pc = create_clients()
        detail = get_node_detail(pc, hostname, brief=True)
        node_id = str(detail["id"]) if detail else "0"
        insert_status_transition(
            status_client=sc, action_client=ac, physical_node_client=pc,
            hostname=hostname, node_id=node_id,
            from_status="", to_status="validating",
            reason="active_validation_exists",
            detail=f"Validation job {active_check['active_job_name']} already running ({active_check['active_job_age_hours']}h)",
            category="",
        )
        return {
            "status": 200,
            "action": "skipped_existing",
            "active_job": active_check["active_job_name"],
            "active_job_age_hours": active_check["active_job_age_hours"],
            "stopped_jobs": active_check["stopped_jobs"],
            "alert_count": 0,
        }

    if not summary:
        summary = f"Revalidation triggered by repair agent for {hostname}"

    alert = {
        "status": "firing",
        "labels": {
            "alertname": "admin-validate-node",
            "node_name": hostname,
        },
        "annotations": {
            "summary": summary,
        },
    }

    result = _send_alerts([alert], dry_run=dry_run)
    result["stopped_jobs"] = active_check["stopped_jobs"]
    result["active_job"] = None
    logger.info("submit_validation %s: %s (stopped %d stale jobs)", hostname, result, len(active_check["stopped_jobs"]))
    return result


def submit_triage_alert(hostname: str, triaged_label: str, alert_name: str,
                         summary: str = "", dry_run: bool = False) -> dict:
    """Submit a triage transition alert via Alert Manager.

    This is the Alert Manager path for status transitions — an alternative
    to move_node_status (which writes directly to DB). The Alert Manager
    path also triggers the platform's automated response (cordon, etc.).

    Args:
        hostname: Node hostname.
        triaged_label: Target triage label (e.g. 'triaged_hardware').
        alert_name: Alert name / reason (e.g. 'PCIeBandwidthDegradation').
        summary: Optional summary annotation.
        dry_run: If True, show what would be sent without sending.

    Returns:
        dict with status, alert_count, and any error info.
    """
    if not summary:
        summary = f"Reason: {alert_name}. Triage transition via agent."

    alert = {
        "status": "firing",
        "labels": {
            "alertname": alert_name,
            "report_type": "admin-abnormal-node",
            "action": "cordon",
            "severity": "error",
            "node_name": hostname,
            "triaged_label": triaged_label,
        },
        "annotations": {
            "summary": summary,
        },
    }

    result = _send_alerts([alert], dry_run=dry_run)
    logger.info("submit_triage_alert %s -> %s: %s", hostname, triaged_label, result)
    return result
