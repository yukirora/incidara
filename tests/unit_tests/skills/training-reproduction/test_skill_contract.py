from pathlib import Path
import re


ROOT = Path(__file__).parents[4]
SKILLS = ROOT / "incidara_agents" / "skills"
MODES = {
    "job-hang": "HANG",
    "nccl-timeout": "NCCL",
    "gpu-oom": "OOM",
    "loss-nan": "NAN",
    "throughput-slowdown": "SLOW",
    "throughput-jitter": "JITTER",
    "checkpoint-failure": "CKPT",
    "process-crash": "CRASH",
}
MAJORS = ("job-log-triage", "system-evidence-diagnosis", "training-reproduction")


def text(path):
    return path.read_text()


def hypothesis_ids(major, mode, prefix):
    content = text(SKILLS / major / "sub-skills" / mode / "SKILL.md")
    return set(re.findall(rf"\b{prefix}-[A-Z]+(?:-[A-Z]+)*\b", content))


def table_hypothesis_ids(content, prefix):
    return re.findall(rf"^\| `{prefix}-[A-Z]+(?:-[A-Z]+)*`", content, re.M)


def test_incident_lifecycle_is_orchestrated_once():
    incident = text(SKILLS / "job-incident-response" / "SKILL.md")
    assert len(incident.splitlines()) < 220
    for stage in (
        "RAPID_DIAGNOSIS",
        "ISOLATION_DECISION",
        "RECOVERING",
        "RECOVERED",
        "DEEP_DIAGNOSIS",
        "FIX_VALIDATION",
        "LEARNING",
        "CLOSED",
    ):
        assert stage in incident
    for skill in (
        "/job-log-triage",
        "/system-evidence-diagnosis",
        "/job-recovery",
        "/training-reproduction",
        "/rca-closeout",
    ):
        assert skill in incident
    assert not (SKILLS / "job-triage").exists()


def test_failure_modes_are_split_by_major_skill_with_matching_ids():
    for mode, prefix in MODES.items():
        sets = []
        for major in MAJORS:
            path = SKILLS / major / "sub-skills" / mode / "SKILL.md"
            assert path.exists(), path
            assert len(text(path).splitlines()) < 260
            sets.append(hypothesis_ids(major, mode, prefix))
        assert sets[0], mode
        assert sets[0] == sets[1] == sets[2], (mode, sets)


def test_each_major_owns_only_its_stage_and_defines_handoff():
    major_files = [
        SKILLS / name / "SKILL.md"
        for name in (
            "job-incident-response",
            "job-log-triage",
            "system-evidence-diagnosis",
            "training-reproduction",
            "job-recovery",
            "rca-closeout",
        )
    ]
    for path in major_files:
        content = text(path)
        assert "## Output Format" in content, path
        assert "## Handoff" in content, path

    log = text(SKILLS / "job-log-triage" / "SKILL.md")
    evidence = text(SKILLS / "system-evidence-diagnosis" / "SKILL.md")
    reproduction = text(SKILLS / "training-reproduction" / "SKILL.md")

    assert len(log.splitlines()) < 180
    assert "first log anomaly" in log
    assert "IMPACT_SCOPE:" in log
    assert "FAILURE_STAGE:" in log
    assert "NEXT_SKILL: /system-evidence-diagnosis" in log

    assert len(evidence.splitlines()) < 220
    assert "Normalize identities" in evidence
    assert "Normalize time" in evidence
    assert "NEXT_SKILL: /job-recovery" in evidence
    assert "REPRODUCTION_GOAL: fast-isolation" in evidence
    assert "REPRODUCTION_GOAL: deep-diagnosis" in evidence

    assert len(reproduction.splitlines()) < 220
    for stage in ("fast-isolation", "deep-diagnosis", "validation"):
        assert stage in reproduction
    assert "Log-pattern classification belongs to `/job-log-triage`" in reproduction
    assert "zero hypotheses is valid" in reproduction
    assert "phenomenon-capture" in reproduction
    assert "completed upstream skill must set the reproduction goal" in reproduction
    assert not (SKILLS / "training-reproduction" / "runbooks").exists()


