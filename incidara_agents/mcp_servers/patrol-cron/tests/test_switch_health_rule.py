"""Tests for the switch_health_v1 analyze() code from the seed SQL.

Tests the rule against realistic raw SSH output from switches.
"""

import pytest
from patrol_cron.models import CollectionResult, TargetData, Finding
from patrol_cron.analyzer import run_sandboxed

# The exact analyze code from patrol_cron_seed.sql
SWITCH_HEALTH_CODE = '''
def analyze(collected, state):
    findings = []
    for t in collected.targets:
        ts = dict(state.get(t.id, {}))
        outputs = t.payload.get("outputs", {})
        error = t.payload.get("ssh_error", "")

        # SSH failure — target unreachable
        if not t.payload.get("ssh_ok") or not outputs:
            fc = ts.get("fail_count", 0) + 1
            ts["fail_count"] = fc
            if fc >= 5:
                findings.append(Finding(
                    target_id=t.id, severity="critical",
                    action="cordon_switch_nodes",
                    action_params={"alertname": "SwitchUnreachable"},
                    evidence={"fail_count": fc, "error": error}))
                ts["fail_count"] = 0
            state[t.id] = ts
            continue

        # SSH succeeded — reset fail count, parse outputs
        ts["fail_count"] = 0
        issues = []

        # Parse uptime from "show version"
        version_out = outputs.get("show version", "")
        uptime_s = None
        m = re.search(r"[Ss]ystem\\s+uptime\\s*:\\s*(\\d+):(\\d+):(\\d+):(\\d+)", version_out)
        if m:
            d, h, mi, s = (int(x) for x in m.groups())
            uptime_s = d * 86400 + h * 3600 + mi * 60 + s
        if uptime_s is None:
            m = re.search(r"^\\s*(\\d+(?:\\.\\d+)?)\\s+\\d+", version_out, re.MULTILINE)
            if m:
                uptime_s = float(m.group(1))
        if uptime_s is not None and uptime_s < 120:
            issues.append("short uptime")

        # Parse PSU from "show power"
        power_out = outputs.get("show power", "")
        for line in power_out.splitlines():
            toks = line.split()
            if toks and re.match(r"PS\\d+$", toks[0]):
                status = toks[-1] if toks else ""
                if status.upper() not in ("OK", ""):
                    issues.append("psu")
                    break

        # Parse fans from "show fan"
        fan_out = outputs.get("show fan", "")
        for line in fan_out.splitlines():
            if re.search(r"\\b(FAIL|ERROR|0RPM)\\b", line, re.IGNORECASE):
                issues.append("fan")
                break

        # Parse temperature from "show temperature"
        temp_out = outputs.get("show temperature", "")
        for line in temp_out.splitlines():
            if re.search(r"\\b(CRITICAL|OVERHEAT|FAIL)\\b", line, re.IGNORECASE):
                issues.append("temperature")
                break

        # Port error delta detection
        iface_out = outputs.get("show interfaces ib", "") or outputs.get("show interfaces counters errors", "")
        port_counters = {}
        for line in iface_out.splitlines():
            m = re.match(r"\\s*((?:IB|Eth)\\S+).*?(\\d+)\\s+errors?", line, re.IGNORECASE)
            if m:
                port, errs = m.group(1), int(m.group(2))
                if errs > 0:
                    port_counters[port] = errs
        prev_counters = ts.get("port_counters", {})
        grew = []
        for port, errs in port_counters.items():
            prev = prev_counters.get(port, 0)
            if errs > prev:
                grew.append(f"{port}+{errs - prev}")
        ts["port_counters"] = port_counters
        if grew:
            issues.append("port_err_delta")

        # Emit findings
        if "psu" in issues:
            findings.append(Finding(
                target_id=t.id, severity="critical",
                action="cordon_switch_nodes",
                action_params={"alertname": "SwitchPSUFailure"},
                evidence={"issues": issues, "power_output": power_out[:500]}))
        elif "short uptime" in issues:
            findings.append(Finding(
                target_id=t.id, severity="warning",
                action="alert",
                action_params={"alertname": "SwitchRebooted"},
                evidence={"uptime_s": uptime_s, "issues": issues}))
        elif "port_err_delta" in issues:
            findings.append(Finding(
                target_id=t.id, severity="warning",
                action="create_task",
                evidence={"port_deltas": grew, "issues": issues}))
        elif "temperature" in issues or "fan" in issues:
            findings.append(Finding(
                target_id=t.id, severity="warning",
                action="alert",
                action_params={"alertname": "SwitchEnvironment"},
                evidence={"issues": issues}))

        state[t.id] = ts
    return findings, state
'''


