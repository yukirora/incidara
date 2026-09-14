import os
import re
import subprocess
import time
import logging
from node_operations.init_stage import run_init_stage
from node_operations.ssh import probe_ssh, run_remote_command, run_remote_command_capture
from node_operations.sysinfo import collect_sbsysinfo, read_sku, sync_node_time

logger = logging.getLogger(__name__)

try:
    from kubernetes import client as k8s_client, config as k8s_config
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
    _k8s_v1 = k8s_client.CoreV1Api()
except Exception:
    _k8s_v1 = None

# Full pipeline definition per category.
# Each entry is a list of (bundle, script) stages executed in order.
# Auto-triggered steps (sync_node_time, check_sku, reboot_and_wait) are defined
# separately in _STAGE_TRIGGERS and fire only if the triggering stage is in the list.
# k8s_device_pods: whether to restart nvidia-device-plugin after k8s scale-up.
_CATEGORY_PIPELINES = {
    "h200": {
        "stages": [
            ("init_bundle", "create_user.sh"),
            ("config_bundle", "config_apt.sh"),
            ("software_bundle", "install_general.sh"),
            ("init_bundle", "harden_node.sh"),
            ("config_bundle", "config_raid.sh"),
            ("software_bundle", "install_h200.sh"),
            ("config_bundle", "config_h200.sh"),
        ],
        "k8s_device_pods": True,
    },
    "b300": {
        "stages": [
            ("init_bundle", "create_user.sh"),
            ("config_bundle", "config_apt.sh"),
            ("software_bundle", "install_general.sh"),
            ("init_bundle", "harden_node.sh"),
            ("config_bundle", "config_raid.sh"),
            ("software_bundle", "install_b300.sh"),
            ("config_bundle", "config_b300.sh"),
        ],
        "k8s_device_pods": True,
    },
    "cpu": {
        "stages": [
            ("init_bundle", "create_user.sh"),
            ("config_bundle", "config_apt.sh"),
            ("software_bundle", "install_general.sh"),
            ("init_bundle", "harden_node.sh"),
            ("config_bundle", "config_raid.sh"),
            ("software_bundle", "install_cpu.sh"),
            ("config_bundle", "config_cpu.sh"),
        ],
        "k8s_device_pods": False,
    },
    "ctrl": {
        "stages": [
            ("init_bundle", "create_user.sh"),
            ("config_bundle", "config_apt.sh"),
            ("software_bundle", "install_general.sh"),
            ("init_bundle", "harden_node.sh"),
            ("config_bundle", "config_raid.sh"),
            ("software_bundle", "install_cpu.sh"),
            ("config_bundle", "config_cpu.sh"),
        ],
        "k8s_device_pods": False,
    },
    "storage": {
        "stages": [
            ("init_bundle", "create_user.sh"),
            ("config_bundle", "config_apt.sh"),
            ("software_bundle", "install_general.sh"),
            ("init_bundle", "harden_node.sh"),
            ("config_bundle", "config_img.sh"),
            ("software_bundle", "install_storage.sh"),
            ("config_bundle", "config_storage.sh"),
        ],
        "k8s_device_pods": False,
    },
}

# Backward compat: derive old _CATEGORY_STAGES from new _CATEGORY_PIPELINES
_CATEGORY_STAGES = {}
for _cat, _pipe in _CATEGORY_PIPELINES.items():
    _stages = _pipe["stages"]
    # Last two stages are (install_bundle, install_script, config_bundle, config_script)
    _CATEGORY_STAGES[_cat] = (_stages[-2][0], _stages[-2][1], _stages[-1][0], _stages[-1][1])

# Trigger rules: after a stage completes, run these auto-steps.
# Keys are (bundle, script) tuples. Values are lists of (step_name, standalone_tool).
# standalone_tool is what the agent calls when stepping manually.
# Triggers only fire if the stage is actually in the category's pipeline.
_STAGE_TRIGGERS = {
    ("init_bundle", "create_user.sh"): [
        ("sync_node_time", "run_ssh_command"),
    ],
    ("config_bundle", "config_apt.sh"): [
        ("collect_sbsysinfo", "run_config_stage(bundle='ltp_bundle', script='collect_system_info_{category}.sh')"),
        ("check_sku", "check_sku"),
    ],
    ("config_bundle", "config_raid.sh"): [
        ("reboot_and_wait", "bmc_power_cycle + wait_for_boot"),
        ("check_raid.sh", "run_config_stage(bundle='config_bundle', script='check_raid.sh')"),
    ],
}