def test_unknown_patterns_use_bounded_provisional_candidates():
    log = text(SKILLS / "job-log-triage" / "SKILL.md")
    evidence = text(SKILLS / "system-evidence-diagnosis" / "SKILL.md")
    reproduction = text(SKILLS / "training-reproduction" / "SKILL.md")
    closeout = text(SKILLS / "rca-closeout" / "SKILL.md")

    for marker in (
        "adequate log evidence contradicts all canonical rows",
        "1–3 incident-scoped PROVISIONAL_CANDIDATES",
        "why known rows do not fit",
        "refuting observation",
        "return a named Evidence Gap; do not invent a cause",
    ):
        assert marker in log
    assert "this stage may propose 1–3 candidates" in evidence
    assert "do not add it to a failure-mode table" in evidence
    assert "use it alone to authorize Node/Rack isolation, RMA, or repair" in evidence
    assert "Do not invent a hypothesis in reproduction" in reproduction
    assert "new causal ideas go back through log/evidence diagnosis" in reproduction
    assert "add exactly one row with that same ID" in closeout
    assert "Recovery success or model confidence alone is insufficient" in closeout
    assert "job-log-triage`, `system-evidence-diagnosis`, and `training-reproduction` tables" in closeout

    for mode in MODES:
        for major in MAJORS:
            content = text(SKILLS / major / "sub-skills" / mode / "SKILL.md")
            assert "NOVEL-" not in content
    assert not (SKILLS / "training-reproduction" / "sub-skills" / "hypothesis-catalog").exists()


def test_major_skills_handoff_directly_by_completed_stage():
    incident = text(SKILLS / "job-incident-response" / "SKILL.md")
    log = text(SKILLS / "job-log-triage" / "SKILL.md")
    evidence = text(SKILLS / "system-evidence-diagnosis" / "SKILL.md")
    reproduction = text(SKILLS / "training-reproduction" / "SKILL.md")
    recovery = text(SKILLS / "job-recovery" / "SKILL.md")
    closeout = text(SKILLS / "rca-closeout" / "SKILL.md")

    assert "Allowed direct transitions" in incident
    assert "job-log-triage → system-evidence-diagnosis" in incident
    assert "Append the log result" in log
    assert "Append the system-evidence result" in evidence
    for next_skill in ("/job-recovery", "/system-evidence-diagnosis", "/rca-closeout"):
        assert f"NEXT_SKILL: {next_skill}" in reproduction
    for next_skill in ("/system-evidence-diagnosis", "/training-reproduction", "/rca-closeout"):
        assert f"NEXT_SKILL: {next_skill}" in recovery
    assert "NEXT_SKILL: /training-reproduction" in closeout
    assert "NEXT_SKILL: CLOSED" in closeout


def test_failure_mode_playbook_contracts_are_explicit():
    for mode in MODES:
        log = text(SKILLS / "job-log-triage" / "sub-skills" / mode / "SKILL.md")
        for heading in ("## Trigger", "## Goal", "## Log patterns", "## Interpretation", "## Output"):
            assert heading in log, (mode, heading)
        for column in ("候选原因", "典型现象", "Log"):
            assert column in log, (mode, column)

        evidence = text(SKILLS / "system-evidence-diagnosis" / "sub-skills" / mode / "SKILL.md")
        for heading in ("## Trigger", "## Goal", "## Queries", "## Interpretation", "## Output"):
            assert heading in evidence, (mode, heading)
        for column in ("需要的Metric/证据", "Pattern", "支持", "最大隔离范围"):
            assert column in evidence, (mode, column)

        reproduction = text(SKILLS / "training-reproduction" / "sub-skills" / mode / "SKILL.md")
        for heading in ("## Trigger", "## Goal", "## Experiments", "## Interpretation", "## Output"):
            assert heading in reproduction, (mode, heading)
        for column in ("进入条件", "唯一变化项", "判定标准", "证明边界"):
            assert column in reproduction, (mode, column)

        prefix = MODES[mode]
        expected = hypothesis_ids("job-log-triage", mode, prefix)
        for content in (log, evidence, reproduction):
            table_rows = table_hypothesis_ids(content, prefix)
            table_ids = {row.split("`")[1] for row in table_rows}
            assert table_ids == expected, (mode, table_ids, expected)
            assert len(table_rows) == len(expected), (mode, table_rows)
        for major in MAJORS:
            assert not (SKILLS / major / "sub-skills" / mode / "runbooks").exists()
        assert "phenomenon capture" in reproduction.lower() or "No hypothesis" in reproduction, mode

    startup = text(SKILLS / "job-log-triage" / "sub-skills" / "startup-scheduling" / "SKILL.md")
    for marker in ("候选原因", "典型现象", "Event/Log关键字与先后Pattern", "判定与动作"):
        assert marker in startup


def test_nccl_log_triage_gates_on_scope_and_stage():
    nccl = text(SKILLS / "job-log-triage" / "sub-skills" / "nccl-timeout" / "SKILL.md")
    for marker in (
        "## Impact scope",
        "Multiple Jobs same Node",
        "Same Rack/Rail multiple Jobs",
        "Independent Jobs same seconds",
        "## Failure stage",
        "Communicator Init/Connection",
        "First Collective",
        "Stable then sudden timeout",
        "Message-size/scale specific",
        "Upgrade/Expansion correlated",
        "Teardown only",
    ):
        assert marker in nccl


