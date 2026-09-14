"""Built-in patrol rule source strings used for tests and deployments."""

B300_NVLINK_FM_V1 = r'''
def analyze(collected, state):
    observations = []
    new_state = dict(state)

    for target in collected.targets:
        hostname = target.id
        payload = target.payload
        host_state = dict(new_state.get(hostname, {}))

        lifecycle = {
            "kind": "condition",
            "open_after_consecutive": 3,
            "close_after_healthy": 2,
            "unknown_keeps_active": True,
        }

        if not payload.get("ssh_ok"):
            observations.append(Observation(
                signal_key="b300_nvlink_fm_bad",
                target_id=hostname,
                status="unknown",
                severity="critical",
                action="cordon_node",
                evidence={"reason": "ssh_unreachable_or_unknown"},
                confidence=0.0,
                lifecycle=lifecycle,
            ))
            host_state["prev_issues"] = host_state.get("prev_issues", [])
            new_state[hostname] = host_state
            continue

        outputs = payload.get("outputs", {})
        fm_output = outputs.get("fm_status", "")
        nvlink_output = outputs.get("nvlink_status", "")

        issues = []

        fm_running = False
        fm_stripped = fm_output.strip().lower()
        if fm_stripped:
            first_line = fm_stripped.split("\n")[0].strip()
            if first_line == "active" or "active (running)" in fm_stripped:
                fm_running = True

        if not fm_running and fm_output.strip():
            issues.append("fm_down")

        inactive_count = 0
        if nvlink_output.strip():
            for line in nvlink_output.strip().split("\n"):
                if "inActive" in line or "inactive" in line.lower():
                    inactive_count = inactive_count + 1

        if inactive_count > 0:
            issues.append("nvlink_inactive")

        host_state["prev_issues"] = issues
        new_state[hostname] = host_state

        if issues:
            observations.append(Observation(
                signal_key="b300_nvlink_fm_bad",
                target_id=hostname,
                status="bad",
                severity="critical",
                action="cordon_node",
                evidence={
                    "raw_output": (fm_output.strip() + "\n---\n" + nvlink_output.strip())[:2000],
                    "issues": issues,
                    "fm_status": fm_output.strip()[:500] if fm_output.strip() else "empty/missing",
                    "nvlink_inactive_count": inactive_count,
                },
                confidence=0.85,
                lifecycle=lifecycle,
            ))
        else:
            observations.append(Observation(
                signal_key="b300_nvlink_fm_bad",
                target_id=hostname,
                status="healthy",
                severity="critical",
                action="cordon_node",
                evidence={
                    "fm_status": fm_output.strip()[:500] if fm_output.strip() else "empty/missing",
                    "nvlink_inactive_count": inactive_count,
                },
                confidence=1.0,
                lifecycle=lifecycle,
            ))

    return RuleResult(observations=observations, state=new_state)
'''


B300_NVSWITCH_TRAY_V1 = r'''
def analyze(collected, state):
    RXDETECT_PATTERN = "knvlinkUpdatePostRxDetectLinkMask_IMPL"
    RXDETECT_PATTERN2 = "knvlinkDiscoverPostRxDetLinks"
    MIN_ERROR_LINES = 5
    SMI_TIMEOUT_CODE = "124"

    observations = []
    new_state = dict(state)

    lifecycle = {
        "kind": "condition",
        "open_after_consecutive": 2,
        "close_after_healthy": 2,
        "unknown_keeps_active": True,
    }

    for target in collected.targets:
        hostname = target.id
        payload = target.payload or {}
        host_state = dict(new_state.get(hostname, {}))

        if not payload.get("ssh_ok"):
            host_state["last_status"] = "unknown"
            host_state["last_issue"] = "ssh_unreachable_or_unknown"
            new_state[hostname] = host_state
            observations.append(Observation(
                signal_key="b300_nvswitch_tray_failure",
                target_id=hostname,
                status="unknown",
                severity="critical",
                action="drain_node",
                evidence={
                    "reason": "ssh_unreachable_or_unknown",
                    "ssh_error": payload.get("ssh_error", ""),
                },
                confidence=0.0,
                lifecycle=lifecycle,
            ))
            continue

        outputs = payload.get("outputs")
        if not isinstance(outputs, dict):
            host_state["last_status"] = "unknown"
            host_state["last_issue"] = "missing_outputs"
            new_state[hostname] = host_state
            observations.append(Observation(
                signal_key="b300_nvswitch_tray_failure",
                target_id=hostname,
                status="unknown",
                severity="critical",
                action="drain_node",
                evidence={"reason": "missing_outputs"},
                confidence=0.0,
                lifecycle=lifecycle,
            ))
            continue

        dmesg_rxdetect = outputs.get("dmesg_rxdetect", "") or ""
        smi_exit_code = (outputs.get("nvidia_smi_timeout", "") or "").strip()

        rxdetect_lines = 0
        for line in dmesg_rxdetect.split("\n"):
            line_stripped = line.strip()
            if line_stripped and (
                RXDETECT_PATTERN in line_stripped
                or RXDETECT_PATTERN2 in line_stripped
            ):
                rxdetect_lines = rxdetect_lines + 1

        has_rxdetect = rxdetect_lines >= MIN_ERROR_LINES
        smi_hung = smi_exit_code == SMI_TIMEOUT_CODE
        is_tray_failure = has_rxdetect or smi_hung

        host_state["last_status"] = "bad" if is_tray_failure else "healthy"
        host_state["last_issue"] = "nvswitch_tray_failure" if is_tray_failure else ""
        host_state["rxdetect_error_lines"] = rxdetect_lines
        host_state["nvidia_smi_exit_code"] = smi_exit_code
        new_state[hostname] = host_state

        if is_tray_failure:
            observations.append(Observation(
                signal_key="b300_nvswitch_tray_failure",
                target_id=hostname,
                status="bad",
                severity="critical",
                action="drain_node",
                evidence={
                    "issue": "b300_nvswitch_tray_failure",
                    "raw_output": dmesg_rxdetect[:2000],
                    "dmesg_signature": RXDETECT_PATTERN,
                    "rxdetect_error_lines": rxdetect_lines,
                    "nvidia_smi_exit_code": smi_exit_code,
                    "nvidia_smi_hung": smi_hung,
                },
                confidence=0.95,
                lifecycle=lifecycle,
            ))
        else:
            observations.append(Observation(
                signal_key="b300_nvswitch_tray_failure",
                target_id=hostname,
                status="healthy",
                severity="critical",
                action="drain_node",
                evidence={
                    "issue_clear": True,
                    "rxdetect_error_lines": rxdetect_lines,
                    "nvidia_smi_exit_code": smi_exit_code,
                    "nvidia_smi_hung": smi_hung,
                },
                confidence=1.0,
                lifecycle=lifecycle,
            ))

    return RuleResult(observations=observations, state=new_state)
'''