def get_category_stage_pairs(category: str):
    if category not in _CATEGORY_STAGES:
        raise RuntimeError(f"unsupported category: {category!r}")
    ib, is_, cb, cs = _CATEGORY_STAGES[category]
    return (ib, is_), (cb, cs)


def _ipmitool(bmc_ip: str, bmc_user: str, bmc_pass: str, *args: str, **_kw) -> tuple[int, str]:
    """Run ipmitool against BMC directly. Tries admin+root users and both passwords."""
    import os as _os
    # Build list of (user, password) combos to try
    users = list(dict.fromkeys([bmc_user, "admin", "root"]))
    reset_pw = _os.environ.get("RESET_BMC_PASSWORD", "")
    passwords = list(dict.fromkeys([bmc_pass, reset_pw]))
    for pw in passwords:
        if not pw:
            continue
        for user in users:
            cmd = ["ipmitool", "-I", "lanplus", "-H", bmc_ip, "-U", user, "-P", pw, *args]
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               check=False, timeout=60)
            if r.returncode == 0 or ("Unable to establish" not in r.stdout and "Error: " not in r.stdout):
                return r.returncode, r.stdout
    # All combos failed, return last result
    return r.returncode, r.stdout


def _bmc_boot_completed(bmc_ip: str, bmc_user: str, bmc_pass: str,
                        reboot_sel_count: int) -> bool | None:
    """Check BMC SEL for 'boot completed' event after the reboot.
    Returns True if boot completed, False if still booting, None if BMC unreachable."""
    rc, out = _ipmitool(bmc_ip, bmc_user, bmc_pass, "sel", "list", "last", "10")
    if rc != 0:
        return None
    # Look for boot completed in recent SEL entries
    for line in out.splitlines():
        if "boot completed" in line.lower():
            # Check if this is a new event (SEL ID > reboot_sel_count)
            m = re.match(r"\s*([0-9a-fA-F]+)\s*\|", line)
            if m:
                try:
                    sel_id = int(m.group(1), 16)
                    if sel_id > reboot_sel_count:
                        return True
                except ValueError:
                    pass
    return False


def _bmc_power_status(bmc_ip: str, bmc_user: str, bmc_pass: str) -> str | None:
    rc, out = _ipmitool(bmc_ip, bmc_user, bmc_pass, "power", "status")
    if rc == 0 and "on" in out.lower():
        return "on"
    if rc == 0 and "off" in out.lower():
        return "off"
    return None


def _get_last_sel_id(bmc_ip: str, bmc_user: str, bmc_pass: str) -> int:
    """Get the last SEL entry ID so we can detect new events after reboot."""
    rc, out = _ipmitool(bmc_ip, bmc_user, bmc_pass, "sel", "list", "last", "1")
    if rc == 0:
        m = re.match(r"\s*([0-9a-fA-F]+)\s*\|", out)
        if m:
            try:
                return int(m.group(1), 16)
            except ValueError:
                pass
    return 0


def _try_capture_kvm(bmc_ip: str, bmc_user: str, bmc_pass: str, hostname: str) -> None:
    """Best-effort KVM screenshot capture for diagnostics."""
    try:
        from node_operations.bmc import get_kvm_screenshot
        path = get_kvm_screenshot(bmc_ip, bmc_user, bmc_pass,
                                  f"/tmp/kvm_{hostname}.jpg")
        logger.error("KVM screenshot saved to %s for diagnostics", path)
    except Exception as e:
        logger.warning("Failed to capture KVM screenshot: %s", e)