def test_job_hang_evidence_playbook_has_executable_queries_and_patterns():
    hang = text(SKILLS / "system-evidence-diagnosis" / "sub-skills" / "job-hang" / "SKILL.md")
    for marker in (
        "list_prometheus_metrics",
        "gpu_utilization",
        "dcgm_power_usage",
        "node_xid_error",
        "ib_port_physical_state",
        "ib_port_rcv_errors",
        "ib_port_xmit_discards",
        "hw_rdma_tx_retx_pkts_total",
        "hw_rdma_tx_ack_timeout_total",
        "same-SKU idle/wait baseline",
        "HANG-COLLECTIVE",
        "HANG-NETWORK",
        "HANG-NODE",
    ):
        assert marker in hang


def test_loss_nan_reproduction_uses_torch_xray_with_strict_boundaries():
    log = text(SKILLS / "job-log-triage" / "sub-skills" / "loss-nan" / "SKILL.md")
    evidence = text(SKILLS / "system-evidence-diagnosis" / "sub-skills" / "loss-nan" / "SKILL.md")
    reproduction = text(SKILLS / "training-reproduction" / "sub-skills" / "loss-nan" / "SKILL.md")

    assert "last finite and first NaN" in log
    for marker in ("候选原因", "典型现象", "Log关键字与先后Pattern", "日志层判定与完整输出"):
        assert marker in log
    for marker in ("需要的Metric/证据", "典型时空Pattern / Rule", "支持、排除与证据不足判定", "最大隔离范围与立即动作"):
        assert marker in evidence
    for marker in ("进入条件与候选原因", "固定条件、唯一变化项与实验", "判定标准", "动作与证明边界"):
        assert marker in reproduction
    for major in ("job-log-triage", "system-evidence-diagnosis", "training-reproduction"):
        assert not (SKILLS / major / "sub-skills" / "loss-nan" / "runbooks").exists()
    for marker in (
        "is not stock PyTorch",
        "PrecisionDebugger",
        "summary target.h5",
        "compare reference.h5 target.h5",
        "begin_dump",
        "jprof --cpu_init",
        "pytest --detail_compare_path",
        "Module Input匹配",
        "Backward/Optimizer需其他数值检查",
        "Full-model/all-rank Dump",
    ):
        assert marker in reproduction


def test_tsdb_is_a_system_evidence_subskill():
    assert not (SKILLS / "tsdb-diagnosis").exists()
    tsdb = text(
        SKILLS
        / "system-evidence-diagnosis"
        / "sub-skills"
        / "tsdb-diagnosis"
        / "SKILL.md"
    )
    for rule in (
        "Verify identity before interpreting values",
        "Verify metric existence, coverage, and freshness",
        "Align training progress with wall clock",
        "Counters",
        "Return evidence, not a root-cause verdict",
    ):
        assert rule in tsdb


def test_minimal_reproduction_remains_a_thin_five_stage_calculator():
    root = SKILLS / "training-reproduction" / "sub-skills" / "minimal-reproduction"
    parent = text(root / "SKILL.md")
    refs = {path.name: text(path) for path in (root / "references").glob("*.md")}
    assert len(parent.splitlines()) < 180
    assert set(refs) == {
        "01-group-closure.md",
        "02-memory-planning.md",
        "03-topology-load-exposure.md",
        "04-resource-selection.md",
        "05-pilot-validation.md",
    }
    combined = "\n".join(refs.values())
    for marker in (
        "Compute closure, not a one-time union",
        "fit-with-headroom",
        "reproduce-memory-pressure",
        "REJECTED_MEMORY_ADJUSTMENTS",
        "G_required_exact",
        "G_required_mechanism_equivalent",
        "topology_matches",
        "Framework config/group build",
    ):
        assert marker in combined


def test_recovery_and_closeout_boundaries():
    recovery = text(SKILLS / "job-recovery" / "SKILL.md")
    closeout = text(SKILLS / "rca-closeout" / "SKILL.md")
    assert "Only `job-recovery` may perform production mutations" not in recovery
    assert "only training-incident stage allowed to mutate" in recovery
    assert "does not prove root cause" in recovery
    assert "job-log-triage failure-mode sub-skill" in closeout
    assert "system-evidence-diagnosis sub-skill" in closeout
    assert "training-reproduction sub-skill" in closeout