B300_GPU_XID_V1 = r'''
def analyze(collected, state):
    CRITICAL_XIDS = {13, 31, 32, 48, 63, 79, 94, 95, 119, 137, 145, 154, 171}
    TRANSIENT_XIDS = {43}
    MIN_PCI_FOR_TRANSIENT = 3
    CONSECUTIVE_REQUIRED = 2
    XID_PATTERN = re.compile(r'Xid \(PCI:([0-9a-fA-F:]+)\):\s*(\d+)')
    lifecycle = {"kind": "event"}
    observations = []
    new_state = dict(state)

    for target in collected.targets:
        hostname = target.id
        payload = target.payload or {}
        if not payload.get("ssh_ok"):
            continue
        outputs = payload.get("outputs", {})
        host_state = dict(new_state.get(hostname, {}))
        dmesg_output = outputs.get("dmesg_xid", "")

        if not dmesg_output.strip():
            host_state["prev_xid_count"] = 0
            host_state["consecutive"] = 0
            host_state["last_fired_xid_count"] = host_state.get("last_fired_xid_count", 0)
            new_state[hostname] = host_state
            continue

        lines = dmesg_output.strip().split("\n")
        xid_matches = []
        for line in lines:
            m = XID_PATTERN.search(line)
            if m:
                xid_matches.append((m.group(1), int(m.group(2))))

        current_xid_count = len(xid_matches)
        prev_xid_count = host_state.get("prev_xid_count", 0)
        last_fired_count = host_state.get("last_fired_xid_count", 0)
        consecutive = host_state.get("consecutive", 0)

        if current_xid_count < prev_xid_count:
            host_state["prev_xid_count"] = current_xid_count
            host_state["consecutive"] = 0
            new_state[hostname] = host_state
            continue

        new_events = current_xid_count - prev_xid_count
        host_state["prev_xid_count"] = current_xid_count
        consecutive = consecutive + 1 if new_events > 0 else 0
        host_state["consecutive"] = consecutive

        if current_xid_count <= last_fired_count or consecutive < CONSECUTIVE_REQUIRED:
            new_state[hostname] = host_state
            continue

        xid_codes = set()
        pci_addresses = set()
        for pci, code in xid_matches:
            xid_codes.add(code)
            pci_addresses.add(pci)

        has_critical = len(xid_codes.intersection(CRITICAL_XIDS)) > 0
        has_only_transient = xid_codes.issubset(TRANSIENT_XIDS)
        if has_only_transient and len(pci_addresses) < MIN_PCI_FOR_TRANSIENT:
            new_state[hostname] = host_state
            continue

        raw_lines = []
        for line in lines:
            if "NVRM" in line or "Xid" in line:
                raw_lines.append(line.strip())

        observations.append(Observation(
            signal_key="b300_gpu_xid",
            target_id=hostname,
            status="bad",
            severity="critical",
            action="cordon_node",
            evidence={
                "raw_output": dmesg_output.strip()[:2000],
                "raw_dmesg_lines": raw_lines[-20:],
                "xid_codes": sorted(list(xid_codes)),
                "unique_pci_addresses": len(pci_addresses),
                "has_unknown_xids": not xid_codes.issubset(CRITICAL_XIDS.union(TRANSIENT_XIDS)),
                "has_critical_xids": has_critical,
                "consecutive_detections": consecutive,
                "new_events_this_check": new_events,
            },
            confidence=0.85,
            event_id=hostname + ":xid:" + str(current_xid_count),
            lifecycle=lifecycle,
        ))
        host_state["last_fired_xid_count"] = current_xid_count
        host_state["consecutive"] = 0
        new_state[hostname] = host_state

    return RuleResult(observations=observations, state=new_state)
'''


