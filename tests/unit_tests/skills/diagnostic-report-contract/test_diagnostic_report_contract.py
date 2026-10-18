"""Generic ordered-diagnosis guidance coverage for routed Incidara skills."""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
SKILLS = ROOT / "incidara_agents" / "skills"
DIAGNOSTIC = ("inspect-infra-issue", "job-check", "triage-nodes", "repair-nodes")
FIELDS = (
    "Primary mechanism",
    "Affected object",
    "Primary evidence",
    "Secondary/competing signals",
    "Action + verification/gaps",
)

# Twelve anonymized audit patterns, mapped to the workflow that must preserve them.
AUDIT_MATRIX = (
    ("triage-nodes", "object mapping", "GPU index", "PCI"),
    ("triage-nodes", "object mapping", "DIMM slot", "crosswalk"),
    ("repair-nodes", "object mapping", "PCIe switch", "retimer"),
    ("inspect-infra-issue", "precise evidence", "narrowest evidence-supported", "mechanism"),
    ("triage-nodes", "precise evidence", "precise mapping", "broader category"),
    ("inspect-infra-issue", "missing discriminator", "unsupported", "primary mechanism unknown"),
    ("triage-nodes", "missing discriminator", "null", "do not promote"),
    ("job-check", "missing discriminator", "missing discriminator", "visible competing symptom"),
    ("inspect-infra-issue", "finding scope", "finding under investigation", "independent workload cause"),
    ("triage-nodes", "temporal evidence", "historical", "current state"),
    ("repair-nodes", "temporal evidence", "recovered", "historical"),
    ("repair-nodes", "report ordering", "Action + verification/gaps", "follow the diagnosis"),
)


def text(name):
    return (SKILLS / name / "SKILL.md").read_text()


def test_all_six_routed_skill_frontmatters_are_valid_yaml():
    for name in (*DIAGNOSTIC, "scan-cluster", "automate-detection-pattern"):
        frontmatter = text(name).split("---", 2)[1]
        assert yaml.safe_load(frontmatter)["name"] == name


def test_diagnostic_responses_use_the_five_ordered_fields():
    for name in DIAGNOSTIC:
        skill = text(name)
        positions = [skill.index(field) for field in FIELDS]
        assert positions == sorted(positions), name
        assert "primary mechanism unknown" in skill.lower(), name
        assert "missing discriminator" in skill.lower(), name


def test_diagnostic_contract_reconciles_precise_object_mappings():
    for name in DIAGNOSTIC:
        skill = text(name).lower()
        assert "mapping" in skill or "mapped" in skill or "crosswalk" in skill, name
        assert "narrowest" in skill or "exact object" in skill, name
    for name in ("inspect-infra-issue", "triage-nodes", "repair-nodes"):
        skill = text(name).lower()
        assert "gpu index" in skill and "pci" in skill, name


def test_unavailable_evidence_stays_unknown_and_competing_signals_stay_secondary():
    for name in DIAGNOSTIC:
        skill = text(name).lower()
        assert "unsupported" in skill and "null" in skill, name
        assert "secondary/competing signals" in skill, name
        assert "do not promote" in skill, name
        assert "name the missing discriminator" in skill, name


def test_finding_target_is_not_conflated_with_workload_cause():
    skill = text("inspect-infra-issue").lower()
    assert "finding under investigation" in skill
    assert "independent workload cause" in skill
    assert "do not conflate" in skill


def test_historical_evidence_is_distinguished_from_recovered_current_state():
    for name in DIAGNOSTIC:
        skill = text(name).lower()
        assert "historical" in skill and "current" in skill, name
        assert "recovered" in skill, name


def test_triage_xids_require_reference_and_primary_secondary_classification():
    skill = text("triage-nodes")
    assert "When any Xid appears" in skill
    assert "MUST read `references/xid-reference.md`" in skill
    assert "Xid 48" in skill and "Xid 94/95" in skill
    assert "primary" in skill.lower() and "secondary" in skill.lower()


