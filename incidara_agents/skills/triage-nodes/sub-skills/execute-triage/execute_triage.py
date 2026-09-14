#!/usr/bin/env python3
"""execute_triage.py — Send triage actions to the LTP Alert Manager API.

Usage:
    # Dry-run (show what would be sent, don't send)
    python3 execute_triage.py --dry-run triage --nodes node1,node2 --status triaged_hardware --reason PCIeBandwidthDegradation --summary "..."

    # Execute triage transition
    python3 execute_triage.py triage --nodes node1,node2 --status triaged_hardware --reason PCIeBandwidthDegradation --summary "..."

    # Dry-run revalidation
    python3 execute_triage.py --dry-run validate --nodes node1,node2

    # Execute revalidation
    python3 execute_triage.py validate --nodes node1,node2

    # From JSON file (for batch operations)
    python3 execute_triage.py --dry-run from-file actions.json
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error

import os

LTP_HOST = os.environ.get("LTP_HOST")
API_URL = f"{LTP_HOST.rstrip('/')}/alert-manager/api/v2/alerts"

# Auth token — loaded from env, token file, or auto-refreshed via PAI API
TOKEN_DIR = os.path.expanduser("~/.ltp_tokens")
TOKEN_MAX_AGE_SECS = 28800  # 8 hours — refresh before 9h JWT expiry


def _get_host_id() -> str:
    """Get a sanitized host identifier for token file lookup."""
    if not LTP_HOST:
        return ""
    return LTP_HOST.replace("http://", "").replace("https://", "").replace("/", "_").rstrip("_")


def _token_file_path() -> str:
    return os.path.join(TOKEN_DIR, f"{_get_host_id()}_token")


def _token_is_fresh(path: str) -> bool:
    """Check if token file exists and was modified within TOKEN_MAX_AGE_SECS."""
    if not os.path.isfile(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < TOKEN_MAX_AGE_SECS


def _get_token() -> str:
    """Get a valid LTP token. Priority: env var → fresh token file.

    For agent containers: LTP_TOKEN env var is always set.
    INTERNAL_SECRET takes priority if available (bypasses JWT validation).
    """
    # Priority 1: INTERNAL_SECRET (bypasses rest-server JWT validation, no rate limit)
    token = os.environ.get("INTERNAL_SECRET", "")
    if token:
        return token
    # Priority 2: Env var
    token = os.environ.get("LTP_TOKEN", "")
    if token:
        return token

    # Priority 3: Token file (if fresh)
    tf = _token_file_path()
    if _token_is_fresh(tf):
        with open(tf) as f:
            return f.read().strip()

    # Fallback: return stale file token (better than nothing)
    if os.path.isfile(tf):
        with open(tf) as f:
            return f.read().strip()

    return ""


def build_triage_alert(node_name: str, triaged_label: str, alert_name: str, summary: str) -> dict:
    """Build an admin-abnormal-node alert for triage transitions."""
    return {
        "status": "firing",
        "labels": {
            "alertname": alert_name,
            "report_type": "admin-abnormal-node",
            "action": "cordon",
            "severity": "error",
            "node_name": node_name,
            "triaged_label": triaged_label,
        },
        "annotations": {
            "summary": summary,
        },
    }


def build_validate_alert(node_name: str, summary: str = "Revalidation triggered after triage review") -> dict:
    """Build an admin-validate-node alert to trigger revalidation."""
    return {
        "status": "firing",
        "labels": {
            "alertname": "admin-validate-node",
            "node_name": node_name,
        },
        "annotations": {
            "summary": summary,
        },
    }


def send_alerts(alerts: list[dict], dry_run: bool = False) -> dict:
    """Send alerts to the API. Returns response info."""
    payload = json.dumps(alerts, indent=2)

    if dry_run:
        print("=== DRY RUN — would send the following request ===")
        print(f"POST {API_URL}")
        print(f"Content-Type: application/json")
        print(f"Payload ({len(alerts)} alert(s)):")
        print(payload)
        print("=== END DRY RUN ===")
        return {"dry_run": True, "alert_count": len(alerts)}

    token = _get_token()
    if not token:
        print("ERROR: No auth token. Set LTP_TOKEN or run: ltp.sh save-token <token>", file=sys.stderr)
        return {"status": 0, "error": "No auth token", "alert_count": len(alerts)}

    print(f"Sending {len(alerts)} alert(s) to {API_URL} ...")

    import subprocess
    result = subprocess.run(
        [
            "curl", "-s", "-w", "\n%{http_code}",
            "-X", "POST",
            "-H", "Content-Type: application/json",
            "-H", f"Authorization: Bearer {token}",
            "-d", payload,
            "--connect-timeout", "10",
            "--max-time", "30",
            API_URL,
        ],
        capture_output=True, text=True, timeout=60,
    )

    lines = result.stdout.strip().rsplit("\n", 1)
    body = lines[0] if len(lines) > 1 else ""
    status_code = int(lines[-1]) if lines[-1].isdigit() else 0

    print(f"Response: HTTP {status_code}")
    if body:
        print(f"Body: {body}")

    if status_code not in (200, 201):
        if result.stderr:
            print(f"Stderr: {result.stderr}", file=sys.stderr)
        return {"status": status_code, "error": body, "alert_count": len(alerts)}

    return {"status": status_code, "body": body, "alert_count": len(alerts)}


def cmd_triage(args):
    nodes = [n.strip() for n in args.nodes.split(",") if n.strip()]
    summary = args.summary or f"Reason: {args.reason}. Summary: Triage review. Reproducer: triage-unknown-nodes skill"
    alerts = [build_triage_alert(n, args.status, args.reason, summary) for n in nodes]

    print(f"Action: Transition {len(nodes)} node(s) to {args.status} / {args.reason}")
    for n in nodes:
        print(f"  - {n}")
    print()

    return send_alerts(alerts, dry_run=args.dry_run)


def cmd_validate(args):
    nodes = [n.strip() for n in args.nodes.split(",") if n.strip()]

    print(f"Action: Trigger revalidation for {len(nodes)} node(s) (with stale job cleanup)")
    for n in nodes:
        print(f"  - {n}")
    print()

    if args.dry_run:
        # Dry-run: just show what would be sent
        alerts = [build_validate_alert(n) for n in nodes]
        print(f"Payload ({len(alerts)} alert(s)):")
        print(json.dumps(alerts, indent=2))
        return {"dry_run": True, "alert_count": len(alerts)}

    # Use node_operations.submit_validation which includes stale job cleanup
    from node_operations.alert_manager import submit_validation
    results = {}
    for n in nodes:
        r = submit_validation(n)
        stopped = r.get("stopped_jobs", [])
        print(f"  {n}: status={r.get('status')} stopped={len(stopped)} stale job(s)"
              + (f" ({', '.join(stopped[:3])})" if stopped else ""))
        results[n] = r
    return results


def cmd_from_file(args):
    with open(args.file) as f:
        alerts = json.load(f)

    if not isinstance(alerts, list):
        print("ERROR: JSON file must contain an array of alert objects", file=sys.stderr)
        sys.exit(1)

    # Summarize
    triage_count = sum(1 for a in alerts if a.get("labels", {}).get("report_type") == "admin-abnormal-node")
    validate_count = sum(1 for a in alerts if a.get("labels", {}).get("alertname") == "admin-validate-node")
    print(f"Action: Send {len(alerts)} alert(s) from {args.file}")
    print(f"  - {triage_count} triage transition(s)")
    print(f"  - {validate_count} revalidation(s)")
    print()

    return send_alerts(alerts, dry_run=args.dry_run)


def main():
    parser = argparse.ArgumentParser(description="Send triage actions to LTP Alert Manager API")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be sent without sending")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # triage subcommand
    p_triage = subparsers.add_parser("triage", help="Transition nodes to a triaged status")
    p_triage.add_argument("--nodes", required=True, help="Comma-separated list of hostnames")
    p_triage.add_argument("--status", required=True, choices=["triaged_hardware", "triaged_platform"],
                          help="Target triage status")
    p_triage.add_argument("--reason", required=True, help="Reason label (e.g. PCIeBandwidthDegradation)")
    p_triage.add_argument("--summary", help="Summary annotation (Reason/Summary/Reproducer). Auto-generated if omitted.")
    p_triage.set_defaults(func=cmd_triage)

    # validate subcommand
    p_validate = subparsers.add_parser("validate", help="Trigger revalidation for nodes")
    p_validate.add_argument("--nodes", required=True, help="Comma-separated list of hostnames")
    p_validate.set_defaults(func=cmd_validate)

    # from-file subcommand
    p_file = subparsers.add_parser("from-file", help="Send alerts from a JSON file")
    p_file.add_argument("file", help="Path to JSON file containing alert array")
    p_file.set_defaults(func=cmd_from_file)

    args = parser.parse_args()
    result = args.func(args)

    if not args.dry_run and result.get("status", 0) not in (200, 201):
        sys.exit(1)


if __name__ == "__main__":
    main()