B300_NIC_HEALTH_V1 = r'''
def analyze(collected, state):
    MLX5_DELTA_THRESHOLD = 50
    observations = []
    new_state = dict(state)
    mlx5_lifecycle = {"kind": "condition", "open_after_consecutive": 3, "close_after_healthy": 2, "unknown_keeps_active": True}
    ib_lifecycle = {"kind": "condition", "open_after_consecutive": 2, "close_after_healthy": 2, "unknown_keeps_active": True}

    for target in collected.targets:
        hostname = target.id
        payload = target.payload or {}
        if "-ctrl-" in hostname or "-pxe-" in hostname:
            continue
        host_state = dict(new_state.get(hostname, {}))

        if not payload.get("ssh_ok"):
            observations.append(Observation(signal_key="b300_nic_mlx5_errors", target_id=hostname, status="unknown", severity="warning", action="cordon_node", evidence={"reason": "ssh_unreachable_or_unknown"}, confidence=0.0, lifecycle=mlx5_lifecycle))
            observations.append(Observation(signal_key="b300_nic_ib_down", target_id=hostname, status="unknown", severity="critical", action="cordon_node", evidence={"reason": "ssh_unreachable_or_unknown"}, confidence=0.0, lifecycle=ib_lifecycle))
            new_state[hostname] = host_state
            continue

        outputs = payload.get("outputs", {})
        mlx5_output = outputs.get("mlx5_errors", "").strip()
        mlx5_count = 0
        if mlx5_output:
            try:
                mlx5_count = int(mlx5_output.split(":")[-1].strip() if ":" in mlx5_output else mlx5_output.split()[0])
            except:
                mlx5_count = 0
        prev_mlx5 = host_state.get("prev_mlx5", 0)
        delta = mlx5_count - prev_mlx5 if mlx5_count > 0 and prev_mlx5 > 0 else 0
        if delta < 0:
            delta = 0
        host_state["prev_mlx5"] = mlx5_count
        mlx5_bad = delta >= MLX5_DELTA_THRESHOLD
        observations.append(Observation(
            signal_key="b300_nic_mlx5_errors",
            target_id=hostname,
            status="bad" if mlx5_bad else "healthy",
            severity="warning",
            action="cordon_node",
            evidence={"raw_output": mlx5_output, "mlx5_error_count": mlx5_count, "mlx5_delta": delta},
            confidence=0.8 if mlx5_bad else 1.0,
            lifecycle=mlx5_lifecycle,
        ))

        ibstat_output = outputs.get("ibstat_ports", "")
        down_ports = []
        block_lines = []
        for line in ibstat_output.strip().split("\n"):
            if (line.startswith("CA '") or line.strip() == "--") and block_lines:
                block_text = "\n".join(block_lines)
                if "State: Down" in block_text:
                    is_ethernet = "Link layer: Ethernet" in block_text
                    is_disabled = "Physical state: Disabled" in block_text
                    if not (is_ethernet and is_disabled):
                        down_ports.append("State: Down")
                block_lines = [line]
            else:
                block_lines.append(line)
        if block_lines:
            block_text = "\n".join(block_lines)
            if "State: Down" in block_text:
                is_ethernet = "Link layer: Ethernet" in block_text
                is_disabled = "Physical state: Disabled" in block_text
                if not (is_ethernet and is_disabled):
                    down_ports.append("State: Down")
        observations.append(Observation(
            signal_key="b300_nic_ib_down",
            target_id=hostname,
            status="bad" if down_ports else "healthy",
            severity="critical",
            action="cordon_node",
            evidence={"raw_output": ibstat_output.strip()[:2000], "down_ports": down_ports, "port_count_down": len(down_ports)},
            confidence=0.9 if down_ports else 1.0,
            lifecycle=ib_lifecycle,
        ))
        new_state[hostname] = host_state

    return RuleResult(observations=observations, state=new_state)
'''


H200_NVME_HEALTH_V1 = r'''
def analyze(collected, state):
    DEVICE_RE = re.compile(r'===DEVICE:(/dev/nvme\d+n1)===')
    JSON_FIELD_RE = re.compile(r'"(\w+)"\s*:\s*(\d+)')
    MDSTAT_DEGRADED_RE = re.compile(r'\[.*_.*\]')
    DMESG_CONTROLLER_RE = re.compile(r'(nvme\s+nvme\d+.*controller is down|I/O error.*nvme|blk_update_request.*nvme|EXT4-fs error|md/raid0.*critical)', re.IGNORECASE)
    DMESG_DISK_FAILURE_RE = re.compile(r'(Disk failure on nvme.*detected|nvme.*fatal error)', re.IGNORECASE)
    lifecycle = {"kind": "condition", "open_after_consecutive": 1, "close_after_healthy": 2, "unknown_keeps_active": True}
    observations = []
    new_state = dict(state)

    for target in collected.targets:
        hostname = target.id
        payload = target.payload or {}
        outputs = payload.get("outputs", {})
        prev_node = dict(state.get(hostname, {}))
        node_state = {}
        if not outputs:
            observations.append(Observation(signal_key="h200_nvme_health_issue", target_id=hostname, status="unknown", severity="critical", action="cordon_node", evidence={"reason": "missing_outputs"}, confidence=0.0, lifecycle=lifecycle))
            new_state[hostname] = prev_node
            continue

        issues = []
        evidence = {}
        nvme_smart = outputs.get("nvme_smart", "")
        if nvme_smart:
            current_device = None
            device_data = {}
            for line in nvme_smart.split("\n"):
                dev_match = DEVICE_RE.search(line)
                if dev_match:
                    current_device = dev_match.group(1)
                    device_data[current_device] = {}
                elif current_device:
                    field_match = JSON_FIELD_RE.search(line)
                    if field_match:
                        device_data[current_device][field_match.group(1)] = int(field_match.group(2))
            for dev, data in device_data.items():
                cw = data.get("critical_warning", 0)
                if cw > 0:
                    issues.append("critical_warning")
                    evidence["critical_warning_device"] = dev
                    evidence["critical_warning_value"] = cw
                media_errors = data.get("media_errors", 0)
                prev_media = prev_node.get("media_" + dev, 0)
                if media_errors > prev_media and prev_media > 0:
                    issues.append("media_errors_increasing")
                    evidence["media_errors_device"] = dev
                    evidence["media_errors_delta"] = media_errors - prev_media
                node_state["media_" + dev] = media_errors
                avail_spare = data.get("avail_spare", 100)
                spare_thresh = data.get("spare_thresh", 10)
                if avail_spare <= spare_thresh:
                    issues.append("spare_below_threshold")
                    evidence["spare_device"] = dev
                    evidence["avail_spare"] = avail_spare

        mdstat = outputs.get("mdstat", "")
        if mdstat and MDSTAT_DEGRADED_RE.search(mdstat):
            issues.append("mdstat_degraded")
            evidence["mdstat_degraded_line"] = mdstat.strip().split("\n")[0]

        dmesg = outputs.get("dmesg_nvme", "")
        if dmesg:
            prev_dmesg_count = prev_node.get("dmesg_error_count", 0)
            current_errors = DMESG_CONTROLLER_RE.findall(dmesg)
            current_count = len(current_errors)
            if current_count > prev_dmesg_count:
                issues.append("dmesg_controller_errors")
                evidence["dmesg_new_errors"] = current_count - prev_dmesg_count
                evidence["dmesg_sample"] = current_errors[:3]
            node_state["dmesg_error_count"] = current_count
            disk_failures = DMESG_DISK_FAILURE_RE.findall(dmesg)
            if disk_failures:
                issues.append("nvme_disk_failure")
                evidence["disk_failure_matches"] = disk_failures[:5]
                evidence["raw_output"] = dmesg

        new_state[hostname] = node_state
        severity = "critical" if any(i in issues for i in ["critical_warning", "mdstat_degraded", "dmesg_controller_errors", "nvme_disk_failure"]) else "warning"
        observations.append(Observation(
            signal_key="h200_nvme_health_issue",
            target_id=hostname,
            status="bad" if issues else "healthy",
            severity=severity,
            action="cordon_node",
            evidence=({"issues": issues, **evidence, "raw_output": dmesg if dmesg else nvme_smart[:500]} if issues else {"issues": [], "raw_output": nvme_smart[:500]}),
            confidence=0.95 if "nvme_disk_failure" in issues else (0.85 if issues else 1.0),
            lifecycle=lifecycle,
        ))

    return RuleResult(observations=observations, state=new_state)
'''


