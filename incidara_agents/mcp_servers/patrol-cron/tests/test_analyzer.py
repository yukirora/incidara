"""Tests for patrol_cron.analyzer — sandboxed analyze() execution."""

import unittest.mock as um
import pytest
from patrol_cron.models import CollectionResult, TargetData, Finding
from patrol_cron.analyzer import run_sandboxed


def _make_result(targets=None):
    return CollectionResult(
        collector_name="test_collector",
        targets=targets or [],
    )


class TestRunSandboxed:
    """Core sandbox tests."""

    def test_simple_analyze_returns_findings(self):
        """analyze() that returns one finding per target works."""
        code = '''
def analyze(collected, state):
    findings = []
    for t in collected.targets:
        findings.append(Finding(
            target_id=t.id,
            severity="warning",
            action="alert",
        ))
    return findings, state
'''
        result = _make_result([
            TargetData(id="node-1", type="node", payload={"ok": False}),
            TargetData(id="node-2", type="node", payload={"ok": True}),
        ])
        findings, new_state = run_sandboxed(code, result, {})
        assert len(findings) == 2
        assert findings[0].target_id == "node-1"
        assert findings[0].severity == "warning"
        assert findings[0].action == "alert"
        assert isinstance(findings[0], Finding)

    def test_state_persists_across_calls(self):
        """State returned by analyze() is passed back on next call."""
        code = '''
def analyze(collected, state):
    count = state.get("count", 0) + 1
    return [], {"count": count}
'''
        result = _make_result()
        _, state1 = run_sandboxed(code, result, {})
        assert state1["count"] == 1

        _, state2 = run_sandboxed(code, result, state1)
        assert state2["count"] == 2

    def test_state_keyed_by_target(self):
        """analyze() can maintain per-target state in the dict."""
        code = '''
def analyze(collected, state):
    findings = []
    for t in collected.targets:
        ts = dict(state.get(t.id, {}))
        fc = ts.get("fail_count", 0)
        if not t.payload.get("ok"):
            fc += 1
        else:
            fc = 0
        ts["fail_count"] = fc
        state[t.id] = ts
        if fc >= 3:
            findings.append(Finding(
                target_id=t.id, severity="critical", action="cordon_node",
            ))
    return findings, state
'''
        result = _make_result([
            TargetData(id="sw1", type="switch", payload={"ok": False}),
        ])
        # Run 3 times — should fire on 3rd
        _, state = run_sandboxed(code, result, {})
        assert state["sw1"]["fail_count"] == 1

        _, state = run_sandboxed(code, result, state)
        assert state["sw1"]["fail_count"] == 2

        findings, state = run_sandboxed(code, result, state)
        assert state["sw1"]["fail_count"] == 3
        assert len(findings) == 1
        assert findings[0].action == "cordon_node"

    def test_empty_targets_no_findings(self):
        """analyze() with no targets returns empty findings."""
        code = '''
def analyze(collected, state):
    return [], state
'''
        findings, state = run_sandboxed(code, _make_result(), {})
        assert findings == []
        assert state == {}

    def test_confidence_field_preserved(self):
        """Confidence value set in Finding is preserved through sandbox."""
        code = '''
def analyze(collected, state):
    return [Finding(
        target_id="n1", severity="info", action="alert",
        confidence=0.75,
    )], state
'''
        findings, _ = run_sandboxed(code, _make_result(), {})
        assert findings[0].confidence == 0.75


class TestSandboxSecurity:
    """Verify sandbox restrictions."""

    def test_no_import_allowed(self):
        """analyze() cannot import modules."""
        code = '''
def analyze(collected, state):
    import os
    return [], state
'''
        with pytest.raises(RuntimeError, match="failed"):
            run_sandboxed(code, _make_result(), {})

    def test_no_open_allowed(self):
        """analyze() cannot open files."""
        code = '''
def analyze(collected, state):
    open("/etc/passwd")
    return [], state
'''
        with pytest.raises(RuntimeError, match="failed"):
            run_sandboxed(code, _make_result(), {})

    def test_no_exec_allowed(self):
        """analyze() cannot call exec."""
        code = '''
def analyze(collected, state):
    exec("import os")
    return [], state
'''
        with pytest.raises(RuntimeError, match="failed"):
            run_sandboxed(code, _make_result(), {})

    def test_math_and_re_available(self):
        """math and re modules are available in sandbox."""
        code = '''
def analyze(collected, state):
    import math  # this should fail — but math is in globals
    return [], state
'''
        # Actually, math is injected into globals, not importable.
        # Let's test it's accessible directly:
        code2 = '''
def analyze(collected, state):
    val = math.sqrt(16)
    match = re.match(r"node-(\\d+)", "node-42")
    return [Finding(
        target_id=str(int(val)),
        severity="info",
        action="alert",
        evidence={"group": match.group(1)},
    )], state
'''
        findings, _ = run_sandboxed(code2, _make_result(), {})
        assert findings[0].target_id == "4"
        assert findings[0].evidence["group"] == "42"

    def test_timeout_kills_infinite_loop(self):
        """analyze() that runs forever is killed after timeout (subprocess mode)."""
        import os
        code = '''
def analyze(collected, state):
    while True:
        pass
    return [], state
'''
        # Timeout only works in subprocess mode
        with um.patch.dict(os.environ, {"PATROL_SANDBOX_SUBPROCESS": "1"}):
            with pytest.raises(RuntimeError, match="timed out"):
                run_sandboxed(code, _make_result(), {}, timeout=2)

    def test_missing_analyze_function(self):
        """Code that doesn't define analyze() raises error."""
        code = '''
def not_analyze(collected, state):
    return [], state
'''
        with pytest.raises(RuntimeError, match="does not define"):
            run_sandboxed(code, _make_result(), {})

    def test_analyze_raising_exception(self):
        """analyze() that raises is caught."""
        code = '''
def analyze(collected, state):
    raise ValueError("bad data")
'''
        with pytest.raises(RuntimeError, match="failed"):
            run_sandboxed(code, _make_result(), {})


def test_run_sandboxed_accepts_rule_result():
    from patrol_cron.models import RuleResult

    code = """
def analyze(collected, state):
    observations = []
    for t in collected.targets:
        observations.append(Observation(
            signal_key="fm_bad",
            target_id=t.id,
            status="bad",
            action="cordon_node",
            severity="critical",
            lifecycle={"kind": "condition", "open_after_consecutive": 3},
        ))
    return RuleResult(observations=observations, state={"ran": True})
"""
    collected = CollectionResult(
        collector_name="c",
        targets=[TargetData(id="node-1", type="node", payload={"ssh_ok": True})],
    )

    result = run_sandboxed(code, collected, {})

    assert isinstance(result, RuleResult)
    assert result.state == {"ran": True}
    assert result.observations[0].signal_key == "fm_bad"
    assert result.observations[0].lifecycle["open_after_consecutive"] == 3


def test_run_sandboxed_wraps_legacy_findings_as_bad_observations():
    from patrol_cron.models import RuleResult

    code = """
def analyze(collected, state):
    return [Finding(target_id="node-1", severity="warning", action="alert", evidence={"x": 1})], {"legacy": True}
"""
    collected = CollectionResult(
        collector_name="c",
        targets=[TargetData(id="node-1", type="node", payload={"ssh_ok": True})],
    )

    result = run_sandboxed(code, collected, {})

    assert isinstance(result, RuleResult)
    assert result.state == {"legacy": True}
    assert result.observations[0].signal_key == "legacy"
    assert result.observations[0].status == "bad"
    assert result.observations[0].target_id == "node-1"
