import re
import ssl
import base64
import subprocess
import logging
import urllib.error
import urllib.request
from datetime import datetime, time, timezone
from node_operations.ssh import run_remote_command_capture

logger = logging.getLogger(__name__)


def get_sn_from_ipmi(ip: str, user: str = "operator", key: str = "") -> str:
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
    if key:
        cmd += ["-i", key]
    cmd += ["-n", f"{user}@{ip}", "sudo", "ipmitool", "fru", "list", "0"]
    result = subprocess.run(
        cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    for line in result.stdout.splitlines():
        m = re.match(r"^\s*Product Serial\s*:\s*(.+?)\s*$", line)
        if m:
            return m.group(1)
    raise RuntimeError(f"failed to parse Product Serial on {ip}")


def check_fabricmanager(ip: str, user: str, timeout: int) -> bool:
    rc, output = run_remote_command_capture(ip, user, "sudo systemctl status nvidia-fabricmanager", timeout)
    lines = [l.strip() for l in output.splitlines() if l.strip()]
    last = lines[-1] if lines else ""
    return rc == 0 and "Started NVIDIA fabric manager service." in last


def reset_bmc_password(mgmt_ips: list[str], bmc_user: str, old_pw: str, new_pw: str) -> None:
    from node_operations.init_stage import get_bundle_path
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix="txt", delete=False) as f:
        f.write("\n".join(mgmt_ips) + "\n")
        hostlist = f.name
    script = str(get_bundle_path("init_bundle") / "set_bmc_password_remote.sh")
    result = subprocess.run(["bash", script, hostlist, bmc_user, old_pw, new_pw],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    if result.returncode != 0:
        logger.warning("BMC password reset failed (exit %d): %s", result.returncode, result.stdout[:200])


def _parse_filter_time(value: str | None, is_end: bool = False) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.upper().endswith(" UTC"):
        text = text[:-4].strip()
    if text.endswith("Z"):
        text = text[:-1].strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%m/%d/%y %H:%M:%S",
        "%m/%d/%y %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    try:
        day = datetime.strptime(text, "%Y-%m-%d").date()
        return datetime.combine(day, time.max if is_end else time.min, tzinfo=timezone.utc)
    except ValueError:
        pass

    raise ValueError(
        f"Invalid time '{value}'. Use UTC formats like '2026-06-21 16:38:00 UTC' "
        "or '2026-06-21'."
    )


def _parse_sel_timestamp(line: str) -> datetime | None:
    # ipmitool SEL lines look like:
    #   75 | 03/05/26 | 15:50:26 UTC | Temperature GPU Temp | ...
    m = re.search(r"\|\s*(\d{2}/\d{2}/\d{2})\s*\|\s*(\d{2}:\d{2}:\d{2})\s+UTC\s*\|", line)
    if not m:
        return None
    return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%m/%d/%y %H:%M:%S").replace(
        tzinfo=timezone.utc
    )


def _filter_sel_output(output: str, since: str | None = None, until: str | None = None,
                       grep: str | None = None) -> str:
    start = _parse_filter_time(since) if since else None
    end = _parse_filter_time(until, is_end=True) if until else None
    pattern = re.compile(grep, re.IGNORECASE) if grep else None

    filtered = []
    for line in output.splitlines():
        if pattern and not pattern.search(line):
            continue
        if start or end:
            ts = _parse_sel_timestamp(line)
            if ts is None:
                continue
            if start and ts < start:
                continue
            if end and ts > end:
                continue
        filtered.append(line)
    return "\n".join(filtered) + ("\n" if filtered else "")