IB_LINK_FLAPPING_REPEAT_V1 = r'''
def analyze(collected, state):
    LOST_CARRIER_RE = re.compile(r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(\S+)\s+systemd-networkd\[\d+\]:\s+(ib\d+):\s+(Lost carrier)')
    REBOOT_PORT_THRESHOLD = 5
    lifecycle = {"kind": "condition", "open_after_consecutive": 2, "close_after_healthy": 2, "unknown_keeps_active": True}
    observations = []
    new_state = dict(state)

    for target in collected.targets:
        hostname = target.id
        payload = target.payload or {}
        prom_phys = payload.get("ib_port_physical_state", {})
        phys_down_ports = []
        for v in prom_phys.get("values", []):
            metric = v.get("metric", {}) if isinstance(v, dict) else {}
            phys_down_ports.append({"port": metric.get("port", "unknown"), "instance": metric.get("instance", "")})
        prom_state = payload.get("ib_port_state", {})
        state_down_ports = []
        for v in prom_state.get("values", []):
            metric = v.get("metric", {}) if isinstance(v, dict) else {}
            state_down_ports.append({"port": metric.get("port", "unknown"), "instance": metric.get("instance", "")})
        ssh_payload = payload.get("syslog_lost_carrier", {})
        lost_carrier_output = ""
        lost_carrier_events = []
        if isinstance(ssh_payload, dict) and ssh_payload.get("ssh_ok"):
            lost_carrier_output = ssh_payload.get("outputs", {}).get("lost_carrier", "")
            for line in lost_carrier_output.strip().split("\n"):
                m = LOST_CARRIER_RE.search(line)
                if m:
                    lost_carrier_events.append({"timestamp": m.group(1), "node_id": m.group(2), "port_id": m.group(3), "message": m.group(4)})
        is_reboot_signature = False
        if len(lost_carrier_events) >= REBOOT_PORT_THRESHOLD:
            distinct_ports = set(e["port_id"] for e in lost_carrier_events)
            is_reboot_signature = len(distinct_ports) >= REBOOT_PORT_THRESHOLD
        if is_reboot_signature:
            lost_carrier_events = []
        raw_parts = []
        if phys_down_ports:
            raw_parts.append("ib_port_physical_state==0: " + str(phys_down_ports))
        if state_down_ports:
            raw_parts.append("ib_port_state==0: " + str(state_down_ports))
        if lost_carrier_output and not is_reboot_signature:
            raw_parts.append("syslog_lost_carrier:\n" + lost_carrier_output[:3000])
        has_issue = bool(phys_down_ports or state_down_ports)
        observations.append(Observation(
            signal_key="ib_link_flapping",
            target_id=hostname,
            status="bad" if has_issue else "healthy",
            severity="critical",
            action="cordon_node",
            evidence={"raw_output": "\n".join(raw_parts) if raw_parts else "", "detection_type": "ib_link_flapping", "phys_down_ports": phys_down_ports, "state_down_ports": state_down_ports},
            confidence=0.9 if has_issue else 1.0,
            lifecycle=lifecycle,
        ))
        new_state[hostname] = dict(new_state.get(hostname, {}))

    return RuleResult(observations=observations, state=new_state)
'''


