#!/usr/bin/env python3
"""Agent Feedback MCP Server.

Provides tools for the feedback loop:
  feedback_readonly: query_similar_cases, get_rule_stats, get_cases_by_hostname,
                     get_misclass_paths, get_problems
  feedback:          all readonly tools + insert_case, update_investigation,
                     update_case, insert/update problems

Separate from node-operations (platform DB) and agent-evidence (investigation artifacts).
"""

import argparse
import json
import logging
import os
from typing import Optional

from fastmcp import FastMCP

from agent_feedback.knowledge_db import (
    ensure_table,
    ensure_analysis_problems_table,
    ensure_rejected_proposals_table,
    clamp_unreconciled_limit,
    insert_case as _insert_case,
    update_investigation as _update_investigation,
    update_case as _update_case,
    query_similar_cases as _query_similar_cases,
    get_cases_by_hostname as _get_cases_by_hostname,
    get_rule_stats as _get_rule_stats,
    get_misclass_paths as _get_misclass_paths,
    insert_analysis_problem as _insert_analysis_problem,
    insert_rma_finding_reconciliation as _insert_rma_finding_reconciliation,
    record_finding_verdict_from_case as _record_finding_verdict_from_case,
    list_rule_feedback_examples as _list_rule_feedback_examples,
    list_unreconciled_rma_outcomes as _list_unreconciled_rma_outcomes,
    get_problems as _get_problems,
    update_problem as _update_problem,
    update_rma_finding_attribution as _update_rma_finding_attribution,
    get_problem_history as _get_problem_history,
    get_repeat_offenders as _get_repeat_offenders,
    insert_rejected_proposal as _insert_rejected_proposal,
    get_rejected_proposals as _get_rejected_proposals,
    ensure_agent_memory_table,
    insert_agent_memory as _insert_agent_memory,
    query_agent_memory as _query_agent_memory,
)

logger = logging.getLogger(__name__)

ROLE_TOOLS = {
    "feedback_readonly": {
        "query_similar_cases",
        "get_rule_stats",
        "get_cases_by_hostname",
        "get_misclass_paths",
        "get_problems",
        "get_problem_history",
        "get_repeat_offenders",
        "list_unreconciled_rma_outcomes",
        "list_rule_feedback_examples",
        "get_rejected_proposals",
        "query_memory",
        "record_memory",
    },
    "feedback": None,  # None = register all
}

PROCESS_ROLE = os.getenv("AGENT_FEEDBACK_ROLE", "feedback_readonly")

_host = os.environ.get("MCP_HOST", "127.0.0.1")
_port = int(os.environ.get("AGENT_FEEDBACK_PORT", "8091"))

mcp = FastMCP("agent-feedback")


def _raise_tool_error(exc: Exception) -> None:
    raise RuntimeError(str(exc)) from exc


def _domain_result(reason: str, message: str, **details) -> str:
    payload = {"ok": False, "reason": reason, "message": message}
    payload.update(details)
    return json.dumps(payload, default=str)


def _register(tool_name: str) -> bool:
    """Check if a tool should be registered for the current role."""
    allowed = ROLE_TOOLS.get(PROCESS_ROLE)
    if allowed is None:
        return True  # None = all tools
    return tool_name in allowed

ensure_table()
ensure_analysis_problems_table()
ensure_rejected_proposals_table()
ensure_agent_memory_table()
logger.info("agent-feedback MCP server started")


def _write_allowed() -> bool:
    return PROCESS_ROLE == "feedback"


# FastMCP decorators register tools at import time in this module, before main()
# parses --role. Write tools therefore enforce role safety at runtime.
def _write_denied_response(action: str) -> str:
    logger.error(f"{action} denied: requires feedback role, current role={PROCESS_ROLE}")
    return _domain_result(
        "write_denied",
        "Write tools require feedback role",
        **{action: False},
        required_role="feedback",
        current_role=PROCESS_ROLE,
    )


# ---------------------------------------------------------------------------
# Tools — feedback_readonly
# ---------------------------------------------------------------------------