def _k8s_delete_pods_on_node(hostname: str) -> None:
    """Delete all pods on a node (force drain)."""
    if not _k8s_v1:
        logger.warning("k8s client not available, skipping pod deletion on %s", hostname)
        return
    logger.info("Deleting all pods on node %s", hostname)
    pods = _k8s_v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={hostname}")
    for pod in pods.items:
        try:
            _k8s_v1.delete_namespaced_pod(
                name=pod.metadata.name, namespace=pod.metadata.namespace,
                grace_period_seconds=0,
                body=k8s_client.V1DeleteOptions(grace_period_seconds=0),
            )
            logger.info("Deleted pod %s/%s", pod.metadata.namespace, pod.metadata.name)
        except Exception as e:
            logger.warning("Failed to delete pod %s/%s: %s", pod.metadata.namespace, pod.metadata.name, e)


def _k8s_force_delete_pods(hostname: str) -> None:
    """Force-delete all pods on a node via kubectl on the master.

    Equivalent to the original cluster-init's delete_k8s_pod.sh.
    Uses --force --grace-period=0 to bypass kubelet (which may be dead).
    """
    k8s_master_ip = os.environ.get("K8S_MASTER_IP", "")
    k8s_master_user = os.environ.get("K8S_MASTER_USER", "operator")

    if not k8s_master_ip:
        logger.warning("K8S_MASTER_IP not set, skipping pod deletion for %s", hostname)
        return

    from node_operations.ssh import run_remote_command_capture
    rc, out = run_remote_command_capture(
        k8s_master_ip, k8s_master_user,
        f"sudo kubectl get pods -A --field-selector spec.nodeName={hostname} -o json"
        f" | jq -r '.items[] | .metadata.namespace + \"/\" + .metadata.name'"
        f" | while read ns_pod; do sudo kubectl delete pod $ns_pod --force --grace-period=0; done",
        timeout=120,
    )
    if rc == 0:
        logger.info("Force-deleted pods on node %s via kubectl", hostname)
    else:
        logger.warning("Failed to delete pods on %s: %s", hostname, out)