JOB_FAILURE_INVESTIGATION = r'''
def analyze(collected, state):
    CUDA_PATTERN = re.compile(r'CUDA error: system not yet initialized')
    CRITICAL_XID_PATTERN = re.compile(r'NVRM: Xid.*:\s*(48|63)\b')
    ANY_XID_PATTERN = re.compile(r'NVRM: Xid.*:\s*(13|43|48|63|109)\b')
    observations = []
    new_state = dict(state)
    lifecycle = {"kind": "event"}

    for target in collected.targets:
        hostname = target.id
        payload = target.payload or {}
        entries = payload.get("entries", [])
        source_jobs = payload.get("source_jobs", [])
        if not entries:
            continue
        node_state = dict(new_state.get(hostname, {}))
        alerted_jobs = set(node_state.get("alerted_jobs", []))
        new_jobs = [j for j in source_jobs if j not in alerted_jobs]
        if not new_jobs:
            continue
        has_cuda_error = False
        has_critical_xid = False
        cuda_entries = []
        xid_entries = []
        for entry in entries:
            entry_str = str(entry)
            if CUDA_PATTERN.search(entry_str):
                has_cuda_error = True
                cuda_entries.append(entry_str)
            if ANY_XID_PATTERN.search(entry_str):
                xid_entries.append(entry_str)
                if CRITICAL_XID_PATTERN.search(entry_str):
                    has_critical_xid = True
        if has_cuda_error or has_critical_xid:
            signal = "cuda_error" if has_cuda_error else "critical_xid"
            observations.append(Observation(
                signal_key="job_failure_investigation",
                target_id=hostname,
                status="bad",
                severity="critical",
                action="cordon_node",
                evidence={"raw_output": entries[:50], "source_jobs": new_jobs[:10], "cuda_errors": cuda_entries[:20], "xid_errors": xid_entries[:20], "has_cuda_error": has_cuda_error, "has_critical_xid": has_critical_xid, "fault_code": "CUDADriverFailure", "signal": signal},
                confidence=0.9,
                event_id=hostname + ":" + "|".join(sorted(new_jobs)),
                lifecycle=lifecycle,
            ))
            alerted_jobs.update(new_jobs)
            if len(alerted_jobs) > 50:
                alerted_jobs = set(sorted(alerted_jobs)[-50:])
            node_state["alerted_jobs"] = list(alerted_jobs)
        new_state[hostname] = node_state

    return RuleResult(observations=observations, state=new_state)
'''


LARGE_JOB_FAILURE_V1 = r'''
def analyze(collected, state):
    CORDON_THRESHOLD = 3
    CO_FAILURE_THRESHOLD = 4
    CASCADE_EXIT_CODES = {0, -210, -220}
    observations = []
    new_state = dict(state)
    alerted_sets = dict(new_state.get("alerted_attempt_sets", {}))
    lifecycle = {"kind": "event"}
    root_cause_map = {}
    base_job_nodes = {}

    for target in collected.targets:
        attempt_key = target.id
        payload = target.payload or {}
        parts = attempt_key.rsplit("~", 1)
        base_job = parts[0] if len(parts) == 2 and parts[1].isdigit() else attempt_key
        task_roles = payload.get("taskRoles", {})
        all_tasks = []
        for role_name, role_info in task_roles.items():
            task_statuses = role_info.get("taskStatuses", [])
            if isinstance(task_statuses, list):
                all_tasks.extend(task_statuses)
            elif isinstance(task_statuses, dict):
                all_tasks.extend(task_statuses.values())
        if not all_tasks:
            nodes_data = payload.get("nodes", {})
            if isinstance(nodes_data, dict):
                for node_name, node_info in nodes_data.items():
                    if node_name:
                        all_tasks.append({"containerNodeName": node_name, "taskState": node_info.get("task_state", node_info.get("taskState", "")), "containerExitCode": node_info.get("containerExitCode"), "completedTime": node_info.get("completedTime"), "taskIndex": node_info.get("task_role_index", node_info.get("taskRoleIndex"))})
        total_tasks = len(all_tasks)
        if total_tasks == 0:
            continue
        timed_failures = []
        untimed_failed_nodes = set()
        succeeded_nodes = set()
        for task in all_tasks:
            node_name = task.get("containerNodeName", "") or ""
            if not node_name:
                continue
            task_state = (task.get("taskState", "") or "").upper()
            exit_code = task.get("containerExitCode")
            completed = task.get("completedTime") or task.get("currentAttemptCompletedTime")
            if task_state == "SUCCEEDED" or exit_code == 0:
                succeeded_nodes.add(node_name)
                continue
            if task_state == "STOPPED" or (exit_code is not None and exit_code in CASCADE_EXIT_CODES):
                continue
            if completed and exit_code is not None:
                timed_failures.append({"node": node_name, "completed_ms": completed, "exit_code": exit_code, "task_index": task.get("taskIndex")})
            elif task_state == "FAILED":
                untimed_failed_nodes.add(node_name)
        root_cause_nodes = set()
        if timed_failures:
            timed_failures.sort(key=lambda x: x["completed_ms"])
            first_fail_time = timed_failures[0]["completed_ms"]
            for f in timed_failures:
                if f["completed_ms"] - first_fail_time <= 10000:
                    root_cause_nodes.add(f["node"])
                else:
                    break
        elif untimed_failed_nodes:
            total_with_outcome = len(untimed_failed_nodes) + len(succeeded_nodes)
            if total_with_outcome > 0 and len(untimed_failed_nodes) <= max(2, int(total_tasks * 0.1)):
                root_cause_nodes = untimed_failed_nodes
        if not root_cause_nodes or len(root_cause_nodes) > max(3, int(total_tasks * 0.1)):
            continue
        base_job_nodes.setdefault(base_job, set()).update(root_cause_nodes)
        for node in root_cause_nodes:
            root_cause_map.setdefault(node, []).append((base_job, attempt_key))

    bad_jobs = set()
    for base_job, nodes in base_job_nodes.items():
        if len(nodes) >= CO_FAILURE_THRESHOLD:
            bad_jobs.add(base_job)
    for node, job_attempt_pairs in root_cause_map.items():
        distinct_base_jobs = {}
        for base_job, attempt_key in job_attempt_pairs:
            if base_job in bad_jobs:
                continue
            distinct_base_jobs.setdefault(base_job, []).append(attempt_key)
        if len(distinct_base_jobs) < CORDON_THRESHOLD:
            continue
        sorted_base_jobs = sorted(distinct_base_jobs.keys())
        base_job_set_key = "|".join(sorted_base_jobs)
        if alerted_sets.get(node) == base_job_set_key:
            continue
        alerted_sets[node] = base_job_set_key
        representative_attempts = []
        for base_job, attempts in sorted(distinct_base_jobs.items()):
            representative_attempts.append(attempts[0])
        observations.append(Observation(signal_key="large_job_failure_root_cause", target_id=node, status="bad", severity="critical", action="cordon_node", evidence={"raw_output": {"distinct_base_jobs": sorted_base_jobs, "representative_attempts": sorted(representative_attempts), "total_attempts_seen": len(job_attempt_pairs), "jobs_excluded_co_failure": len([j for j, _ in job_attempt_pairs if j in bad_jobs])}, "summary": {"fault_code": "NodeRootCauseRepeatFailure", "distinct_base_job_count": len(distinct_base_jobs), "detection_method": "first_to_fail_deduped_co_failure_filtered"}}, confidence=0.9, event_id=node + ":" + base_job_set_key, lifecycle=lifecycle))

    new_state["alerted_attempt_sets"] = alerted_sets
    return RuleResult(observations=observations, state=new_state)
'''