def _make_switch(name, ok=True, outputs=None, error=""):
    return TargetData(
        id=name, type="switch",
        payload={"ssh_ok": ok, "outputs": outputs or {}, "ssh_error": error},
        meta={"ip": "10.0.0.1"},
    )


def _run(targets, state=None):
    result = CollectionResult(collector_name="switch_health", targets=targets)
    return run_sandboxed(SWITCH_HEALTH_CODE, result, state or {})


class TestHealthySwitch:
    def test_ok_switch_no_findings(self):
        t = _make_switch("sw1", outputs={
            "show version": "System uptime: 10:05:30:00",
            "show power": "PS1 OK",
            "show fan": "Fan1 OK 5000RPM",
            "show temperature": "CPU T1 45 OK",
        })
        findings, state = _run([t])
        assert findings == []
        assert state["sw1"]["fail_count"] == 0


class TestSshFailure:
    def test_single_failure_no_finding(self):
        t = _make_switch("sw1", ok=False, error="Connection timed out")
        findings, state = _run([t])
        assert findings == []
        assert state["sw1"]["fail_count"] == 1

    def test_5_failures_fires_cordon(self):
        state = {"sw1": {"fail_count": 4}}
        t = _make_switch("sw1", ok=False, error="timeout")
        findings, state = _run([t], state)
        assert len(findings) == 1
        assert findings[0].action == "cordon_switch_nodes"
        assert findings[0].action_params["alertname"] == "SwitchUnreachable"

    def test_fail_count_resets_after_firing(self):
        state = {"sw1": {"fail_count": 4}}
        t = _make_switch("sw1", ok=False, error="timeout")
        findings, state = _run([t], state)
        assert state["sw1"]["fail_count"] == 0

    def test_ok_resets_fail_count(self):
        state = {"sw1": {"fail_count": 3}}
        t = _make_switch("sw1", outputs={"show version": "System uptime: 1:00:00:00"})
        findings, state = _run([t], state)
        assert state["sw1"]["fail_count"] == 0


class TestPSUFailure:
    def test_psu_not_ok_fires_critical(self):
        t = _make_switch("sw1", outputs={
            "show version": "System uptime: 10:00:00:00",
            "show power": "PS1 OK\nPS2 FAIL",
            "show fan": "", "show temperature": "",
        })
        findings, _ = _run([t])
        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert findings[0].action_params["alertname"] == "SwitchPSUFailure"


class TestShortUptime:
    def test_short_uptime_fires_alert(self):
        t = _make_switch("sw1", outputs={
            "show version": "System uptime: 0:00:01:30",  # 90 seconds
            "show power": "", "show fan": "", "show temperature": "",
        })
        findings, _ = _run([t])
        assert len(findings) == 1
        assert findings[0].action == "alert"
        assert findings[0].action_params["alertname"] == "SwitchRebooted"


class TestFanFailure:
    def test_fan_fail_fires_alert(self):
        t = _make_switch("sw1", outputs={
            "show version": "System uptime: 10:00:00:00",
            "show power": "", "show temperature": "",
            "show fan": "Fan1 Module1 F1 5000 OK\nFan2 Module1 F2 0 FAIL",
        })
        findings, _ = _run([t])
        assert len(findings) == 1
        assert findings[0].action_params["alertname"] == "SwitchEnvironment"


class TestMultipleSwitches:
    def test_independent_state(self):
        targets = [
            _make_switch("sw1", ok=False, error="timeout"),
            _make_switch("sw2", outputs={
                "show version": "System uptime: 5:00:00:00",
                "show power": "PS1 OK", "show fan": "", "show temperature": "",
            }),
        ]
        findings, state = _run(targets)
        assert state["sw1"]["fail_count"] == 1
        assert state["sw2"]["fail_count"] == 0
        assert findings == []