def _k8s_scale_node(hostname: str, **_kw) -> None:
    """Scale node into k8s cluster via kubespray-service.

    Calls kubespray-service with just the hostname. The service itself
    knows the cluster layout and config (mounted as volumes).
    Skips if the node is already Ready.

    If kubespray-service is not directly reachable (agent runs outside
    k8s cluster), proxies the request via SSH to the k8s master.
    """
    import json
    import urllib.request

    # Check if node is already Ready — skip scale if so
    if _k8s_v1:
        try:
            node = _k8s_v1.read_node(hostname)
            for cond in node.status.conditions or []:
                if cond.type == "Ready" and cond.status == "True":
                    logger.info("%s already Ready, skipping kubespray scale", hostname)
                    return
        except Exception:
            pass

    kubespray_url = os.environ.get("KUBESPRAY_SERVICE_URL", "http://kubespray-service:5000")
    k8s_master_ip = os.environ.get("K8S_MASTER_IP", "")
    k8s_master_user = os.environ.get("K8S_MASTER_USER", "operator")

    logger.info("Calling kubespray-service scale-up for %s", hostname)

    def _call_kubespray_api(path: str, method: str = "GET", data: bytes | None = None) -> dict:
        """Call kubespray-service API, with SSH proxy fallback."""
        # Try direct call first
        try:
            req = urllib.request.Request(
                f"{kubespray_url}{path}",
                data=data,
                headers={"Content-Type": "application/json"},
                method=method,
            )
            resp = urllib.request.urlopen(req, timeout=30)
            return json.loads(resp.read())
        except Exception as direct_err:
            if not k8s_master_ip:
                raise RuntimeError(
                    f"Cannot reach kubespray-service at {kubespray_url} and K8S_MASTER_IP not set"
                ) from direct_err
            logger.info("Direct call failed (%s), proxying via %s", direct_err, k8s_master_ip)
            from node_operations.ssh import run_remote_command_capture
            cmd = f"curl -s -X {method} '{kubespray_url}{path}'"
            if data:
                # Escape single quotes in JSON payload for shell
                payload = data.decode().replace("'", "'\\''")
                cmd += f" -H 'Content-Type: application/json' -d '{payload}'"
            rc, out = run_remote_command_capture(k8s_master_ip, k8s_master_user, cmd, timeout=30)
            if rc != 0:
                raise RuntimeError(f"kubespray API call via SSH proxy failed: {out}")
            # SSH proxy may prepend warnings like "Warning: Permanently added..."
            # Strip non-JSON lines before parsing
            lines = out.strip().splitlines()
            json_line = lines[-1] if lines else out  # JSON is always the last line
            try:
                return json.loads(json_line)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"kubespray API unexpected response: {out}") from e

    # Submit scale-up job
    payload = json.dumps({"hostnames": [hostname]}).encode()
    result = _call_kubespray_api("/api/v1/scale-up", method="POST", data=payload)
    job_id = result["job_id"]
    logger.info("kubespray scale-up job %s submitted for %s", job_id, hostname)

    # Phase 1: Wait for job to leave "queued" state (another job may be running — no timeout)
    queue_polls = 0
    while True:
        time.sleep(10)
        try:
            status = _call_kubespray_api(f"/api/v1/jobs/{job_id}")
        except Exception as e:
            logger.warning("Failed to poll kubespray job %s: %s", job_id, e)
            continue
        if status["status"] == "failed":
            error = status.get("error", "unknown")
            log_tail = status.get("log_tail", "")
            # Include last few lines of log_tail for actionable diagnostics
            if log_tail:
                tail_lines = log_tail.strip().splitlines()[-5:]
                error += "\n" + "\n".join(tail_lines)
            raise RuntimeError(f"kubespray scale-up failed for {hostname}: {error}")
        if status["status"] != "queued":
            logger.info("kubespray scale-up %s: %s", hostname, status["status"])
            break
        queue_polls += 1
        if queue_polls % 6 == 0:
            logger.info("kubespray scale-up %s: still queued (waited %ds)", hostname, queue_polls * 10)

    # Phase 2: Job is running — poll until succeeded/failed (kubespray takes 10-30 min)
    if status["status"] == "running":
        for i in range(120):  # 120 * 15s = 30 min max for running job
            time.sleep(15)
            try:
                status = _call_kubespray_api(f"/api/v1/jobs/{job_id}")
            except RuntimeError:
                raise
            except Exception as e:
                logger.warning("Failed to poll kubespray job %s: %s", job_id, e)
                continue
            if status["status"] == "succeeded":
                logger.info("kubespray scale-up succeeded for %s", hostname)
                return
            if status["status"] == "failed":
                error = status.get("error", "unknown")
                log_tail = status.get("log_tail", "")
                if log_tail:
                    tail_lines = log_tail.strip().splitlines()[-5:]
                    error += "\n" + "\n".join(tail_lines)
                raise RuntimeError(f"kubespray scale-up failed for {hostname}: {error}")
            if (i + 1) % 4 == 0:
                logger.info("kubespray scale-up %s: %s (running poll %d)", hostname, status["status"], i + 1)
        raise RuntimeError(f"kubespray scale-up timed out for {hostname} (30 min running limit)")

    # If status is something unexpected (succeeded already caught above)
    if status["status"] == "succeeded":
        logger.info("kubespray scale-up succeeded for %s", hostname)
        return