NVIDIA_ECC_ERROR_V1 = r'''
def analyze(collected, state):
    observations = []
    new_state = dict(state)
    lifecycle = {"kind": "event"}
    for target in collected.targets:
        hostname = target.id
        payload = target.payload or {}
        ts = dict(new_state.get(hostname, {}))
        ssh_payload = payload.get("ecc_check", {})
        ecc_raw = ""
        ecc_triggered = False
        gpu_details = []
        if ssh_payload.get("ssh_ok"):
            ecc_raw = ssh_payload.get("outputs", {}).get("ecc_counts", "")
            if "NVIDIA_SMI_FAILED" not in ecc_raw and ecc_raw.strip():
                prev_counts = ts.get("prev_ecc_counts", {})
                current_counts = {}
                for line in ecc_raw.strip().split("\n"):
                    parts = line.strip().split(",")
                    if len(parts) >= 4:
                        gpu_idx = parts[0].strip()
                        try:
                            total = int(parts[1].strip())
                            dram = int(parts[2].strip())
                            sram = int(parts[3].strip())
                        except:
                            continue
                        current_counts[gpu_idx] = {"total": total, "dram": dram, "sram": sram}
                        prev = prev_counts.get(gpu_idx, {})
                        prev_total = prev.get("total", 0)
                        if total > prev_total:
                            ecc_triggered = True
                            gpu_details.append({"gpu": gpu_idx, "total": total, "dram": dram, "sram": sram, "delta": total - prev_total})
                ts["prev_ecc_counts"] = current_counts
        job_payload = payload.get("ecc_job_logs", {})
        job_entries = job_payload.get("entries", [])
        source_jobs = job_payload.get("source_jobs", [])
        job_log_triggered = len(job_entries) > 0
        if ecc_triggered or job_log_triggered:
            observations.append(Observation(signal_key="nvidia_ecc_error", target_id=hostname, status="bad", severity="critical", action="cordon_node", evidence={"raw_output": ecc_raw, "gpu_ecc_details": gpu_details, "job_log_entries": job_entries[:20], "source_jobs": source_jobs[:10], "ecc_triggered": ecc_triggered, "job_log_triggered": job_log_triggered}, confidence=1.0, event_id=hostname + ":ecc:" + str(len(gpu_details)) + ":" + "|".join(source_jobs[:10]), lifecycle=lifecycle))
        new_state[hostname] = ts
    return RuleResult(observations=observations, state=new_state)
'''


STORAGE_NVME_HEALTH_V1 = r'''
def analyze(collected, state):
    CW_BITS = {0x01: "spare_capacity_below_threshold", 0x02: "temperature_exceeded", 0x04: "nvm_subsystem_degraded", 0x08: "read_only_mode", 0x10: "volatile_backup_failed"}
    DMESG_DISK_FAILURE_RE = re.compile(r'(Disk failure on nvme.*detected|nvme.*fatal error)', re.IGNORECASE)
    DMESG_CONTROLLER_RE = re.compile(r'(CSTS=0xffffffff|controller is down)', re.IGNORECASE)
    observations = []
    new_state = dict(state)
    critical_lifecycle = {"kind": "condition", "open_after_consecutive": 1, "close_after_healthy": 2, "unknown_keeps_active": True}
    alert_lifecycle = {"kind": "condition", "open_after_consecutive": 1, "close_after_healthy": 2, "unknown_keeps_active": True}

    def parse_json_value(text, key):
        m = re.search(r'"' + re.escape(key) + r'"\s*:\s*(\d+)', text)
        return int(m.group(1)) if m else 0

    for target in collected.targets:
        hostname = target.id
        outputs = (target.payload or {}).get("outputs", {})
        if not outputs:
            observations.append(Observation(signal_key="storage_nvme_critical", target_id=hostname, status="unknown", severity="critical", action="cordon_node", evidence={"reason": "missing_outputs"}, confidence=0.0, lifecycle=critical_lifecycle))
            observations.append(Observation(signal_key="storage_nvme_alert", target_id=hostname, status="unknown", severity="warning", action="alert", evidence={"reason": "missing_outputs"}, confidence=0.0, lifecycle=alert_lifecycle))
            continue
        critical_evidence = None
        alert_evidence = None
        dmesg = outputs.get("dmesg_nvme", "")
        if dmesg:
            disk_failures = DMESG_DISK_FAILURE_RE.findall(dmesg)
            controller_errors = DMESG_CONTROLLER_RE.findall(dmesg)
            all_dmesg_errors = disk_failures + controller_errors
            if all_dmesg_errors:
                critical_evidence = {"fault_code": "NVMeError", "dmesg_matches": all_dmesg_errors[:5], "disk_failure_count": len(disk_failures), "controller_error_count": len(controller_errors), "raw_output": dmesg, "message": "NVMe disk failure/fatal error detected in dmesg - drive dead, cordon immediately."}
        raw = outputs.get("nvme_smart", "")
        if raw and not ("ERROR" in raw and "===DEVICE:" not in raw):
            device_sections = re.split(r"===DEVICE:(/dev/nvme\d+n1)===", raw)
            i = 1
            while i < len(device_sections) - 1:
                device = device_sections[i]
                section_text = device_sections[i + 1].strip()
                i = i + 2
                if not section_text or section_text.startswith("NVMe Status"):
                    continue
                critical_warning = parse_json_value(section_text, "critical_warning")
                media_errors = parse_json_value(section_text, "media_errors")
                percentage_used = parse_json_value(section_text, "percentage_used")
                new_state[hostname + ":" + device] = {"critical_warning": critical_warning, "media_errors": media_errors, "percentage_used": percentage_used}
                if critical_warning != 0 and not critical_evidence:
                    bits_set = [desc for bit, desc in CW_BITS.items() if critical_warning & bit]
                    critical_warning_hex = "0x%x" % critical_warning
                    critical_evidence = {"device": device, "critical_warning": critical_warning_hex, "warning_bits": bits_set, "media_errors": media_errors, "percentage_used": percentage_used, "raw_output": section_text[:500], "message": device + ": critical_warning=" + critical_warning_hex}
                elif (media_errors > 0 or percentage_used > 95) and not alert_evidence:
                    alert_evidence = {"device": device, "critical_warning": "0x%x" % critical_warning, "media_errors": media_errors, "percentage_used": percentage_used, "raw_output": section_text[:500], "message": device + ": drive degrading"}
                elif percentage_used > 80 and not alert_evidence:
                    alert_evidence = {"device": device, "percentage_used": percentage_used, "message": device + ": drive approaching end of life"}
        observations.append(Observation(signal_key="storage_nvme_critical", target_id=hostname, status="bad" if critical_evidence else "healthy", severity="critical", action="cordon_node", evidence=critical_evidence or {"issue_clear": True}, confidence=0.95 if critical_evidence else 1.0, lifecycle=critical_lifecycle))
        observations.append(Observation(signal_key="storage_nvme_alert", target_id=hostname, status="bad" if alert_evidence else "healthy", severity="warning", action="alert", evidence=alert_evidence or {"issue_clear": True}, confidence=0.85 if alert_evidence else 1.0, lifecycle=alert_lifecycle))
    return RuleResult(observations=observations, state=new_state)
'''