def bmc_sel_query(bmc_ip: str, bmc_user: str, bmc_password: str,
                  command: str = "sel_list", timeout: int = 15,
                  record_id: str | None = None, since: str | None = None,
                  until: str | None = None, grep: str | None = None) -> str:
    """Query BMC remotely via ipmitool (lanplus). No SSH to node required.

    Args:
        bmc_ip: BMC management IP address.
        bmc_user: BMC username (typically 'admin' or 'root').
        bmc_password: BMC password.
        command: One of sel_list, sel_elist, sel_info, sel_time_get, sel_get,
            chassis_status, chassis_poh, mc_watchdog_get, sensor_list, fru.
        timeout: Command timeout in seconds.
        record_id: SEL record ID for command="sel_get" (hex IDs like "84" are accepted).
        since: Optional UTC start time for filtering sel_list/sel_elist output.
        until: Optional UTC end time for filtering sel_list/sel_elist output.
        grep: Optional case-insensitive regex filter applied to command output.

    Returns:
        ipmitool stdout as string.
    """
    cmd_map = {
        "sel_list":       ["sel", "list"],
        "sel_elist":      ["sel", "elist"],
        "sel_info":       ["sel", "info"],
        "sel_time_get":   ["sel", "time", "get"],
        "chassis_status": ["chassis", "status"],
        "chassis_poh":    ["chassis", "poh"],
        "mc_watchdog_get": ["mc", "watchdog", "get"],
        "sensor_list":    ["sensor", "list"],
        "fru":            ["fru"],
    }
    if command == "sel_get":
        if not record_id:
            raise ValueError("record_id is required for command='sel_get'")
        args = ["sel", "get", str(record_id)]
    else:
        args = cmd_map.get(command)
    if not args:
        valid = sorted([*cmd_map.keys(), "sel_get"])
        raise ValueError(f"Unknown command: {command}. Use: {', '.join(valid)}")

    full_cmd = ["ipmitool", "-I", "lanplus", "-H", bmc_ip,
                "-U", bmc_user, "-P", bmc_password] + args
    result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"ipmitool failed (rc={result.returncode}): {result.stderr.strip()}")
    output = result.stdout
    if command in ("sel_list", "sel_elist") and (since or until or grep):
        output = _filter_sel_output(output, since=since, until=until, grep=grep)
    elif grep:
        output = _filter_sel_output(output, grep=grep)
    return output


def _detect_bmc_vendor(bmc_ip: str, timeout: int = 5) -> str:
    """Detect BMC vendor from Redfish ServiceRoot. Returns lowercase vendor name."""
    import json, urllib.request, ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(
            f"https://{bmc_ip}/redfish/v1/",
            headers={"Connection": "close"},
        )
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        d = json.loads(resp.read())
        vendor = d.get("Vendor", "").lower()
        if not vendor:
            vendor = d.get("Product", "").lower()
        return vendor
    except Exception:
        return ""


def get_kvm_screenshot(bmc_ip: str, bmc_user: str, bmc_password: str,
                       output_path: str = "/tmp/bmc_kvm.jpg") -> str:
    """Capture a KVM console screenshot from a BMC.

    Supported BMC types:
    - AMI MegaRAC SP-X (b300 nodes): Uses /api/session login + /api/remote-kvm.
      Note: AMI BMCs require username='admin' (not 'root'). We try both.
    - Supermicro (h200 nodes): Uses /cgi/login.cgi + sys_preview trigger +
      /cgi/url_redirect.cgi Snapshot fetch. Newer X14 firmware may not support
      the Snapshot CGI — returns a clear error in that case.

    Raises RuntimeError if the BMC type does not support screenshots or login fails.
    """
    import json
    import urllib.request
    import ssl
    import http.cookiejar

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    vendor = _detect_bmc_vendor(bmc_ip)

    if "supermicro" in vendor:
        return _screenshot_supermicro(bmc_ip, bmc_user, bmc_password,
                                      output_path, ctx)
    else:
        # AMI MegaRAC SP-X or unknown — try the AMI API
        return _screenshot_ami(bmc_ip, bmc_user, bmc_password,
                               output_path, ctx)