def _k8s_restart_device_pods(hostname: str) -> None:
    """Restart containerd and nvidia-device-plugin/job-exporter pods on a node.
    
    Containerd must be restarted after nvidia-container-runtime is configured
    so the nvidia runtime becomes available. Then the device-plugin pod is
    recreated to detect GPUs."""
    from node_operations.ssh import run_remote_command_capture, run_kubectl_via_ssh
    ssh_user_val = os.environ.get("SSH_USER", "operator")
    ssh_password_val = os.environ.get("SSH_PASSWORD", "")
    k8s_master_ip = os.environ.get("K8S_MASTER_IP", "")
    k8s_master_user = os.environ.get("K8S_MASTER_USER", "operator")

    # Restart containerd via SSH so nvidia runtime takes effect
    try:
        node = None
        if _k8s_v1:
            node = _k8s_v1.read_node(hostname)
            for addr in (node.status.addresses or []):
                if addr.type == "InternalIP":
                    ip = addr.address
                    break
            else:
                ip = None
        elif k8s_master_ip:
            # Fallback: get node IP via kubectl on master
            result = run_kubectl_via_ssh(k8s_master_ip, k8s_master_user,
                                          f"get node {hostname} -o jsonpath={{.status.addresses[?(@.type=='InternalIP')].address}}",
                                          timeout=30)
            ip = result.get("stdout", "").strip() or None
        else:
            ip = None

        if ip:
            rc, out = run_remote_command_capture(
                ip, ssh_user_val, "sudo systemctl restart containerd", timeout=60,
                password=ssh_password_val)
            if rc == 0:
                logger.info("Restarted containerd on %s", hostname)
                time.sleep(5)
            else:
                logger.warning("Failed to restart containerd on %s: %s", hostname, out)
    except Exception as e:
        logger.warning("Failed to restart containerd on %s: %s", hostname, e)
    
    # Delete device pods so they respawn and re-detect GPUs
    # Always delete after reallocation — old pods may be stale (weeks old).
    # Then check if the new pod is healthy; retry if CrashLoopBackOff.
    if _k8s_v1:
        for pattern in ("nvidia-device-plugin", "job-exporter"):
            for _retry in range(3):
                pods = _k8s_v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={hostname}")
                target = None
                for pod in pods.items:
                    if pattern in pod.metadata.name:
                        target = pod
                        break
                if not target:
                    logger.info("No %s pod on %s yet, waiting", pattern, hostname)
                    time.sleep(10)
                    continue
                # First attempt: always delete. Later attempts: only delete if CrashLoopBackOff.
                if _retry > 0:
                    cs = (pod.status.container_statuses or []) if pod.status else []
                    crash = any(c.state.waiting and c.state.waiting.reason == "CrashLoopBackOff"
                                for c in cs if c.state and c.state.waiting)
                    if not crash:
                        logger.info("Pod %s on %s is healthy after restart", pattern, hostname)
                        break
                try:
                    _k8s_v1.delete_namespaced_pod(
                        name=target.metadata.name, namespace=target.metadata.namespace,
                    )
                    logger.info("Deleted pod %s/%s on %s (retry %d/3)", target.metadata.namespace, target.metadata.name, hostname, _retry + 1)
                except Exception as e:
                    logger.warning("Failed to delete pod: %s", e)
                    break
                time.sleep(60)
    elif k8s_master_ip:
        try:
            for pattern in ("nvidia-device-plugin", "job-exporter"):
                for _retry in range(3):
                    list_result = run_kubectl_via_ssh(
                        k8s_master_ip, k8s_master_user,
                        f"get pods -A --field-selector spec.nodeName={hostname} -o json",
                        timeout=30)
                    import json as _json
                    pod_data = _json.loads(list_result.get("stdout", "{}"))
                    target = None
                    for pod in pod_data.get("items", []):
                        pod_name = pod.get("metadata", {}).get("name", "")
                        if pattern in pod_name:
                            target = (pod_name, pod.get("metadata", {}).get("namespace", ""))
                            break
                    if not target:
                        logger.info("No %s pod on %s yet, waiting", pattern, hostname)
                        time.sleep(10)
                        continue
                    # First attempt: always delete. Later attempts: only delete if CrashLoopBackOff.
                    if _retry > 0:
                        cs = pod.get("status", {}).get("containerStatuses", [])
                        crash = any(c.get("state", {}).get("waiting", {}).get("reason") == "CrashLoopBackOff" for c in cs)
                        if not crash:
                            logger.info("Pod %s on %s is healthy after restart", pattern, hostname)
                            break
                    pod_name, pod_ns = target
                    try:
                        run_kubectl_via_ssh(k8s_master_ip, k8s_master_user,
                                            f"delete pod {pod_name} -n {pod_ns} --grace-period=0",
                                            timeout=30)
                        logger.info("Deleted pod %s/%s on %s via kubectl (retry %d/3)", pod_ns, pod_name, hostname, _retry + 1)
                    except Exception as e:
                        logger.warning("Failed to delete pod %s/%s: %s", pod_ns, pod_name, e)
                        break
                    time.sleep(60)
        except Exception as e:
            logger.warning("Failed to list/delete device pods on %s via kubectl: %s", hostname, e)
    else:
        logger.warning("k8s client not available and K8S_MASTER_IP not set, skipping device pod restart on %s", hostname)