SWITCH_HEALTH_IB_V1 = r'''
def analyze(collected, state):
    PORT_HEADER_RE = re.compile(r'^(IB\d+/\d+(?:/\d+)?)\s+state:', re.MULTILINE)
    SYMBOL_LINE_RE = re.compile(r'Symbol errors\s*:\s*(\d+)')
    SSH_FAILURE_THRESHOLD = 10
    SYMBOL_ERROR_DELTA_THRESHOLD = 100
    CONSECUTIVE_THRESHOLD = 3
    BULK_FAILURE_THRESHOLD = 5
    ssh_lifecycle = {"kind": "condition", "open_after_consecutive": SSH_FAILURE_THRESHOLD, "close_after_healthy": 2, "unknown_keeps_active": True}
    symbol_lifecycle = {"kind": "condition", "open_after_consecutive": CONSECUTIVE_THRESHOLD, "close_after_healthy": 2, "unknown_keeps_active": True}
    observations = []
    new_state = dict(state)
    failed_count = len([t for t in collected.targets if not (t.payload or {}).get("ssh_ok")])
    is_bulk_failure = failed_count > BULK_FAILURE_THRESHOLD

    for target in collected.targets:
        switch_id = target.id
        payload = target.payload or {}
        sw_state = dict(new_state.get(switch_id, {}))
        if not payload.get("ssh_ok"):
            sw_state["last_error"] = payload.get("ssh_error", "")
            observations.append(Observation(signal_key="switch_ib_ssh_unreachable", target_id=switch_id, status="unknown" if is_bulk_failure else "bad", severity="warning", action="alert", evidence={"error": "SSH unreachable", "bulk_failure": is_bulk_failure, "failed_switches_in_cycle": failed_count, "ssh_error": payload.get("ssh_error", "")}, confidence=0.0 if is_bulk_failure else 0.6, lifecycle=ssh_lifecycle))
            new_state[switch_id] = sw_state
            continue
        outputs = payload.get("outputs", {})
        ib_output = outputs.get("show interfaces ib", "")
        current_symbol_errors = {}
        current_port = None
        for line in ib_output.split("\n"):
            port_match = PORT_HEADER_RE.match(line)
            if port_match:
                current_port = port_match.group(1)
            sym_match = SYMBOL_LINE_RE.search(line)
            if sym_match and current_port:
                current_symbol_errors[current_port] = int(sym_match.group(1))
        prev_symbol = sw_state.get("prev_symbol_errors", {})
        error_ports = []
        for port, count in current_symbol_errors.items():
            prev_count = prev_symbol.get(port, 0)
            if count >= prev_count:
                delta = count - prev_count
                if delta >= SYMBOL_ERROR_DELTA_THRESHOLD:
                    error_ports.append({"port": port, "symbol_error_delta": delta, "symbol_error_total": count})
        sw_state["prev_symbol_errors"] = current_symbol_errors
        observations.append(Observation(signal_key="switch_ib_symbol_errors", target_id=switch_id, status="bad" if error_ports else "healthy", severity="warning", action="alert", evidence={"error_ports": error_ports}, confidence=0.7 if error_ports else 1.0, lifecycle=symbol_lifecycle))
        observations.append(Observation(signal_key="switch_ib_ssh_unreachable", target_id=switch_id, status="healthy", severity="warning", action="alert", evidence={"ssh_ok": True}, confidence=1.0, lifecycle=ssh_lifecycle))
        new_state[switch_id] = sw_state
    return RuleResult(observations=observations, state=new_state)
'''