@pytest.mark.parametrize("name,category,first,second", AUDIT_MATRIX)
def test_each_anonymized_audit_pattern_has_routed_guidance(name, category, first, second):
    skill = text(name).lower()
    assert first.lower() in skill, (category, name, first)
    assert second.lower() in skill, (category, name, second)


def test_router_skills_preserve_exact_evidence_and_scope():
    scan = text("scan-cluster").lower()
    rules = text("automate-detection-pattern").lower()
    assert "raw evidence" in scan and "precise" in scan and "finding" in scan
    assert "underlying mechanism" in rules and "specific object" in rules


CHECKPOINT_FIELDS = (
    "Primary mechanism",
    "Affected object",
    "Strongest causal evidence/source",
    "Secondary/competing evidence",
    "Missing discriminator",
    "Confidence",
)


def test_triage_checkpoint_is_after_investigation_and_before_decision():
    skill = text("triage-nodes").lower()
    investigation = skill.index("#### step 2: investigate")
    checkpoint = skill.index("diagnosis checkpoint")
    gate = skill.index("pre-action diagnosis gate")
    decide = skill.index("#### step 3: decide")
    assert investigation < checkpoint < gate < decide


def test_checkpoint_has_all_fields_and_uses_existing_working_file():
    skill = text("triage-nodes")
    for field in CHECKPOINT_FIELDS:
        assert field in skill
    assert "triage_nodes_working.md" in skill
    assert "checkpoint update log" in skill.lower()


def test_checkpoint_updates_are_monotonic_and_require_cited_evidence():
    skill = text("triage-nodes").lower()
    assert "monotonic evidence state" in skill
    assert "new evidence narrows it or directly contradicts it" in skill
    assert "source" in skill and "why it is stronger or contradictory" in skill
    assert "generic status or reachability symptom" in skill
    assert "action result" in skill and "cannot replace" in skill


def test_action_gate_blocks_mutations_until_checkpoint_is_complete():
    skill = text("triage-nodes").lower()
    gate = skill.index("pre-action diagnosis gate")
    decide = skill.index("#### step 3: decide")
    execute = skill.index("### phase 3: execute")
    assert gate < decide < execute
    assert "no status transition, delegation, reset, ticket, platform fix, or revalidation" in skill
    assert "checkpoint is complete" in skill


def test_final_diagnosis_is_copied_from_latest_checkpoint():
    skill = text("triage-nodes").lower()
    assert "copy the latest checkpoint" in skill
    assert "do not reconstruct" in skill
    assert "final response" in skill


def test_specific_mechanism_survives_later_generic_status():
    skill = text("triage-nodes").lower()
    assert "later generic status" in skill and "reachability symptom" in skill
    assert "cannot replace a supported mechanism" in skill


def test_dimensional_object_survives_node_level_action():
    skill = text("triage-nodes").lower()
    assert "exact component mapping survives a node-level action" in skill
    assert "action detail must retain the checkpoint object" in skill


def test_missing_discriminator_blocks_competing_symptom_promotion():
    skill = text("triage-nodes").lower()
    assert "primary mechanism unknown" in skill
    assert "name the exact missing discriminator" in skill
    assert "do not promote a visible competing symptom" in skill


def test_peer_mapping_survives_action_and_verification():
    skill = text("triage-nodes").lower()
    assert "nvlink peer" in skill
    assert "preserve the exact peer in the affected object" in skill
    assert "action verification results cannot replace it" in skill


def test_inspection_and_repair_reports_use_checkpoint_before_action():
    inspect = text("inspect-infra-issue").lower()
    repair = text("repair-nodes").lower()
    assert inspect.index("diagnosis checkpoint") < inspect.index("### step 5: select action")
    assert "copy the checkpoint into the final report" in inspect
    assert repair.index("diagnosis checkpoint") < repair.index("manual approval checkpoint")
    assert "copy the latest diagnosis checkpoint into the repair report and ticket" in repair