def reboot_and_wait(*, hostname: str, ip: str, category: str,
                    ssh_user: str, timeout: int,
                    bmc_ip: str = "", bmc_user: str = "admin",
                    bmc_pass: str = "",
                    k8s_master_user: str = "", k8s_master_ip: str = "") -> None:
    if bmc_ip:
        logger.info("BMC IP for %s: %s", hostname, bmc_ip)

    # Record last SEL ID before reboot so we can detect new boot events
    last_sel_id = 0
    if bmc_ip and bmc_user and bmc_pass:
        last_sel_id = _get_last_sel_id(bmc_ip, bmc_user, bmc_pass)

    reboot_cmd = """sudo -n sh -c 'nohup sh -c "sleep 2; /usr/bin/systemctl reboot" >/dev/null 2>&1 &'"""
    # Prefer BMC power cycle for reliability (doesn't depend on OS being healthy).
    # h200: also needs bootdev disk set (Supermicro may not boot from disk after cycle).
    # b300: AMI MegaRAC handles boot order automatically, just needs power cycle.
    if bmc_ip and bmc_user and bmc_pass:
        # Set always-on policy before power cycle so node comes back up
        _ipmitool(bmc_ip, bmc_user, bmc_pass, "chassis", "policy", "always-on")
        if category == "h200":
            rc, out = run_remote_command_capture(ip, ssh_user, "sudo ipmitool chassis bootparam get 5", timeout)
            if rc != 0:
                raise RuntimeError("failed to check bootparam")
            if "Boot Device Selector : Force Boot from default Hard-Drive" not in out:
                if run_remote_command(ip, ssh_user,
                                      "sudo ipmitool chassis bootdev disk options=persistent,efiboot", timeout) != 0:
                    raise RuntimeError("failed to set bootdev disk")
        reboot_cmd = "sudo ipmitool chassis power cycle"
    if run_remote_command(ip, ssh_user, reboot_cmd, timeout) != 0:
        raise RuntimeError("reboot failed")

    time.sleep(5)

    # Phase 1: Wait for node to go down
    for i in range(20):
        if not probe_ssh(ip, ssh_user, timeout=10):
            logger.info("%s SSH is down (attempt %d), node is rebooting", hostname, i + 1)
            break
        time.sleep(60)
    else:
        raise RuntimeError("node did not go down")

    # Phase 2: Wait for BMC to report boot completed (if BMC accessible)
    # Some BMCs (e.g. Supermicro) don't log "boot completed" in SEL.
    # If BMC SEL never reports boot completed, fall through to Phase 3 (SSH check).
    boot_confirmed = False
    if bmc_ip and bmc_user and bmc_pass:
        logger.info("Waiting for BMC boot completion on %s (BMC %s)...", hostname, bmc_ip)
        for i in range(60):  # 60 * 30s = 30 min max
            boot_done = _bmc_boot_completed(bmc_ip, bmc_user, bmc_pass, last_sel_id)
            if boot_done is True:
                logger.info("%s BMC reports boot completed (attempt %d)", hostname, i + 1)
                boot_confirmed = True
                break
            # Also check SSH — if node is up, boot is done regardless of SEL
            if probe_ssh(ip, ssh_user, timeout=10):
                logger.info("%s SSH reachable before BMC boot event (attempt %d) — boot likely done", hostname, i + 1)
                boot_confirmed = True
                break
            # Check power status — if power is OFF, BMC has a latched fault.
            # Don't keep polling 30 min for a node that's not even powered on.
            power = _bmc_power_status(bmc_ip, bmc_user, bmc_pass)
            if power and "off" in power.lower() and i >= 3:
                logger.warning("%s power is OFF after power cycle (attempt %d) — BMC latched fault?", hostname, i + 1)
                # Use shared BMC power cycle with cold reset to clear latched fault
                from node_operations.bmc import bmc_power_cycle as _bmc_power_cycle_fn
                logger.warning("%s attempting BMC cold reset via bmc_power_cycle", hostname)
                _pc = _bmc_power_cycle_fn(
                    hostname=hostname, bmc_ip=bmc_ip, bmc_user=bmc_user,
                    bmc_password=bmc_pass,
                    poll_interval=15, poll_timeout=900, cold_reset_retry=True,
                )
                if _pc["success"]:
                    logger.info("%s BMC power cycle recovered node", hostname)
                else:
                    logger.warning("%s BMC power cycle failed: %s", hostname, _pc["error"])
                # Continue polling — give it a chance to come up after reset
                continue
            logger.info("%s still booting (power=%s, attempt %d)", hostname, power, i + 1)
            time.sleep(30)
        if not boot_confirmed:
            # BMC didn't report boot completed, but don't fail — fall through to SSH check
            logger.warning("%s BMC did not report boot completed after 30 min — falling back to SSH check", hostname)
            _try_capture_kvm(bmc_ip, bmc_user, bmc_pass, hostname)

    # Phase 3: Wait for SSH to come back
    logger.info("Waiting for SSH on %s...", hostname)
    for i in range(60):  # 60 * 30s = 30 min max
        if probe_ssh(ip, ssh_user, timeout=10):
            logger.info("%s SSH is back (attempt %d)", hostname, i + 1)
            return
        # Check if power is still off (cold reset from Phase 2 may need time)
        if bmc_ip and bmc_user and bmc_pass and i % 10 == 9:
            power = _bmc_power_status(bmc_ip, bmc_user, bmc_pass)
            if power and "off" in power.lower():
                logger.warning("%s power still OFF in Phase 3 (attempt %d) — trying power on", hostname, i + 1)
                try:
                    _ipmitool(bmc_ip, bmc_user, bmc_pass, "chassis", "power", "on")
                except Exception:
                    pass
        time.sleep(30)

    # SSH didn't come back — capture KVM screenshot for diagnostics
    if bmc_ip and bmc_user and bmc_pass:
        _try_capture_kvm(bmc_ip, bmc_user, bmc_pass, hostname)
        power = _bmc_power_status(bmc_ip, bmc_user, bmc_pass)
        raise RuntimeError(
            f"{hostname} booted (BMC confirmed) but SSH not reachable after 15 min "
            f"(power={power}, BMC {bmc_ip}) — possible network config issue"
        )
    raise RuntimeError(f"{hostname} did not come back after reboot (no BMC available)")


