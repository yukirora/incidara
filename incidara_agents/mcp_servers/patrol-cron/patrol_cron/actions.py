"""Action engine — executes findings (cordon, alert, create task).

Ported from NFD analysis_executor.py publish_detection_event() and
switch_monitor_cron.py _write_alert_file().
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import psycopg2
import requests

from patrol_cron.models import Finding

logger = logging.getLogger(__name__)

# Minimum confidence required per stage
STAGE_MIN_CONFIDENCE = {
    "log_only": 0.0,
    "create_task": 0.3,
    "submit_alert": 0.7,
    "auto_cordon": 0.9,
}

PATROL_ADMIN_USER = os.environ.get("PATROL_ADMIN_USER", "admin")
ALERT_MANAGER_URL = os.environ.get("ALERT_MANAGER_URL", "http://localhost:9093")
LTP_TOKEN = os.environ.get("LTP_TOKEN", "")
INTERNAL_SECRET = os.environ.get("INTERNAL_SECRET", "")
CHAT_UI_API_URL = os.environ.get("CHAT_UI_API_URL", "")
CHAT_UI_DB_URL = os.environ.get("CHAT_UI_DB_URL", "")

# INTERNAL_SECRET takes priority for alert-manager (bypasses rest-server JWT validation).
# LTP_TOKEN is used as fallback for other rest-server calls.
_ALERT_TOKEN = INTERNAL_SECRET or LTP_TOKEN


def execute_finding(finding: Finding, rule_stage: str, rule_id: str = "") -> bool:
    """Execute a finding based on the rule's current stage.

    Per-stage behavior:
      log_only:      record to DB, no action
      create_task:   create investigation task (all actions demoted here)
      submit_alert:  send admin alert; stop_abnormal_job demoted to notify_abnormal_job
      auto_cordon:   execute real action (cordon node, stop_abnormal_job job)

    Returns True if executed, False if skipped.
    """
    min_conf = STAGE_MIN_CONFIDENCE.get(rule_stage, 0.5)
    if finding.confidence < min_conf:
        logger.debug(f"Skipping {finding.target_id}: confidence {finding.confidence} < {min_conf}")
        return False

    if rule_stage == "log_only":
        logger.info(f"[log_only] rule={rule_id} target={finding.target_id} "
                     f"action={finding.action}")
        return True

    if rule_stage == "create_task":
        return _create_investigation_task(finding, rule_id)

    if rule_stage == "submit_alert":
        # submit_alert: send abnormal-job alert but with username=Admin (admin reviews, not job owner)
        if finding.action in ("stop_abnormal_job", "notify_abnormal_job"):
            return _execute_job_action(finding, rule_id, username=PATROL_ADMIN_USER)
        return _execute_infra_action(finding, rule_id)

    if rule_stage == "auto_cordon":
        # stop_abnormal_job → terminate job, notify_abnormal_job → email job owner
        if finding.action in ("stop_abnormal_job", "notify_abnormal_job"):
            return _execute_job_action(finding, rule_id)
        return _execute_infra_action(finding, rule_id)
    return False


def _execute_infra_action(finding: Finding, rule_id: str) -> bool:
    """Execute infrastructure actions (cordon/drain/alert/create_task)."""
    if finding.action == "cordon_node":
        ok, _ = _submit_triage_alert(hostname=finding.target_id, action="cordon",
                                    alertname=finding.action_params.get("alertname", rule_id),
                                    triaged_label=finding.action_params.get("triaged_label", "triaged_hardware"),
                                    evidence=finding.evidence)
        return ok
    elif finding.action == "drain_node":
        ok, _ = _submit_triage_alert(hostname=finding.target_id, action="drain",
                                    alertname=finding.action_params.get("alertname", rule_id),
                                    triaged_label=finding.action_params.get("triaged_label", "triaged_hardware"),
                                    evidence=finding.evidence)
        return ok
    elif finding.action == "cordon_switch_nodes":
        nodes = _lookup_switch_nodes(finding.target_id)
        if not nodes:
            ok, _ = _submit_triage_alert(hostname=finding.target_id, action="alert",
                                        alertname=finding.action_params.get("alertname", rule_id),
                                        evidence=finding.evidence)
            return ok
        return all(_submit_triage_alert(hostname=n, action="cordon",
                                        alertname=finding.action_params.get("alertname", rule_id),
                                        triaged_label="triaged_hardware",
                                        evidence=finding.evidence)[0] for n in nodes)
    elif finding.action == "alert":
        ok, _ = _submit_triage_alert(hostname=finding.target_id, action="alert",
                                    alertname=finding.action_params.get("alertname", rule_id),
                                    evidence=finding.evidence)
        return ok
    elif finding.action == "create_task":
        return _create_investigation_task(finding, rule_id)
    else:
        logger.warning(f"Unknown infra action: {finding.action}")
        return False


# ── Triage alert submission ───────────────────────────────────────────────

def _submit_admin_alert(finding: Finding, rule_id: str) -> bool:
    """Send admin alert via alert-manager (email, no cordon/stop)."""
    return _submit_triage_alert(
        hostname=finding.target_id,
        action="alert",
        alertname=finding.action_params.get("alertname", rule_id),
        evidence=finding.evidence,
    )


def _submit_triage_alert(
    hostname: str,
    action: str,
    alertname: str = "",
    triaged_label: str = "triaged_hardware",
    evidence: dict | None = None,
) -> tuple[bool, str]:
    """Submit alert to alert-manager (same format as NFD)."""
    alert = {
        "status": "firing",
        "labels": {
            "alertname": alertname,
            "action": action,
            "report_type": "admin-abnormal-node",
            "severity": "warning" if action == "alert" else "fatal",
            "trigger_time": datetime.now(timezone.utc).isoformat(),
            "node_name": hostname.lower(),
            "triaged_label": triaged_label,
        },
        "annotations": {
            "summary": f"Patrol detection: {alertname}",
            "description": json.dumps(evidence or {}, default=str)[:2000],
        },
    }

    try:
        headers = {"Content-Type": "application/json"}
        if _ALERT_TOKEN:
            headers["Authorization"] = f"Bearer {_ALERT_TOKEN}"

        resp = requests.post(
            f"{ALERT_MANAGER_URL}/alert-manager/api/v2/alerts",
            json=[alert],
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 200:
            logger.info(f"Alert sent: {alertname} → {action} {hostname}")
            return True, ""
        else:
            err = f"HTTP {resp.status_code}: {resp.text[:300]}"
            logger.error(f"Alert failed: {err}")
            return False, err
    except Exception as e:
        err = str(e)
        logger.error(f"Alert request failed: {err}")
        return False, err


# ── Switch → node lookup ──────────────────────────────────────────────────

def _lookup_switch_nodes(switch_hostname: str) -> list[str]:
    """Look up compute nodes connected to a switch."""
    topo_path = os.environ.get("SWITCH_TOPOLOGY_PATH", "")
    if not topo_path or not os.path.exists(topo_path):
        return []
    try:
        import yaml
        with open(topo_path) as f:
            data = yaml.safe_load(f) or {}
        switch_data = data.get(switch_hostname, {})
        ports = switch_data.get("ports", {})
        return list(set(ports.values()))
    except Exception as e:
        logger.error(f"Failed to load topology: {e}")
        return []


# ── Job action ──────────────────────────────────────────────────────────────

def _execute_job_action(finding: Finding, rule_id: str, username: str = "") -> bool:
    """Execute a job action via alert-manager.
    notify_abnormal_job → pai-abnormal-job-email (email job owner, action=none)
    stop_abnormal_job → pai-abnormal-job-terminate (terminate job, action=terminate)
    """
    target = finding.target_id
    if not username:
        parts = target.split("~", 1)
        username = parts[0] if len(parts) == 2 else ""

    # Map patrol actions to alert-manager actions
    action_map = {
        "notify_abnormal_job": "none",
        "stop_abnormal_job": "terminate",
    }
    alert_action = action_map.get(finding.action, "none")

    labels = {
        "alertname": "PAIAbnormalJob",
        "report_type": "abnormal-job",
        "severity": "warn",
        "trigger_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "username": username,
        "job_name": target,
        "action": alert_action,
    }
    annotations = {
        "summary": finding.evidence.get("summary", f"Patrol detection: {rule_id}"),
        "action": alert_action,
        "reason": finding.evidence.get("reason", f"Rule {rule_id} detected abnormal job"),
        "notification": finding.evidence.get("notification", ""),
    }

    return _send_alert(labels, annotations)


def _send_alert(labels: dict, annotations: dict | None = None, _retries: int = 1) -> bool:
    """Send alert to alert-manager API. Auto-retries once on 401 to handle transient auth issues."""
    if annotations is None:
        annotations = {"summary": f"Patrol detection: {labels.get('alertname', 'unknown')}"}
    alert = {
        "status": "firing",
        "labels": labels,
        "annotations": annotations,
    }
    last_err = ""
    for attempt in range(_retries + 1):
        try:
            headers = {"Content-Type": "application/json"}
            if _ALERT_TOKEN:
                headers["Authorization"] = f"Bearer {_ALERT_TOKEN}"
            resp = requests.post(
                f"{ALERT_MANAGER_URL}/alert-manager/api/v2/alerts",
                json=[alert], headers=headers, timeout=30,
            )
            if resp.status_code == 200:
                logger.info(f"Alert: {labels['alertname']} → {labels.get('action')}")
                return True
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code == 401 and attempt < _retries:
                logger.warning(f"Alert 401 on attempt {attempt+1}, retrying...")
                continue
            logger.error(f"Alert failed ({resp.status_code}): {resp.text[:200]}")
            return False
        except Exception as e:
            last_err = str(e)
            logger.error(f"Alert request failed: {last_err}")
            return False
    logger.error(f"Alert failed after {_retries + 1} attempts: {last_err}")
    return False


# ── Investigation task ────────────────────────────────────────────────────

def _create_investigation_task(finding: Finding, rule_id: str) -> bool:
    """Create an investigation task in Chat UI for the agent to pick up.

    Uses the same API as delegate_to_agent: POST /api/agents/{agent_id}/sessions
    which creates session + task + sends prompt to gateway.
    """
    if not CHAT_UI_API_URL:
        logger.info(
            f"[dry-run] Would create task: rule={rule_id} "
            f"target={finding.target_id} evidence={finding.evidence}"
        )
        return True

    chat_user = os.environ.get("CHAT_UI_USER", "")
    chat_password = os.environ.get("CHAT_UI_PASSWORD", "")
    if not chat_user or not chat_password:
        logger.warning("No CHAT_UI_USER/PASSWORD set — skipping task creation")
        return False

    # Determine target agent based on target type
    agent_id = os.environ.get("AGENT_NAME", "detection")

    title = f"[patrol] Investigate {finding.target_id} ({rule_id})"

    # Dedup: check by title directly in DB to avoid API pagination limits.
    # The API only returns first 100 results, but we may have 100+ waiting_input tasks.
    # Direct SQL bypasses this entirely.
    try:
        if CHAT_UI_DB_URL:
            conn = psycopg2.connect(CHAT_UI_DB_URL)
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM tasks WHERE title = %s AND status = 'waiting_input' LIMIT 1",
                (title,),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                logger.info(f"Dedup: task {row[0]} already exists for {finding.target_id} ({rule_id})")
                return True  # task exists — skip creating duplicate
    except Exception as e:
        logger.warning(f"Dedup check failed: {e}, creating task anyway")

    # Compute stable signature hash for memory matching (Feature 2 B6)
    import hashlib
    sig_parts = [
        rule_id,
        finding.target_id.split("-")[0] if "-" in finding.target_id else finding.target_id,
        finding.evidence.get("error_code", finding.evidence.get("xid_code", "")),
    ]
    signature_hash = hashlib.sha256(":".join(str(p) for p in sig_parts).encode()).hexdigest()[:16]

    prompt = (
        f"Patrol rule '{rule_id}' detected a potential issue on {finding.target_id}.\n"
        f"Severity: {finding.severity}\n"
        f"Evidence: {json.dumps(finding.evidence, default=str)}\n\n"
        f"Please investigate and record your verdict."
    )

    try:
        session = requests.Session()
        # Login first to get session cookie
        login_resp = session.post(
            f"{CHAT_UI_API_URL}/api/auth/login",
            json={"email": chat_user, "password": chat_password},
            timeout=15,
        )
        if not login_resp.ok:
            logger.error(f"Chat UI login failed ({login_resp.status_code}): {login_resp.text[:200]}")
            return False

        # Create session + task on target agent
        resp = session.post(
            f"{CHAT_UI_API_URL}/api/agents/{agent_id}/sessions",
            json={
                "prompt": prompt,
                "title": title,
                "completion_mode": "manual",
                "signature_hash": signature_hash,
            },
            timeout=30,
        )
        if resp.ok:
            result = resp.json()
            task_id = result.get("task", {}).get("id", "?")
            logger.info(f"Task created for {finding.target_id} ({rule_id}): task_id={task_id} agent={agent_id}")
            return True
        else:
            logger.error(f"Task creation failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Task creation request failed: {e}")
        return False