@mcp.tool()
def query_similar_cases(
    classification: str = "",
    alert_names: str = "",
    limit: int = 10,
) -> str:
    """Find past cases with similar symptoms. Ranked by alert overlap.

    Call before classifying a node to see how similar cases turned out.

    Args:
        classification: Filter by our_classification.
                        Empty = match all.
        alert_names: Comma-separated alert names to rank by overlap.
        limit: Max cases to return (default 10).
    """
    try:
        names = [n.strip() for n in alert_names.split(",") if n.strip()] if alert_names else []
        rows = _query_similar_cases(
            classification=classification,
            alert_names=names,
            limit=limit,
        )
        return json.dumps({"cases": rows, "count": len(rows)})
    except Exception as e:
        logger.error(f"query_similar_cases failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def get_rule_stats() -> str:
    """Get accuracy stats per fault type — live query from case_memory.

    Returns per-fault-type breakdown: total, correct, wrong, accuracy_pct,
    misclassified, nff_reliable, nff_unreliable, maintenance_fix, config_task.
    Accuracy = (REPAIR_CONFIRMED + MAINTENANCE_FIX + CONFIG_TASK) / total.
    RecallForUpgrade and test excluded.
    """
    try:
        stats = _get_rule_stats()
        return json.dumps({"stats": stats, "count": len(stats)})
    except Exception as e:
        logger.error(f"get_rule_stats failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def get_misclass_paths() -> str:
    """Get misclassification paths from case_memory.

    Returns rows showing: fault_type → vendor_component with count and hostnames.
    Only cases where vendor_verdict = 'MISCLASSIFIED'.
    E.g. NodeCrash → IB_NIC (1 case): we said NodeCrash but vendor fixed IB_NIC.
    """
    try:
        paths = _get_misclass_paths()
        return json.dumps({"paths": paths, "count": len(paths)})
    except Exception as e:
        logger.error(f"get_misclass_paths failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def get_cases_by_hostname(hostname: str) -> str:
    """Get all case_memory rows for a hostname, newest first.

    Args:
        hostname: Node hostname.
    """
    try:
        cases = _get_cases_by_hostname(hostname)
        return json.dumps({"cases": cases, "count": len(cases)})
    except Exception as e:
        logger.error(f"get_cases_by_hostname failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def get_problems(status: str = "", fault_type: str = "") -> str:
    """Get analysis problems, optionally filtered by status and/or fault_type.

    Without filters, returns all problems with their latest status (for dedup and overview).
    Status values: open, patch_created, blocked, wont_fix, resolved, unresolved, closed.

    Args:
        status: Filter by status. Empty = all.
        fault_type: Filter by fault_type. Empty = all.
    """
    try:
        problems = _get_problems(status=status, fault_type=fault_type)
        return json.dumps({"problems": problems, "count": len(problems)})
    except Exception as e:
        logger.error(f"get_problems failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def list_unreconciled_rma_outcomes(limit: int = 100) -> str:
    """List completed RMA outcomes not yet matched to detection findings.

    Args:
        limit: Max cases to return, ordered by oldest RMA completion first.
    """
    try:
        limit = clamp_unreconciled_limit(limit)
        rows = _list_unreconciled_rma_outcomes(limit=limit)
        return json.dumps({"cases": rows, "count": len(rows)})
    except Exception as e:
        logger.error(f"list_unreconciled_rma_outcomes failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def list_rule_feedback_examples(rule_id: str, feedback_label: str = "") -> str:
    """List detection-attributed labeled finding feedback for a rule.

    Args:
        rule_id: Detection rule ID.
        feedback_label: Optional positive | negative filter.
    """
    try:
        rows = _list_rule_feedback_examples(rule_id=rule_id, feedback_label=feedback_label)
        return json.dumps({"rule_id": rule_id, "examples": rows, "count": len(rows)})
    except Exception as e:
        logger.error(f"list_rule_feedback_examples failed: {e}")
        _raise_tool_error(e)


# ---------------------------------------------------------------------------
# Tools — feedback (write)
# ---------------------------------------------------------------------------

@mcp.tool()
def insert_case(
    hostname: str,
    our_classification: str,
    our_reason: str = "",
    vendor_verdict: str = "",
    vendor_repair_raw: str = "",
    vendor_repair_type: str = "",
    vendor_component: str = "",
    vendor_confidence: str = "MEDIUM",
    vendor_answer_quality: str = "MODERATE",
    our_evidence: str = "{}",
    our_investigation: str = "{}",
    rma_ticket_id: str = "",
    onboard_id: int = 0,
    rma_completed_at: str = "",
    claude_session_id: str = "[]",
) -> str:
    """Insert a case into case_memory. collected_by auto-set from AGENT_NAME.

    Args:
        hostname: Node hostname.
        our_classification: e.g. "triaged_hardware / GPUFault".
        our_reason: e.g. "ECC errors on GPU 3".
        vendor_verdict: REPAIR_CONFIRMED | MISCLASSIFIED | MAINTENANCE_FIX | NO_FAULT_FOUND | CONFIG_TASK.
            (VENDOR_MISS is retroactive only — set when node recurs after NO_FAULT_FOUND)
        vendor_repair_raw: Raw vendor repair description.
        vendor_repair_type: Normalized type.
        vendor_component: GPU | NVSwitch | DIMM | Motherboard | PSU | IB_Cable | ...
        vendor_confidence: HIGH | MEDIUM | LOW | UNKNOWN — confidence in our classification.
        vendor_answer_quality: DETAILED | MODERATE | MINIMAL | NONE — how thorough the vendor's
            response is. DETAILED = swap test with serials; MODERATE = physical action with
            reasoning; MINIMAL = generic check; NONE = empty or boilerplate.
        our_evidence: JSON string with {alert_types, sku} from platform DB.
        rma_ticket_id: CompleteRMA ticket ID.
        rma_completed_at: ISO timestamp when vendor finished repair.
        claude_session_id: JSON array of sessions from query_completed_rmas.
            Pass the full `sessions` array from the query result — it contains both repair
            and triage session IDs with agent info. Do NOT pass a single session ID string.
    """
    try:
        if not _write_allowed():
            return _write_denied_response("inserted")
        evidence = {}
        if our_evidence:
            try:
                evidence = json.loads(our_evidence) if isinstance(our_evidence, str) else our_evidence
            except (json.JSONDecodeError, TypeError):
                pass

        investigation = {}
        if our_investigation:
            try:
                investigation = json.loads(our_investigation) if isinstance(our_investigation, str) else our_investigation
            except (json.JSONDecodeError, TypeError):
                pass

        row_id = _insert_case(
            hostname=hostname,
            our_classification=our_classification,
            our_reason=our_reason,
            vendor_verdict=vendor_verdict,
            vendor_repair_raw=vendor_repair_raw,
            vendor_repair_type=vendor_repair_type,
            vendor_component=vendor_component,
            vendor_confidence=vendor_confidence,
            vendor_answer_quality=vendor_answer_quality,
            our_evidence=evidence,
            our_investigation=investigation,
            rma_ticket_id=rma_ticket_id,
            onboard_id=onboard_id,
            rma_completed_at=rma_completed_at,
            claude_session_id=claude_session_id,
        )
        return json.dumps({"inserted": True, "id": row_id})
    except Exception as e:
        logger.error(f"insert_case failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def record_finding_verdict_from_case(
    case_id: int,
    hostname: str,
    vendor_verdict: str,
    vendor_answer_quality: str = "",
    rma_completed_at: str = "",
) -> str:
    """After inserting a case, backfill verdict on unjudged patrol findings
    for the same hostname. Closes the feedback loop: RMA outcome → detection rule verdict.

    Called by collect-rma-cases after each insert_case.
    Skips NO_FAULT_FOUND with unreliable vendor (MINIMAL/NONE quality).

    Args:
        case_id: The case_memory row id just inserted.
        hostname: Node hostname to match against patrol_findings.target_id.
        vendor_verdict: REPAIR_CONFIRMED, MISCLASSIFIED, MAINTENANCE_FIX, NO_FAULT_FOUND, CONFIG_TASK.
        vendor_answer_quality: DETAILED, MODERATE, MINIMAL, or NONE.
        rma_completed_at: ISO timestamp — only match findings detected before this date.
    """
    try:
        result = _record_finding_verdict_from_case(
            case_id=case_id,
            hostname=hostname,
            vendor_verdict=vendor_verdict,
            vendor_answer_quality=vendor_answer_quality,
            rma_completed_at=rma_completed_at or None,
        )
        return json.dumps(result)
    except Exception as e:
        logger.error(f"record_finding_verdict_from_case failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def update_investigation(case_id: int, our_investigation: str = "{}") -> str:
    """Update our_investigation field for an existing case (P2).

    Args:
        case_id: case_memory row ID.
        our_investigation: JSON string with {checked, not_checked, tool_calls}.
    """
    try:
        if not _write_allowed():
            return _write_denied_response("updated")
        inv = {}
        try:
            inv = json.loads(our_investigation) if isinstance(our_investigation, str) else our_investigation
        except (json.JSONDecodeError, TypeError):
            pass

        ok = _update_investigation(case_id, inv)
        return json.dumps({"updated": ok})
    except Exception as e:
        logger.error(f"update_investigation_tool failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def update_case(
    case_id: int,
    vendor_verdict: str = None,
    vendor_confidence: str = None,
    vendor_answer_quality: str = None,
    vendor_repair_type: str = None,
    vendor_component: str = None,
    vendor_repair_raw: str = None,
    our_classification: str = None,
    our_reason: str = None,
    our_evidence: str = None,
    our_investigation: str = None,
) -> str:
    """Update any combination of fields on an existing case_memory row.
    Only non-None fields are updated. Used by analyze-rma-cases and case-diagnosis
    to correct verdicts, confidence, or classification after deeper analysis.

    Args:
        case_id: case_memory row ID.
        vendor_verdict: REPAIR_CONFIRMED, MISCLASSIFIED, MAINTENANCE_FIX, NO_FAULT_FOUND, CONFIG_TASK.
        vendor_confidence: HIGH, MEDIUM, LOW.
        vendor_answer_quality: DETAILED, MODERATE, MINIMAL, NONE.
        vendor_repair_type: gpu_replace, motherboard_replace, etc.
        vendor_component: GPU, Motherboard, NVMe, etc.
        vendor_repair_raw: Raw vendor repair text.
        our_classification: Our classification string.
        our_reason: Our reason/investigation summary.
        our_evidence: JSON string with evidence dict.
        our_investigation: JSON string with investigation dict.
    """
    try:
        if not _write_allowed():
            return _write_denied_response("updated")
        evidence = None
        if our_evidence is not None:
            evidence = json.loads(our_evidence) if isinstance(our_evidence, str) else our_evidence
        investigation = None
        if our_investigation is not None:
            investigation = json.loads(our_investigation) if isinstance(our_investigation, str) else our_investigation
        ok = _update_case(
            case_id=case_id,
            vendor_verdict=vendor_verdict,
            vendor_confidence=vendor_confidence,
            vendor_answer_quality=vendor_answer_quality,
            vendor_repair_type=vendor_repair_type,
            vendor_component=vendor_component,
            vendor_repair_raw=vendor_repair_raw,
            our_classification=our_classification,
            our_reason=our_reason,
            our_evidence=evidence,
            our_investigation=investigation,
        )
        return json.dumps({"updated": ok})
    except Exception as e:
        logger.error(f"update_case_tool failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def insert_rma_finding_reconciliation(
    case_id: int,
    finding_id: int,
    rule_id: str,
    repair_outcome: str,
    feedback_label: str = "",
    expected_behavior: Optional[dict] = None,
    label_source: str = "auto",
) -> str:
    """Insert or update the matched RMA outcome for one detection finding.

    Args:
        case_id: case_memory row ID.
        finding_id: Detection finding ID.
        rule_id: Detection rule that produced the finding.
        repair_outcome: REPAIR_CONFIRMED | NO_FAULT_FOUND | MISCLASSIFIED |
            MAINTENANCE_FIX | CONFIG_TASK.
    """
    try:
        if not _write_allowed():
            return _write_denied_response("inserted")
        ok = _insert_rma_finding_reconciliation(
            case_id=case_id,
            finding_id=finding_id,
            rule_id=rule_id,
            repair_outcome=repair_outcome,
            feedback_label=feedback_label,
            expected_behavior=expected_behavior,
            label_source=label_source,
        )
        return json.dumps({"inserted": ok})
    except Exception as e:
        logger.error(f"insert_rma_finding_reconciliation failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def update_rma_finding_attribution(
    case_id: int,
    finding_id: int,
    attribution: str,
    attribution_confidence: str,
    fix_route: str,
    feedback_label: str = "",
    expected_behavior: Optional[dict] = None,
    label_source: str = "case_diagnosis",
) -> str:
    """Update case-diagnosis attribution for a reconciled RMA finding.

    Args:
        case_id: case_memory row ID.
        finding_id: Detection finding ID.
        attribution: detection | triage | inspect | repair | automation |
            vendor_uncertain | unknown.
        attribution_confidence: high | medium | low.
        fix_route: rule_code | triage_skill | inspect_skill | repair_skill |
            automation_skill | attention | observe.
    """
    try:
        if not _write_allowed():
            return _write_denied_response("updated")
        ok = _update_rma_finding_attribution(
            case_id=case_id,
            finding_id=finding_id,
            attribution=attribution,
            attribution_confidence=attribution_confidence,
            fix_route=fix_route,
            feedback_label=feedback_label,
            expected_behavior=expected_behavior,
            label_source=label_source,
        )
        return json.dumps({"updated": ok})
    except Exception as e:
        logger.error(f"update_rma_finding_attribution failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def insert_analysis_problem_tool(
    prompt: str,
    case_ids: list,
    title: str = "",
    fault_type: str = "",
) -> str:
    """Insert a problem found by analyze-rma-cases for cross-session tracking.

    Args:
        prompt: Full delegation prompt (facts, mismatch summary, what to investigate).
        case_ids: List of case_memory IDs affected by this problem.
        title: Short title (e.g. "NodeCrash NFF — 15/22 cases no fault found").
        fault_type: Fault type (e.g. "NodeCrash", "ModelPerformanceDegradation").
    """
    try:
        if not _write_allowed():
            return _write_denied_response("inserted")
        row_id = _insert_analysis_problem(
            prompt=prompt,
            case_ids=case_ids,
            title=title,
            fault_type=fault_type,
        )
        return json.dumps({"inserted": True, "id": row_id})
    except Exception as e:
        logger.error(f"insert_analysis_problem failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def update_problem_tool(
    problem_id: int,
    status: str = "",
    diagnosis: str = "",
    patch_summary: str = "",
    patch_commit: str = "",
    monitor_expectation: str = "",
    pr_url: str = "",
    add_case_ids: list = None,
) -> str:
    """Update an analysis problem — add diagnosis results, patch info, or change status.

    Status values: open (needs work), patch_created (branch pushed, awaiting deploy), 
    monitoring (deployed, watching for results), resolved (confirmed fixed), 
    unresolved (fix attempted but didn't work), blocked (can't fix).

    Args:
        problem_id: analysis_problems problem_id or row id (tries problem_id column first, then id column).
        status: New status (optional — only update if provided).
        diagnosis: JSON string with diagnosis findings from case-diagnosis (optional).
        patch_summary: What was changed in skill files (optional).
        patch_commit: Git commit hash or branch name (optional).
        monitor_expectation: What to watch for in next feedback cycle (optional).
        pr_url: URL of the created PR on codeup (optional).
        add_case_ids: New case IDs to merge into existing problem (optional, no duplicates).
    """
    try:
        if not _write_allowed():
            return _write_denied_response("updated")
        diag = None
        if diagnosis:
            try:
                diag = json.loads(diagnosis) if isinstance(diagnosis, str) else diagnosis
            except (json.JSONDecodeError, TypeError):
                pass

        ok = _update_problem(
            problem_id=problem_id,
            status=status or None,
            diagnosis=diag,
            patch_summary=patch_summary,
            patch_commit=patch_commit,
            monitor_expectation=monitor_expectation,
            pr_url=pr_url,
            add_case_ids=add_case_ids,
        )
        return json.dumps({"updated": ok})
    except Exception as e:
        logger.error(f"update_problem failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def find_session_by_hostname(hostname: str, before_ts: str = "") -> str:
    """Find Claude SDK session IDs for a node by searching session metas.

    Scans /mnt/sessions/<agent>/meta.json files for sessions whose title
    contains the hostname. Returns gateway_session_id, claudeSessionId,
    agent, title, and createdAt for each match.

    If before_ts is given (ISO timestamp), only returns sessions created
    before that timestamp — useful to find the session that led to a specific RMA.

    Priority: repair → triage → recycler (most recent first within each agent).

    Args:
        hostname: Node hostname to search for (e.g. "h200-000705").
        before_ts: Only return sessions created before this ISO timestamp (optional).
    """
    import os
    from datetime import datetime
    results = []
    short = hostname.split("-")[-1] if "-" in hostname else hostname
    cutoff = None
    if before_ts:
        try:
            cutoff = datetime.fromisoformat(before_ts.replace("Z", "+00:00"))
        except ValueError:
            pass
    for agent in ["repair", "triage", "recycler"]:
        base = f"/mnt/sessions/{agent}"
        if not os.path.isdir(base):
            continue
        for sess_dir in os.listdir(base):
            meta_path = os.path.join(base, sess_dir, "meta.json")
            if not os.path.isfile(meta_path):
                continue
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                title = meta.get("title", "")
                if short not in title and hostname not in title:
                    continue
                created = meta.get("createdAt", "")
                if cutoff and created:
                    try:
                        ct = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        if ct > cutoff:
                            continue
                    except ValueError:
                        pass
                results.append({
                    "agent": agent,
                    "gateway_session_id": meta.get("id", sess_dir),
                    "claude_session_id": meta.get("claudeSessionId", ""),
                    "title": title,
                    "created_at": created,
                })
            except (json.JSONDecodeError, OSError):
                continue
    # Sort most recent first
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return json.dumps({"hostname": hostname, "sessions": results, "count": len(results)})


@mcp.tool()
def get_repeat_offenders_tool(min_cases: int = 2) -> str:
    """Find hostnames with multiple RMA cases across any fault types.

    Returns hostnames sorted by case count (descending). For each hostname,
    shows case count, distinct classifications, distinct verdicts, and case IDs.
    Use this to find cross-fault-type repeat offenders — nodes that keep coming
    back with different fault codes but likely the same root cause.

    Args:
        min_cases: Minimum number of cases per hostname (default 2).
    """
    try:
        results = _get_repeat_offenders(min_cases=min_cases)
        return json.dumps({"repeat_offenders": results, "count": len(results)})
    except Exception as e:
        logger.error(f"get_repeat_offenders failed: {e}")
        _raise_tool_error(e)


@mcp.tool()
def get_problem_history_tool(problem_id: int) -> str:
    """Return the full history of a problem — all status transitions with timestamps.

    Each row is a snapshot at a point in time. The last row is the current state.
    Use this to find when a problem entered monitoring (analyze-rma-cases uses this
    to split cases into before-fix vs after-fix).

    Args:
        problem_id: The problem ID to look up.
    """
    try:
        history = _get_problem_history(problem_id)
        return json.dumps({"problem_id": problem_id, "history": history, "count": len(history)})
    except Exception as e:
        logger.error(f"get_problem_history failed: {e}")
        _raise_tool_error(e)


# ---- Rejected Proposals (rejected-edit buffer) ----

if _register("insert_rejected_proposal"):
    @mcp.tool()
    def insert_rejected_proposal(
        skill_file: str,
        edit_type: str,
        edit_summary: str,
        outcome: str,
        edit_detail: str = "",
        rationale: str = "",
        before_metric: str = "",
        after_metric: str = "",
        problem_id: int = 0,
        deployed_at: str = "",
        note: str = "",
    ) -> str:
        """Record a skill change that was deployed but didn't improve outcomes.

        This is the rejected-edit buffer: when a deployed skill edit didn't help
        (or hurt), record it so the feedback agent doesn't re-propose the same
        approach in a future cycle.

        Args:
            skill_file: Which skill file was changed (e.g. "categorization-rules.md").
            edit_type: Type of edit: "add", "delete", or "replace".
            edit_summary: One-line description of what the change did.
            outcome: What happened: "no_improvement" (accuracy stayed same),
                     "regression" (accuracy dropped), "reverted" (rolled back),
                     "superseded" (replaced by better change).
            edit_detail: Full diff or content of the change (optional).
            rationale: Why the agent thought this change would help (optional).
            before_metric: JSON string — metrics before the change, e.g. '{"accuracy": 0.85, "cases": 42}'.
            after_metric: JSON string — metrics after the change, e.g. '{"accuracy": 0.82, "cases": 45}'.
            problem_id: Associated problem ID if any (0 = none).
            deployed_at: When the change was originally deployed (ISO format, optional).
            note: What went wrong, why it didn't help.
        """
        if not _write_allowed():
            return _write_denied_response("insert_rejected_proposal")
        try:
            bm = json.loads(before_metric) if before_metric else {}
            am = json.loads(after_metric) if after_metric else {}
            pid = problem_id if problem_id else None
            da = deployed_at if deployed_at else None
            row_id = _insert_rejected_proposal(
                skill_file=skill_file, edit_type=edit_type, edit_summary=edit_summary,
                outcome=outcome, edit_detail=edit_detail, rationale=rationale,
                before_metric=bm, after_metric=am, problem_id=pid,
                deployed_at=da, note=note,
            )
            return json.dumps({"id": row_id, "recorded": True})
        except Exception as e:
            logger.error(f"insert_rejected_proposal failed: {e}")
            _raise_tool_error(e)


if _register("get_rejected_proposals"):
    @mcp.tool()
    def get_rejected_proposals(skill_file: str = "", limit: int = 20) -> str:
        """Query rejected skill change proposals. Returns most recent first.

        Use this BEFORE proposing a skill edit to check if something similar was
        already tried and failed. Avoid re-proposing bad edits.

        Args:
            skill_file: Filter to a specific skill file (e.g. "categorization-rules.md"). Empty = all files.
            limit: Max proposals to return (default 20).
        """
        try:
            rows = _get_rejected_proposals(skill_file=skill_file, limit=limit)
            return json.dumps({"proposals": rows, "count": len(rows)})
        except Exception as e:
            logger.error(f"get_rejected_proposals failed: {e}")
            _raise_tool_error(e)


if _register("record_memory"):
    @mcp.tool()
    def record_memory(
        target: str,
        domain: str,
        action: str,
        outcome: str,
        reason: str = "",
        insight: str = "",
        detail: str = "",
        before_metric: str = "",
        after_metric: str = "",
    ) -> str:
        """Record an action attempt and its outcome for cross-session agent memory.

        Use this to remember what was tried, whether it worked, and (optionally)
        the deeper reusable insight. Before proposing a change, always check
        query_memory first — if something similar was tried and failed, try a
        different approach.

        Args:
            target: What was changed. Convention: "rule:<rule_id>", "collector:<name>",
                    "skill:<filename>", "repair:<approach>", "workflow:<name>",
                    "problem:<id>" (links to analysis_problems).
            domain: Which area: "detection", "feedback", "repair", "triage", etc.
            action: What was done (one-line description).
            outcome: "improvement", "no_improvement", or "regression".
            reason: Why it didn't work (or why it did). Optional.
            insight: Deeper reusable lesson. Optional — only when there's a cross-target
                     pattern to capture. Example: "SSH grep on syslog has no time
                     filtering — always use node_logs source for log reading".
            detail: Full content of the change (diff, code, config). Optional.
            before_metric: JSON string — metrics before (optional).
            after_metric: JSON string — metrics after (optional).
        """
        # No _write_allowed() check — recording memory is safe (append-only) and
        # should be available to all agents, not just the feedback write role.
        try:
            bm = json.loads(before_metric) if before_metric else {}
            am = json.loads(after_metric) if after_metric else {}
            row_id = _insert_agent_memory(
                target=target, domain=domain, action=action, outcome=outcome,
                reason=reason, insight=insight, detail=detail,
                before_metric=bm, after_metric=am,
            )
            return json.dumps({"id": row_id, "recorded": True})
        except Exception as e:
            logger.error(f"record_memory failed: {e}")
            _raise_tool_error(e)


if _register("query_memory"):
    @mcp.tool()
    def query_memory(
        target: str = "",
        domain: str = "",
        with_insight_only: bool = False,
        limit: int = 20,
    ) -> str:
        """Query agent memory — what's been tried and what happened.

        Always call this BEFORE proposing a change to check if something similar
        was already tried. Filter by target (specific rule/collector/skill) or
        domain (all detection, all repair, etc.). Use with_insight_only=True to
        find cross-target reusable lessons.

        Args:
            target: Filter to a specific target (e.g. "rule:ib_link_flapping_repeat_v1"). Empty = all.
            domain: Filter to a domain (e.g. "detection", "repair"). Empty = all.
            with_insight_only: Only return rows with insights (default False).
            limit: Max rows to return (default 20).
        """
        try:
            rows = _query_agent_memory(
                target=target, domain=domain,
                with_insight_only=with_insight_only, limit=limit,
            )
            return json.dumps({"memories": rows, "count": len(rows)})
        except Exception as e:
            logger.error(f"query_memory failed: {e}")
            _raise_tool_error(e)


def main():
    parser = argparse.ArgumentParser(description="Agent Feedback MCP Server")
    parser.add_argument("--role", default="feedback_readonly",
                        choices=list(ROLE_TOOLS.keys()))
    parser.add_argument("--transport", default="http",
                        choices=["stdio", "sse", "http"])
    args = parser.parse_args()

    global PROCESS_ROLE
    PROCESS_ROLE = args.role

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    logger.info(f"agent-feedback starting (role={args.role}, transport={args.transport})")
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, host=_host, port=_port)


if __name__ == "__main__":
    main()