def run_full_config(*, hostname: str, ip: str, category: str,
                    ssh_user: str, ssh_password: str,
                    reset_ssh_user: str, reset_ssh_password: str,
                    bmc_password: str, timeout: int,
                    k8s_master_user: str, k8s_master_ip: str,
                    base_dir: str = ".",
                    bmc_ip: str = "", bmc_user: str = "admin",
                    bmc_pass: str = "",
                    available_user: str = "", available_password: str = "") -> None:
    """Run full config pipeline. available_user/password is the user that currently
    has SSH access (operator or ubuntu). After create_user.sh creates operator,
    subsequent stages use ssh_user (operator). If available_user not set, defaults to ssh_user."""
    import time as _time

    if category not in _CATEGORY_PIPELINES:
        raise RuntimeError(f"unsupported category: {category!r}")

    pipeline = _CATEGORY_PIPELINES[category]

    progress_log = []

    def _step(step_name: str, status: str, detail: str = ""):
        entry = f"config/{step_name}: {status}" + (f" — {detail}" if detail else "")
        progress_log.append(entry)
        logger.info("config %s: %s", hostname, entry)

    common = dict(hostname=hostname, ip=ip,
                  bmc_password=bmc_password, timeout=timeout)
    first_user = available_user or ssh_user
    first_pw = available_password or ssh_password

    # Build stages list from the pipeline definition
    # First stage uses available_user, rest use ssh_user
    stages = []
    for i, (bundle, script) in enumerate(pipeline["stages"]):
        if i == 0:
            stages.append((bundle, script, first_user, first_pw))
        else:
            stages.append((bundle, script, ssh_user, ssh_password))


    for i, (bundle, script, user, pw) in enumerate(stages, 1):
        _step(f"stage_{i}_{script}", "in_progress", f"bundle={bundle}, user={user}")
        try:
            run_init_stage(**common, ssh_user=user, ssh_password=pw, bundle=bundle, script=script)
            _step(f"stage_{i}_{script}", "ok")
        except Exception as e:
            _step(f"stage_{i}_{script}", "failed", str(e)[:200])
            raise

        # Fire auto-triggered steps for this stage (if any)
        triggers = _STAGE_TRIGGERS.get((bundle, script), [])
        for auto_name, _auto_tool in triggers:
            if auto_name == "sync_node_time":
                _step("sync_time", "in_progress", "syncing node time")
                try:
                    sync_node_time(ip, ssh_user)
                    _step("sync_time", "ok")
                except Exception as e:
                    _step("sync_time", "failed", str(e)[:200])
                    raise

            elif auto_name == "collect_sbsysinfo":
                _step("sysinfo", "in_progress", "collecting SKU info")
                collect_sbsysinfo(hostname=hostname, ip=ip, category=category,
                                  ssh_user=ssh_user, ssh_password=ssh_password,
                                  bmc_password=bmc_password, timeout=timeout,
                                  base_dir=base_dir)

            elif auto_name == "check_sku":
                _step("check_sku", "in_progress", "validating SKU")
                try:
                    read_sku(ip, base_dir=base_dir)
                    _step("check_sku", "ok", "SKU verified")
                except RuntimeError as e:
                    _step("check_sku", "failed", f"SKU mismatch: {e}")
                    raise RuntimeError(f"SKU mismatch: {e}") from e

            elif auto_name == "reboot_and_wait":
                _step("reboot", "in_progress", "rebooting after RAID config")
                reboot_and_wait(hostname=hostname, ip=ip, category=category,
                                ssh_user=ssh_user, timeout=timeout,
                                bmc_ip=bmc_ip, bmc_user=bmc_user, bmc_pass=bmc_pass,
                                k8s_master_user=k8s_master_user, k8s_master_ip=k8s_master_ip)
                _step("reboot", "ok", "node back up")

            elif auto_name == "check_raid.sh":
                _step("check_raid", "in_progress")
                run_init_stage(**common, ssh_user=ssh_user, ssh_password=ssh_password,
                               bundle="config_bundle", script="check_raid.sh")
                _step("check_raid", "ok")

    # K8s maintenance — follow original cluster-init pipeline order:
    # 1. Force-delete pods (--force --grace-period=0) so kubespray drain finds nothing to evict
    # 2. Scale-up via kubespray (installs kubelet, joins cluster)
    for fn, label in [(_k8s_force_delete_pods, "delete_pods"),
                       (_k8s_scale_node, "scale-up")]:
        try:
            _step(f"k8s_{label}", "in_progress")
            fn(hostname)
            _step(f"k8s_{label}", "ok")
        except Exception as e:
            err_msg = str(e)[:200]
            # k8s scale-up failure is FATAL — without kubelet the node cannot join the cluster
            # delete_pods is non-fatal (kubespray scale-up may handle it)
            if label == "scale-up":
                _step(f"k8s_{label}", "failed", err_msg)
                raise RuntimeError(f"k8s scale-up failed for {hostname}: {err_msg}") from e
            _step(f"k8s_{label}", "warning", err_msg)
            logger.warning("k8s %s failed for %s (non-fatal): %s", label, hostname, e)
    if pipeline.get("k8s_device_pods"):
        try:
            _step("k8s_restart_device_pods", "in_progress")
            _k8s_restart_device_pods(hostname)
            _step("k8s_restart_device_pods", "ok")
        except Exception as e:
            _step("k8s_restart_device_pods", "warning", str(e)[:200])
            logger.warning("k8s restart device pods failed for %s (non-fatal): %s", hostname, e)
    _step("all_stages", "ok", "full config pipeline completed")
    return progress_log