SWITCH_HEALTH_RUIJIE_V1 = r'''
def analyze(collected, state):
    SSH_INCOMPATIBLE_PATTERNS = ["Unable to negotiate", "no matching key exchange", "no matching host key", "no matching cipher", "no matching MAC", "Connection refused"]
    MIN_PORT_DELTA = 3
    lifecycle = {"kind": "condition", "open_after_consecutive": 2, "close_after_healthy": 2, "unknown_keeps_active": True}
    observations = []
    new_state = dict(state)
    if "port_errors" not in new_state:
        new_state["port_errors"] = {}
    if "ssh_incompatible" not in new_state:
        new_state["ssh_incompatible"] = []
    ssh_incompatible = set(new_state["ssh_incompatible"])

    for target in collected.targets:
        target_id = target.id
        payload = target.payload or {}
        if not payload.get("ssh_ok"):
            error_msg = payload.get("ssh_error", "")
            is_ssh_incompatible = any(pattern in error_msg for pattern in SSH_INCOMPATIBLE_PATTERNS)
            if is_ssh_incompatible:
                ssh_incompatible.add(target_id)
                new_state["port_errors"].pop(target_id, None)
                continue
            observations.append(Observation(signal_key="switch_ruijie_port_errors", target_id=target_id, status="unknown", severity="warning", action="alert", evidence={"ssh_error": error_msg}, confidence=0.0, lifecycle=lifecycle))
            continue
        ssh_incompatible.discard(target_id)
        counters_output = payload.get("outputs", {}).get("show interfaces counters errors", "")
        if not counters_output:
            observations.append(Observation(signal_key="switch_ruijie_port_errors", target_id=target_id, status="unknown", severity="warning", action="alert", evidence={"reason": "missing_counters"}, confidence=0.0, lifecycle=lifecycle))
            continue
        current_errors = {}
        in_crc_section = False
        for line in counters_output.split("\n"):
            line = line.strip().replace("\r", "")
            if "CRC-Align-Err" in line and "FCS-Err" in line:
                in_crc_section = True
                continue
            if not in_crc_section or line.startswith("---") or not line or line.endswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 5:
                port = parts[0]
                try:
                    crc_align_err = int(parts[2])
                    fcs_err = int(parts[4])
                except:
                    continue
                if crc_align_err > 0 or fcs_err > 0:
                    current_errors[port] = {"crc": crc_align_err, "fcs": fcs_err}
        prev_errors = new_state["port_errors"].get(target_id, {})
        growing_ports = []
        for port, counts in current_errors.items():
            prev = prev_errors.get(port, {})
            crc_delta = counts["crc"] - prev.get("crc", 0)
            fcs_delta = counts["fcs"] - prev.get("fcs", 0)
            if prev and (crc_delta > 0 or fcs_delta > 0) and crc_delta + fcs_delta >= MIN_PORT_DELTA:
                growing_ports.append({"port": port, "error_delta": crc_delta + fcs_delta, "error_total": counts["crc"] + counts["fcs"], "crc_delta": crc_delta, "fcs_delta": fcs_delta})
        new_state["port_errors"][target_id] = current_errors
        growing_ports.sort(key=lambda x: x["error_delta"], reverse=True)
        observations.append(Observation(signal_key="switch_ruijie_port_errors", target_id=target_id, status="bad" if growing_ports else "healthy", severity="warning", action="alert", evidence={"error_details": growing_ports, "persistent_error_ports": [p["port"] for p in growing_ports]}, confidence=0.8 if growing_ports else 1.0, lifecycle=lifecycle))
    new_state["ssh_incompatible"] = list(ssh_incompatible)
    return RuleResult(observations=observations, state=new_state)
'''


SWITCH_HEALTH_UFM_V1 = r'''
def analyze(collected, state):
    observations = []
    new_state = dict(state)
    unreachable_lifecycle = {"kind": "condition", "open_after_consecutive": 15, "close_after_healthy": 2, "unknown_keeps_active": True}
    reboot_lifecycle = {"kind": "event"}
    total_targets = len(collected.targets)
    failing_targets = sum(1 for t in collected.targets if not (t.payload or {}).get("ssh_ok") or not (t.payload or {}).get("outputs", {}))
    all_targets_failing = failing_targets == total_targets and total_targets > 1

    for t in collected.targets:
        ts = dict(new_state.get(t.id, {}))
        payload = t.payload or {}
        outputs = payload.get("outputs", {})
        error = payload.get("ssh_error", "")
        if not payload.get("ssh_ok") or not outputs:
            ts["fail_count"] = ts.get("fail_count", 0) + 1
            observations.append(Observation(signal_key="switch_ufm_unreachable", target_id=t.id, status="unknown" if all_targets_failing else "bad", severity="critical", action="cordon_switch_nodes", action_params={"alertname": "SwitchUnreachable"}, evidence={"fail_count": ts["fail_count"], "error": error, "all_targets_failing": all_targets_failing, "raw_output": "SSH unreachable"}, confidence=0.0 if all_targets_failing else 1.0, lifecycle=unreachable_lifecycle))
            new_state[t.id] = ts
            continue
        ts["fail_count"] = 0
        uptime_out = outputs.get("cat /proc/uptime", "")
        uptime_s = None
        for line in uptime_out.splitlines():
            line = line.strip()
            m = re.match(r'^(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)$', line)
            if m:
                uptime_s = float(m.group(1))
                break
        if uptime_s is not None and uptime_s < 120:
            observations.append(Observation(signal_key="switch_ufm_rebooted", target_id=t.id, status="bad", severity="warning", action="alert", action_params={"alertname": "SwitchRebooted"}, evidence={"uptime_s": uptime_s, "raw_output": uptime_out.strip()}, confidence=1.0, event_id=t.id + ":reboot:" + str(int(uptime_s)), lifecycle=reboot_lifecycle))
        observations.append(Observation(signal_key="switch_ufm_unreachable", target_id=t.id, status="healthy", severity="critical", action="cordon_switch_nodes", action_params={"alertname": "SwitchUnreachable"}, evidence={"ssh_ok": True}, confidence=1.0, lifecycle=unreachable_lifecycle))
        new_state[t.id] = ts
    return RuleResult(observations=observations, state=new_state)
'''