def _screenshot_ami(bmc_ip, bmc_user, bmc_password, output_path, ctx):
    """Screenshot via AMI MegaRAC SP-X /api/session + /api/remote-kvm."""
    import json
    import urllib.request
    import http.cookiejar

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(cj),
    )

    # AMI MegaRAC uses 'admin' not 'root'. Try provided user first, then 'admin'.
    users_to_try = [bmc_user]
    if bmc_user != "admin":
        users_to_try.append("admin")

    csrf_token = None
    for user in users_to_try:
        try:
            login_data = f"username={user}&password={bmc_password}".encode()
            req = urllib.request.Request(
                f"https://{bmc_ip}/api/session",
                data=login_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            resp = opener.open(req, timeout=10)
            login_resp = json.loads(resp.read())
            csrf_token = login_resp.get("CSRFToken", "")
            if csrf_token:
                logger.info("AMI BMC login OK as %s on %s", user, bmc_ip)
                break
        except Exception:
            continue

    if not csrf_token:
        raise RuntimeError(
            f"AMI BMC login failed on {bmc_ip}. Tried users: {users_to_try}. "
            "If this is a Supermicro BMC, it may not support the AMI screenshot API."
        )

    # Get screenshot
    req = urllib.request.Request(
        f"https://{bmc_ip}/api/remote-kvm",
        headers={"X-CSRFTOKEN": csrf_token},
    )
    resp = opener.open(req, timeout=15)
    data = resp.read()

    if len(data) < 100:
        raise RuntimeError(f"BMC screenshot too small ({len(data)} bytes), likely not an image")

    with open(output_path, "wb") as f:
        f.write(data)
    logger.info("AMI KVM screenshot saved to %s (%d bytes)", output_path, len(data))
    return output_path


def _screenshot_supermicro(bmc_ip, bmc_user, bmc_password, output_path, ctx):
    """Screenshot via Supermicro web UI login + sys_preview trigger + Snapshot fetch.

    Flow (reverse-engineered from Supermicro X14 SMT firmware v01.03.18):
      1) Login via /cgi/login.cgi to get SID cookie.
      2) Redfish session via /redfish/v1/SessionService/Sessions for auth token.
      3) Load dashboard to extract CSRF token from SmcCsrfInsert() call.
      4) POST /cgi/upgrade_process.cgi with fwtype=255 — check firmware update status.
      5) POST /cgi/op.cgi with op=sys_preview + CSRF_TOKEN header — trigger screen capture.
      6) Wait 3 seconds for capture to complete.
      7) GET /cgi/url_redirect.cgi?url_name=Snapshot&url_type=img — fetch JPEG image.

    The CSRF_TOKEN is a per-session token embedded in the dashboard HTML via
    SmcCsrfInsert("CSRF_TOKEN", "<token>"), sent as a request header on state-changing
    requests. It is NOT the _x_auth Redfish session token.

    Newer X14 firmware may not support the Snapshot CGI — raises RuntimeError.
    """
    import re
    import time
    import urllib.request
    import urllib.parse
    import http.cookiejar

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(cj),
    )

    # Step 1: Login via /cgi/login.cgi to get SID cookie
    login_data = urllib.parse.urlencode({"name": bmc_user, "pwd": bmc_password}).encode()
    req = urllib.request.Request(
        f"https://{bmc_ip}/cgi/login.cgi",
        data=login_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        resp = opener.open(req, timeout=10)
        resp.read()
    except Exception as e:
        raise RuntimeError(f"Supermicro BMC login failed on {bmc_ip}: {e}")

    sid_cookies = [c for c in cj if c.name == "SID"]
    if not sid_cookies:
        raise RuntimeError(f"Supermicro BMC login did not set SID cookie on {bmc_ip}")
    logger.info("Supermicro login OK on %s", bmc_ip)

    # Step 2: Load dashboard page to extract CSRF token
    csrf_token = None
    try:
        req = urllib.request.Request(
            f"https://{bmc_ip}/cgi/url_redirect.cgi?url_name=topmenu",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        resp = opener.open(req, timeout=10)
        page_html = resp.read().decode("utf-8", errors="replace")
        # Parse: SmcCsrfInsert("CSRF_TOKEN", "<token>");
        m = re.search(r'SmcCsrfInsert\s*\(\s*"CSRF_TOKEN"\s*,\s*"([^"]+)"\s*\)', page_html)
        if m:
            csrf_token = m.group(1)
            logger.info("Supermicro CSRF token obtained for %s", bmc_ip)
    except Exception as e:
        logger.warning("Failed to get CSRF token from %s: %s", bmc_ip, e)

    if not csrf_token:
        raise RuntimeError(
            f"Supermicro BMC ({bmc_ip}) could not obtain CSRF token. "
            "Screenshot requires the CSRF token from the dashboard page."
        )

    # Step 3: Check firmware update status via /cgi/upgrade_process.cgi
    common_headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "CSRF_TOKEN": csrf_token,
    }
    try:
        req = urllib.request.Request(
            f"https://{bmc_ip}/cgi/upgrade_process.cgi",
            data=b"fwtype=255",
            headers=common_headers,
            method="POST",
        )
        resp = opener.open(req, timeout=15)
        upgrade_xml = resp.read().decode("utf-8", errors="replace")
        # Check percent=0 means no update in progress
        if "<percent>0</percent>" not in upgrade_xml:
            raise RuntimeError(
                f"Supermicro BMC ({bmc_ip}) firmware update in progress, cannot take screenshot"
            )
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Supermicro firmware check failed on {bmc_ip}: HTTP {e.code}")
    logger.info("Supermicro firmware check OK on %s", bmc_ip)

    # Step 4: Trigger screen capture via /cgi/op.cgi
    try:
        req = urllib.request.Request(
            f"https://{bmc_ip}/cgi/op.cgi",
            data=b"op=sys_preview",
            headers=common_headers,
            method="POST",
        )
        resp = opener.open(req, timeout=15)
        op_resp = resp.read().decode("utf-8", errors="replace")
        if "Token Value is not matched" in op_resp:
            raise RuntimeError(
                f"Supermicro BMC ({bmc_ip}) CSRF token rejected. "
                "Token may have expired; retry the operation."
            )
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Supermicro sys_preview trigger failed on {bmc_ip}: HTTP {e.code}")
    logger.info("Supermicro sys_preview triggered on %s", bmc_ip)

    # Step 5: Wait for capture to complete (firmware takes ~3s)
    time.sleep(3)

    # Step 6: Fetch the snapshot image
    ts = time.strftime("%a %b %d %Y %H:%M:%S GMT+0000")
    encoded_ts = urllib.parse.quote(ts)
    url = (
        f"https://{bmc_ip}/cgi/url_redirect.cgi"
        f"?url_name=Snapshot&url_type=img&time_stamp={encoded_ts}"
    )
    req = urllib.request.Request(url)
    try:
        resp = opener.open(req, timeout=15)
        content_type = resp.headers.get("Content-Type", "")
        data = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError(
                f"Supermicro BMC ({bmc_ip}) firmware does not support the Snapshot CGI. "
                "This is a known limitation of some X14 firmware versions. "
                "Use bmc_query (SEL/sensors) for diagnostics instead."
            )
        raise RuntimeError(f"Supermicro screenshot fetch failed on {bmc_ip}: HTTP {e.code}")

    if len(data) < 100:
        raise RuntimeError(
            f"Supermicro screenshot response too small on {bmc_ip}: {len(data)} bytes"
        )

    # Supermicro firmware returns image/jpeg but with content-type application/octet-stream
    # Check for JPEG magic bytes (FFD8FF) instead of relying on Content-Type
    is_jpeg = data[:3] == b"\xff\xd8\xff"
    if not is_jpeg and "image" not in content_type:
        raise RuntimeError(
            f"Supermicro screenshot response invalid on {bmc_ip}: "
            f"size={len(data)}, content_type={content_type}, first_bytes={data[:8].hex()}"
        )

    with open(output_path, "wb") as f:
        f.write(data)
    logger.info("Supermicro KVM screenshot saved to %s (%d bytes)", output_path, len(data))
    return output_path


def bmc_health_log(
    bmc_ip: str,
    bmc_user: str,
    bmc_password: str,
    category: str = "h200",
    severity: str = "all",
    limit: int = 100,
) -> list[dict]:
    """Fetch BMC Health Event Log via Redfish API.

    Supermicro (h200): /redfish/v1/Systems/1/LogServices/Log1/Entries
      - Contains structured health events: GPU not present, NIC temp critical,
        PCIe errors, power supply events, etc.
      - Uses Basic auth with root:password

    AMI MegaRAC (b300): /redfish/v1/Managers/Self/LogServices/SEL/Entries
      - Contains IPMI SEL events in structured format.
      - Also available: GPUEventLog, EventLog, AuditLog
      - Uses Basic auth with admin:password

    Args:
        bmc_ip: BMC IP address.
        bmc_user: BMC username (root for Supermicro, admin for AMI).
        bmc_password: BMC password.
        category: Node category - "h200" for Supermicro, "b300" for AMI MegaRAC.
        severity: Filter by severity - "all", "Critical", "Warning", or "OK".
        limit: Maximum number of entries to return (default 100).

    Returns:
        List of event dicts with keys: id, timestamp, severity, message, message_id.
    """
    import json as _json

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # Select the correct Redfish endpoint based on BMC type
    if category == "b300":
        # AMI MegaRAC SP-X: SEL log + GPUEventLog
        endpoints = [
            f"https://{bmc_ip}/redfish/v1/Managers/Self/LogServices/SEL/Entries",
            f"https://{bmc_ip}/redfish/v1/Managers/Self/LogServices/GPUEventLog/Entries",
        ]
    else:
        # Supermicro X14: Health Event Log
        endpoints = [
            f"https://{bmc_ip}/redfish/v1/Systems/1/LogServices/Log1/Entries",
        ]

    all_entries = []
    seen_ids = set()

    for endpoint in endpoints:
        try:
            req = urllib.request.Request(endpoint)
            # Basic auth header
            credentials = base64.b64encode(f"{bmc_user}:{bmc_password}".encode()).decode()
            req.add_header("Authorization", f"Basic {credentials}")
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                data = _json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:
            logger.warning("Failed to fetch Redfish log from %s: %s", endpoint, e)
            continue

        members = data.get("Members", [])
        for entry in members:
            eid = entry.get("Id", "")
            # Deduplicate across endpoints
            dedup_key = f"{endpoint}:{eid}"
            if dedup_key in seen_ids:
                continue
            seen_ids.add(dedup_key)

            sev = entry.get("Severity", "OK")
            # Apply severity filter (strict match)
            if severity != "all" and sev != severity:
                continue

            all_entries.append({
                "id": eid,
                "timestamp": entry.get("Created", ""),
                "severity": sev,
                "message": entry.get("Message", ""),
                "message_id": entry.get("MessageId", ""),
            })

    # Sort by timestamp descending (most recent first)
    all_entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    return all_entries[:limit]


def bmc_power_cycle(
    hostname: str,
    bmc_ip: str,
    bmc_user: str = "admin",
    bmc_password: str = "",
    reset_bmc_password: str = "",
    poll_interval: int = 15,
    poll_timeout: int = 900,
    cold_reset_retry: bool = True,
) -> dict:
    """BMC power cycle: check BMC → power off → always-on → power on → poll → cold reset if stuck.

    This is a standalone BMC-level power cycle.  It does NOT run clear_node.sh
    or any SSH-level operations — it only deals with BMC power control.

    Used by:
      - reallocate_node (unreachable path)
      - reboot_and_wait (reachable path, Phase 2 BMC fault recovery)
      - As a standalone MCP tool for manual BMC recovery

    Args:
        hostname: Node hostname (for logging).
        bmc_ip: BMC management IP.
        bmc_user: BMC username.
        bmc_password: Primary BMC password.
        reset_bmc_password: Vendor/RMA BMC password fallback.
        poll_interval: Seconds between power-status polls (default 15s).
        poll_timeout: Seconds to wait for power-on per attempt (default 900 = 15min).
        cold_reset_retry: If True, do BMC cold reset + retry when power stays off.

    Returns:
        dict with keys:
          success: bool
          active_bmc_password: str — the password that worked
          steps: list[str] — step-by-step progress log
          error: str | None — error message if failed
    """
    import time as _time
    from node_operations.config import _ipmitool

    steps = []
    active_password = bmc_password

    def _log(msg: str):
        steps.append(msg)
        logger.info("bmc_power_cycle %s: %s", hostname, msg)

    # Step 1: Verify BMC is reachable — try both passwords with retries
    _log(f"checking BMC at {bmc_ip}")
    bmc_ok = False
    for attempt in range(3):
        if attempt > 0:
            _time.sleep(10)
        try:
            rc, power_status = _ipmitool(bmc_ip, bmc_user, bmc_password, "chassis", "power", "status")
            if rc == 0:
                bmc_ok = True
                _log(f"BMC reachable (primary password), {power_status.strip()}")
                break
        except Exception as e:
            logger.debug("BMC primary password attempt %d failed: %s", attempt + 1, e)
        if reset_bmc_password:
            try:
                rc, power_status = _ipmitool(bmc_ip, bmc_user, reset_bmc_password, "chassis", "power", "status")
                if rc == 0:
                    active_password = reset_bmc_password
                    bmc_ok = True
                    _log(f"BMC reachable (vendor password), {power_status.strip()}")
                    break
            except Exception as e2:
                logger.debug("BMC vendor password attempt %d failed: %s", attempt + 1, e2)

    if not bmc_ok:
        err = f"BMC {bmc_ip} unreachable after 3 attempts with both passwords"
        _log(f"FAILED: {err}")
        return {"success": False, "active_bmc_password": active_password, "steps": steps, "error": err}

    # Step 2: Power off
    _log(f"powering off (BMC={bmc_ip})")
    try:
        _ipmitool(bmc_ip, bmc_user, active_password, "chassis", "power", "off")
    except Exception as e:
        _log(f"WARNING: power off command failed: {e}")
    _time.sleep(5)
    try:
        _, off_status = _ipmitool(bmc_ip, bmc_user, active_password, "chassis", "power", "status")
        if "off" in off_status.lower():
            _log("power confirmed off")
        else:
            _log(f"WARNING: power status after off: {off_status.strip()}")
    except Exception:
        pass

    # Step 3: Set always-on policy + power on
    try:
        _ipmitool(bmc_ip, bmc_user, active_password, "chassis", "policy", "always-on")
    except Exception as e:
        _log(f"WARNING: set always-on policy failed: {e}")
    try:
        _ipmitool(bmc_ip, bmc_user, active_password, "chassis", "power", "on")
    except Exception as e:
        _log(f"WARNING: power on command failed: {e}")

    # Step 4: Poll for power on, with optional cold reset retry
    max_attempts = poll_timeout // poll_interval
    power_on_ok = False
    last_status = "unknown"

    for attempt_idx in range(2 if cold_reset_retry else 1):
        if attempt_idx == 1:
            # Cold reset to clear latched fault (e.g. VRM sensor spikes on Supermicro)
            _log("power still off after first attempt, trying BMC cold reset")
            try:
                _ipmitool(bmc_ip, bmc_user, active_password, "mc", "reset", "cold")
            except Exception as reset_err:
                _log(f"WARNING: BMC cold reset failed: {reset_err}")
            _time.sleep(60)  # wait for BMC to come back
            try:
                _ipmitool(bmc_ip, bmc_user, active_password, "chassis", "policy", "always-on")
                _ipmitool(bmc_ip, bmc_user, active_password, "chassis", "power", "on")
            except Exception as e:
                _log(f"WARNING: power on after cold reset failed: {e}")

        for i in range(max_attempts):
            _time.sleep(poll_interval)
            try:
                _, status = _ipmitool(bmc_ip, bmc_user, active_password, "chassis", "power", "status")
                last_status = status.strip()
            except Exception:
                last_status = "BMC unreachable"
            if "on" in last_status.lower():
                power_on_ok = True
                elapsed = (i + 1) * poll_interval
                _log(f"power on confirmed after {elapsed}s")
                break
            if (i + 1) % 4 == 0:
                elapsed = (i + 1) * poll_interval
                logger.info("Power on still waiting for %s: %s (%ds)", hostname, last_status, elapsed)
        if power_on_ok:
            break

    if not power_on_ok:
        err = f"power on failed after {2 if cold_reset_retry else 1} attempt(s): {last_status}"
        _log(f"FAILED: {err}")
        return {"success": False, "active_bmc_password": active_password, "steps": steps, "error": err}

    _log("power cycle completed successfully")
    return {"success": True, "active_bmc_password": active_password, "steps": steps, "error": None}


def wait_for_boot(
    hostname: str,
    ip: str,
    ssh_user: str = "operator",
    ssh_password: str = "",
    reset_ssh_user: str = "ubuntu",
    reset_ssh_password: str = "",
    poll_interval: int = 30,
    poll_timeout: int = 1800,
    bmc_ip: str = "",
    bmc_user: str = "admin",
    bmc_password: str = "",
    category: str = "h200",
) -> dict:
    """Wait for a node to boot by polling SSH reachability.

    After a BMC power cycle or reboot, poll SSH until the node is reachable.
    On failure, captures a BMC KVM screenshot for diagnosis.

    Args:
        hostname: Node hostname (for logging).
        ip: Node IP address.
        ssh_user: Primary SSH user.
        ssh_password: Primary SSH password.
        reset_ssh_user: Fallback SSH user (after reset).
        reset_ssh_password: Fallback SSH password.
        poll_interval: Seconds between SSH probes (default 30s).
        poll_timeout: Max seconds to wait (default 1800 = 30min).
        bmc_ip: Optional BMC IP for screenshot on failure.
        bmc_user: BMC username.
        bmc_password: BMC password.
        category: Node category for screenshot path.

    Returns:
        dict with keys:
          success: bool
          reachable_user: str | None — the SSH user that worked
          boot_time_seconds: int | None
          steps: list[str] — progress log
          error: str | None
          screenshot_path: str | None — if boot failed and screenshot captured
    """
    import time as _time
    import tempfile
    from node_operations.ssh import probe_ssh

    steps = []
    reachable_user = None
    boot_time = None
    screenshot_path = None

    def _log(msg: str):
        steps.append(msg)
        logger.info("wait_for_boot %s: %s", hostname, msg)

    _log(f"polling SSH reachability at {ip} (timeout={poll_timeout}s)")

    # Quick initial check — node might already be up
    if probe_ssh(ip, ssh_user, timeout=15, password=ssh_password):
        reachable_user = ssh_user
        boot_time = 0
        _log(f"already up as {ssh_user}")
    elif probe_ssh(ip, reset_ssh_user, timeout=15, password=reset_ssh_password):
        reachable_user = reset_ssh_user
        boot_time = 0
        _log(f"already up as {reset_ssh_user}")

    max_attempts = poll_timeout // poll_interval
    for i in range(max_attempts):
        _time.sleep(poll_interval)
        if probe_ssh(ip, ssh_user, timeout=15, password=ssh_password):
            reachable_user = ssh_user
            boot_time = (i + 1) * poll_interval
            _log(f"up as {ssh_user} after {boot_time}s")
            break
        if probe_ssh(ip, reset_ssh_user, timeout=15, password=reset_ssh_password):
            reachable_user = reset_ssh_user
            boot_time = (i + 1) * poll_interval
            _log(f"up as {reset_ssh_user} after {boot_time}s")
            break
        if (i + 1) % 4 == 0:
            logger.info("Boot still waiting for %s (%ds)", hostname, (i + 1) * poll_interval)

    if not reachable_user:
        # Boot failed — try BMC screenshot for diagnosis
        _log(f"FAILED: unreachable after {poll_timeout}s")
        if bmc_ip and bmc_user and bmc_password:
            try:
                ss_dir = tempfile.mkdtemp(prefix="bmc_ss_")
                screenshot_path = get_kvm_screenshot(bmc_ip, bmc_user, bmc_password,
                                                     category=category, base_dir=ss_dir)
                _log(f"BMC screenshot saved to {screenshot_path}")
            except Exception as ss_err:
                _log(f"BMC screenshot failed: {str(ss_err)[:200]}")
        return {
            "success": False,
            "reachable_user": None,
            "boot_time_seconds": None,
            "steps": steps,
            "error": f"Node {hostname} unreachable after {poll_timeout}s — no SSH to {ip}",
            "screenshot_path": screenshot_path,
        }

    return {
        "success": True,
        "reachable_user": reachable_user,
        "boot_time_seconds": boot_time,
        "steps": steps,
        "error": None,
        "screenshot_path": None,
    }
