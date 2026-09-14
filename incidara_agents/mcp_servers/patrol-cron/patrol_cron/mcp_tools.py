"""MCP tool functions for patrol rule/collector CRUD.

These are the pure logic functions. They can be registered with any MCP server
via FastMCP's @mcp.tool() decorator. Kept separate so they're testable
without spinning up an MCP server.

Usage in an MCP server:
    from patrol_cron.mcp_tools import register_patrol_tools
    register_patrol_tools(mcp)
"""

from __future__ import annotations
from patrol_cron.db import _t

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

EVIDENCE_DB_URL = os.environ.get(
    "EVIDENCE_DB_URL",
    "",
)

VALID_STAGES = {"log_only", "create_task", "submit_alert", "auto_cordon"}
VALID_VERDICTS = {"confirmed", "rejected", "rejected_nff"}
VALID_REPAIR_OUTCOMES = {
    "REPAIR_CONFIRMED",
    "NO_FAULT_FOUND",
    "MISCLASSIFIED",
    "MAINTENANCE_FIX",
    "CONFIG_TASK",
}
VALID_REPLAY_SOURCES = {
    "rma_bad_feedback",
    "confirmed_counterexample",
    "manual_counterexample",
}


def _conn():
    return psycopg2.connect(EVIDENCE_DB_URL)


def _raise_tool_error(exc: Exception) -> None:
    raise RuntimeError(str(exc)) from exc


def _input_error(message: str) -> None:
    raise ValueError(message)


def _domain_result(reason: str, message: str, **details) -> str:
    payload = {"ok": False, "reason": reason, "message": message}
    payload.update(details)
    return json.dumps(payload, default=str)


def _not_found(entity: str, identifier) -> str:
    return _domain_result("not_found", f"{entity} {identifier} not found", entity=entity, id=identifier)


# ── Collector CRUD ────────────────────────────────────────────────────────

def create_collector(
    name: str,
    schedule_sec: int,
    target_type: str,
    sources: list,
    created_by: str,
    description: str = "",
    target_filter: Optional[dict] = None,
) -> str:
    """Create a new collector definition.

    Args:
        name: Unique collector name (e.g. "switch_health").
        schedule_sec: How often to run in seconds.
        target_type: "node" | "switch" | "job" | "query_result".
        sources: List of source configs [{type, name, config}, ...].
        created_by: Who created this (e.g. "agent", "human").
        description: Optional description.
        target_filter: Optional filter (e.g. {hostname_pattern, schedulable}).
    """
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO collectors
                   (name, description, schedule_sec, target_type, target_filter, sources, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (name) DO UPDATE SET
                     description = EXCLUDED.description,
                     schedule_sec = EXCLUDED.schedule_sec,
                     target_type = EXCLUDED.target_type,
                     target_filter = EXCLUDED.target_filter,
                     sources = EXCLUDED.sources,
                     updated_at = NOW()""",
                (
                    name, description, schedule_sec, target_type,
                    json.dumps(target_filter or {}),
                    json.dumps(sources),
                    created_by,
                ),
            )
            conn.commit()
        return json.dumps({"status": "created", "name": name})
    except Exception as e:
        _raise_tool_error(e)


def _source_summary(sources: list[dict]) -> list[dict]:
    """Return source configs as-is from the database."""
    return sources


def list_collectors(
    has_rules: Optional[bool] = None,
    rule_id: Optional[str] = None,
) -> str:
    """List collectors with their bound rule counts.

    Args:
        has_rules: True = only with rules, False = only without rules, None = all.
        rule_id: Filter to the collector that this rule binds to.
    """
    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            base = f"""SELECT c.name, c.description, c.schedule_sec, c.target_type,
                          c.enabled, c.last_run, c.created_at, c.updated_at, c.created_by,
                          c.sources, c.target_filter,
                          count(r.rule_id) AS rule_count,
                          count(r.rule_id) FILTER (WHERE r.enabled) AS active_rules,
                          array_agg(r.rule_id) FILTER (WHERE r.rule_id IS NOT NULL) AS rule_ids
                   FROM {_t('collectors')} c
                   LEFT JOIN {_t('patrol_rules')} r ON r.binds_to = c.name"""
            if rule_id:
                cur.execute(
                    base + f" WHERE c.name = (SELECT binds_to FROM {_t('patrol_rules')} WHERE rule_id = %s)"
                    " GROUP BY c.name, c.target_filter, c.created_at, c.updated_at, c.created_by ORDER BY c.name",
                    (rule_id,),
                )
            else:
                cur.execute(base + " GROUP BY c.name, c.target_filter, c.created_at, c.updated_at, c.created_by ORDER BY c.name")
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                for k in ("last_run", "created_at"):
                    if r.get(k):
                        r[k] = str(r[k])
                r["rule_ids"] = r.get("rule_ids") or []
                # Parse sources JSON → list of {name, type, summary}
                sources = r.pop("sources", None)
                if sources:
                    try:
                        src_list = json.loads(sources) if isinstance(sources, str) else sources
                        r["sources"] = _source_summary(src_list)
                    except (json.JSONDecodeError, TypeError):
                        r["sources"] = []
            if has_rules is True:
                rows = [r for r in rows if r["rule_count"] > 0]
            elif has_rules is False:
                rows = [r for r in rows if r["rule_count"] == 0]
            return json.dumps({"collectors": rows}, default=str)
    except Exception as e:
        _raise_tool_error(e)


def get_collector(name: str) -> str:
    """Get full details of a single collector, including complete source configs.

    Unlike list_collectors which summarizes sources, this returns the raw source
    configuration with all details (commands, patterns, concurrency, timeouts, etc.).

    Args:
        name: Collector name (e.g. "large_job_failure", "switch_health_ib")
    """
    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"""SELECT c.name, c.description, c.schedule_sec, c.target_type,
                           c.enabled, c.last_run, c.created_at, c.sources,
                           c.target_filter,
                           count(r.rule_id) AS rule_count,
                           array_agg(r.rule_id) FILTER (WHERE r.rule_id IS NOT NULL) AS rule_ids
                    FROM {_t('collectors')} c
                    LEFT JOIN {_t('patrol_rules')} r ON r.binds_to = c.name
                    WHERE c.name = %s
                    GROUP BY c.name, c.description, c.schedule_sec, c.target_type,
                             c.enabled, c.last_run, c.created_at, c.sources, c.target_filter""",
                (name,),
            )
            row = cur.fetchone()
            if not row:
                return _not_found("Collector", name)
            r = dict(row)
            sources = r.pop("sources", None)
            if sources:
                r["sources"] = json.loads(sources) if isinstance(sources, str) else sources
            for ts_field in ("last_run", "created_at", "updated_at"):
                if r.get(ts_field):
                    r[ts_field] = str(r[ts_field])
            return json.dumps(r, default=str)
    except Exception as e:
        _raise_tool_error(e)


def get_collector_health(name: Optional[str] = None) -> str:
    """Get collector health: last_run, time since last run, recent check log stats.

    Args:
        name: Optional collector name. If omitted, returns all collectors.
    """
    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if name:
                cur.execute(
                    f"""SELECT c.name, c.schedule_sec, c.enabled, c.last_run,
                              EXTRACT(EPOCH FROM (NOW() - c.last_run))::int AS secs_since_run,
                              (SELECT count(DISTINCT l.target_id) FROM {_t('patrol_findings')} l
                               WHERE l.rule_id IN (SELECT rule_id FROM {_t('patrol_rules')} WHERE binds_to = c.name)
                                 AND l.detected_at > NOW() - interval '3 days') AS findings_3d
                       FROM {_t('collectors')} c WHERE c.name = %s""",
                    (name,),
                )
            else:
                cur.execute(
                    f"""SELECT c.name, c.schedule_sec, c.enabled, c.last_run,
                              EXTRACT(EPOCH FROM (NOW() - c.last_run))::int AS secs_since_run,
                              (SELECT count(DISTINCT l.target_id) FROM {_t('patrol_findings')} l
                               WHERE l.rule_id IN (SELECT rule_id FROM {_t('patrol_rules')} WHERE binds_to = c.name)
                                 AND l.detected_at > NOW() - interval '3 days') AS findings_3d
                       FROM {_t('collectors')} c ORDER BY c.name"""
                )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                if r.get("last_run"):
                    r["last_run"] = str(r["last_run"])
                # Flag if overdue (hasn't run in 2x schedule)
                secs = r.get("secs_since_run")
                r["overdue"] = secs is not None and secs > r["schedule_sec"] * 2
            return json.dumps({"collectors": rows}, default=str)
    except Exception as e:
        _raise_tool_error(e)


def _compute_collector_hash(name, sources, target_filter, schedule_sec, enabled):
    """Compute collector config hash — must match run_collector_job.py."""
    import hashlib as _hl
    raw = (
        str(name or "")
        + json.dumps(sources or [], sort_keys=True)
        + json.dumps(target_filter or {}, sort_keys=True)
        + str(schedule_sec or "")
        + str(enabled or "")
    )
    return _hl.md5(raw.encode()).hexdigest()


def update_collector(
    name: str,
    description: Optional[str] = None,
    schedule_sec: Optional[int] = None,
    target_type: Optional[str] = None,
    target_filter: Optional[dict] = None,
    sources: Optional[list] = None,
    enabled: Optional[bool] = None,
) -> str:
    """Update a collector's config. Only provided fields are changed.

    Args:
        name: Collector name to update.
        description: New description.
        schedule_sec: New schedule interval in seconds.
        target_type: New target type ("node" | "switch" | "job" | "query_result").
        target_filter: New target filter (replaces existing).
        sources: New sources config (replaces existing). [{type, name, config}, ...].
        enabled: Enable or disable the collector.
    """
    updates = []
    params = []
    if description is not None:
        updates.append("description = %s")
        params.append(description)
    if schedule_sec is not None:
        updates.append("schedule_sec = %s")
        params.append(schedule_sec)
    if target_type is not None:
        updates.append("target_type = %s")
        params.append(target_type)
    if target_filter is not None:
        updates.append("target_filter = %s")
        params.append(json.dumps(target_filter))
    if sources is not None:
        updates.append("sources = %s")
        params.append(json.dumps(sources))
    if enabled is not None:
        updates.append("enabled = %s")
        params.append(enabled)

    if not updates:
        _input_error("No fields to update")

    updates.append("updated_at = NOW()")
    params.append(name)
    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Read current config for archiving
            cur.execute(
                f"SELECT name, sources, target_filter, schedule_sec, enabled FROM {_t('collectors')} WHERE name = %s",
                (name,),
            )
            current = cur.fetchone()
            if not current:
                return _not_found("Collector", name)
            # Archive current version before overwriting (hash computed in Python for consistency)
            config_hash = _compute_collector_hash(
                current["name"], current["sources"], current["target_filter"],
                current["schedule_sec"], current["enabled"],
            )
            cur.execute(
                """INSERT INTO collector_versions (config_hash, name, sources, target_filter, schedule_sec, enabled, saved_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (config_hash, name) DO NOTHING""",
                (config_hash, current["name"], json.dumps(current["sources"], sort_keys=True),
                 json.dumps(current["target_filter"], sort_keys=True),
                 current["schedule_sec"], current["enabled"], "update_collector"),
            )
            cur.execute(
                f"UPDATE {_t('collectors')} SET {', '.join(updates)} WHERE name = %s",
                params,
            )
            conn.commit()
            if cur.rowcount == 0:
                return _not_found("Collector", name)
        return json.dumps({"status": "updated", "name": name})
    except Exception as e:
        _raise_tool_error(e)


# ── Rule CRUD ─────────────────────────────────────────────────────────────

def create_rule(
    rule_id: str,
    name: str,
    binds_to: str,
    analyze_code: str,
    created_by: str,
    description: str = "",
    stage: str = "create_task",
) -> str:
    """Create a new patrol rule.

    Args:
        rule_id: Unique rule ID (e.g. "switch_health_v1").
        name: Human-readable name.
        binds_to: Collector name this rule binds to.
        analyze_code: Python code defining analyze(collected, state).
        created_by: Who created this.
        description: Optional description.
        stage: Initial stage ("create_task" | "submit_alert" | "auto_cordon").
    """
    if stage not in VALID_STAGES:
        _input_error(f"Invalid stage: {stage}. Must be one of {VALID_STAGES}")

    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO patrol_rules
                   (rule_id, name, description, binds_to, analyze_code, stage, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (rule_id, name, description, binds_to, analyze_code, stage, created_by),
            )
            conn.commit()
        return json.dumps({"status": "created", "rule_id": rule_id, "stage": stage})
    except Exception as e:
        _raise_tool_error(e)


def update_rule_stage(rule_id: str, new_stage: str) -> str:
    """Promote or demote a rule's stage.

    Args:
        rule_id: The rule to update.
        new_stage: "create_task" | "submit_alert" | "auto_cordon".
    """
    if new_stage not in VALID_STAGES:
        _input_error(f"Invalid stage: {new_stage}. Must be one of {VALID_STAGES}")

    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""UPDATE {_t('patrol_rules')}
                   SET stage = %s, updated_at = NOW()
                   WHERE rule_id = %s""",
                (new_stage, rule_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return _not_found("Rule", rule_id)
        return json.dumps({"status": "updated", "rule_id": rule_id, "new_stage": new_stage})
    except Exception as e:
        _raise_tool_error(e)


def _parse_tool_json(raw: str, label: str) -> tuple[dict | None, dict | None]:
    try:
        data = json.loads(raw)
    except Exception as e:
        return None, {"error": f"Invalid {label} JSON: {e}"}
    if not isinstance(data, dict):
        return None, {"error": f"Invalid {label} JSON: expected object"}
    return data, None


def _ssh_payload_ok(payload: dict) -> bool | None:
    if not isinstance(payload, dict):
        return None
    if "ssh_ok" in payload:
        return bool(payload["ssh_ok"])
    if "ok" in payload:
        return bool(payload["ok"])
    return None


def _looks_like_feedback_refinement(reason: str) -> bool:
    normalized = reason.lower()
    return any(
        marker in normalized
        for marker in (
            "nff",
            "no_fault_found",
            "no fault found",
            "misclassified",
            "rma",
            "refinement",
        )
    )


def update_rule_code(
    rule_id: str,
    analyze_code: str,
    reason: str = "",
    mode: str = "finalize",
) -> str:
    """Update a rule's analyze_code.

    Args:
        rule_id: The rule to update.
        analyze_code: New Python code defining analyze(collected, state).
        reason: Optional update reason. NFF/MISCLASSIFIED refinements are replay-gated.
        mode: "finalize" writes code. "safe_mode"/"shadow_mode" contain blast radius only.
    """
    if mode not in {"finalize", "safe_mode", "shadow_mode"}:
        _input_error(f"Invalid mode: {mode}")

    refinement_reasons = {"nff_refinement", "misclassified_refinement"}
    if mode in {"safe_mode", "shadow_mode"}:
        stage_result, parse_error = _parse_tool_json(
            update_rule_stage(rule_id, "log_only"),
            "containment result",
        )
        if parse_error:
            _input_error(parse_error["error"])
        if stage_result.get("ok") is False:
            return json.dumps(stage_result)
        return json.dumps({
            "status": "contained",
            "rule_id": rule_id,
            "mode": mode,
            "dirty": True,
        })

    replay_gated = reason in refinement_reasons
    if reason and not replay_gated and _looks_like_feedback_refinement(reason):
        return json.dumps({
            "status": "rejected",
            "reason": "invalid_refinement_reason",
            "details": {
                "reason": reason,
                "allowed": sorted(refinement_reasons),
            },
        })
    if replay_gated:
        replay_result, parse_error = _parse_tool_json(
            run_rule_replay_suite(rule_id, analyze_code=analyze_code),
            "replay result",
        )
        if parse_error:
            return json.dumps({
                "status": "rejected",
                "reason": "replay_failed",
                "details": parse_error,
            })
        if not replay_result.get("passed"):
            details = replay_result.get("failures")
            if not details and replay_result.get("error"):
                details = {"error": replay_result["error"]}
            if not details:
                details = replay_result
            return json.dumps({
                "status": "rejected",
                "reason": "replay_failed",
                "details": details,
            })

        coverage = _replay_coverage_counts(rule_id)
        if coverage["positive"] < 1 or coverage["negative"] < 1:
            return json.dumps({
                "status": "rejected",
                "reason": "insufficient_replay_coverage",
                "details": coverage,
            })

        live_result, parse_error = _parse_tool_json(
            test_rule_once(rule_id, dry_run=True, analyze_code=analyze_code, sample=5),
            "live test result",
        )
        if parse_error:
            return json.dumps({
                "status": "rejected",
                "reason": "live_test_failed",
                "details": parse_error,
            })
        if live_result.get("error"):
            return json.dumps({
                "status": "rejected",
                "reason": "live_test_failed",
                "details": live_result,
            })

    try:
        with _conn() as conn:
            cur = conn.cursor()
            # Archive current version before overwriting (with code hash)
            cur.execute(
                f"""INSERT INTO patrol_rule_versions (code_hash, rule_id, analyze_code, stage, saved_by)
                   SELECT md5(analyze_code), rule_id, analyze_code, stage, %s
                   FROM {_t('patrol_rules')} WHERE rule_id = %s
                   ON CONFLICT (code_hash, rule_id) DO NOTHING""",
                ("update_rule_code", rule_id),
            )
            cur.execute(
                f"""UPDATE {_t('patrol_rules')}
                   SET analyze_code = %s, updated_at = NOW()
                   WHERE rule_id = %s""",
                (analyze_code, rule_id),
            )
            if cur.rowcount == 0:
                return _not_found("Rule", rule_id)
            if replay_gated:
                cur.execute(
                    """INSERT INTO rule_reconciliation_state
                       (rule_id, reconciliation_dirty, nff_baseline_at, reconcile_attempts, updated_at)
                       VALUES (%s, FALSE, NOW(), 1, NOW())
                       ON CONFLICT (rule_id) DO UPDATE SET
                           reconciliation_dirty = FALSE,
                           nff_baseline_at = NOW(),
                           reconcile_attempts = rule_reconciliation_state.reconcile_attempts + 1,
                           updated_at = NOW()""",
                    (rule_id,),
                )
            conn.commit()
        result = {"status": "updated", "rule_id": rule_id}
        if replay_gated:
            result["replay_gated"] = True
        return json.dumps(result)
    except Exception as e:
        _raise_tool_error(e)


def toggle_rule(rule_id: str, enabled: bool) -> str:
    """Enable or disable a rule.

    Args:
        rule_id: The rule to toggle.
        enabled: True to enable, False to disable.
    """
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE patrol_rules
                   SET enabled = %s, updated_at = NOW()
                   WHERE rule_id = %s""",
                (enabled, rule_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return _not_found("Rule", rule_id)
        return json.dumps({"status": "updated", "rule_id": rule_id, "enabled": enabled})
    except Exception as e:
        _raise_tool_error(e)


def list_rules(
    binds_to: Optional[str] = None,
    stage: Optional[str] = None,
) -> str:
    """List patrol rules, optionally filtered by collector or stage.

    Args:
        binds_to: Optional collector name to filter by.
        stage: Optional stage to filter by ("log_only", "create_task", "submit_alert", "auto_cordon").
    """
    try:
        conditions = []
        params = []
        if binds_to:
            conditions.append("binds_to = %s")
            params.append(binds_to)
        if stage:
            conditions.append("stage = %s")
            params.append(stage)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"""SELECT rule_id, name, binds_to, stage, enabled, created_at, updated_at
                    FROM {_t('patrol_rules')} {where} ORDER BY rule_id""",
                params,
            )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if r.get(k):
                        r[k] = str(r[k])
            return json.dumps({"rules": rows}, default=str)
    except Exception as e:
        _raise_tool_error(e)


def get_rule_detail(rule_id: str) -> str:
    """Get full rule details including analyze_code.

    Args:
        rule_id: The rule to fetch.
    """
    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT * FROM {_t('patrol_rules')} WHERE rule_id = %s", (rule_id,))
            row = cur.fetchone()
            if not row:
                return _not_found("Rule", rule_id)
            result = dict(row)
            for k in ("created_at", "updated_at", "graduated_at"):
                if result.get(k):
                    result[k] = str(result[k])
            return json.dumps(result, default=str)
    except Exception as e:
        _raise_tool_error(e)


def list_rule_versions(rule_id: str, limit: int = 10) -> str:
    """List version history for a rule. Each version is a snapshot saved before an update.

    Args:
        rule_id: The rule to list versions for.
        limit: Max versions to return (default 10, newest first).
    """
    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                """SELECT code_hash, rule_id, stage, saved_at, saved_by,
                          LENGTH(analyze_code) as code_length
                   FROM patrol_rule_versions
                   WHERE rule_id = %s
                   ORDER BY saved_at DESC LIMIT %s""",
                (rule_id, limit),
            )
            rows = cur.fetchall()
            return json.dumps([dict(r) for r in rows], default=str)
    except Exception as e:
        _raise_tool_error(e)


def rollback_rule(rule_id: str, code_hash: Optional[str] = None, saved_before: Optional[str] = None) -> str:
    """Rollback a rule to a previous version. Provide code_hash OR saved_before timestamp.

    Args:
        rule_id: The rule to rollback.
        code_hash: Specific code hash from list_rule_versions. Takes priority over saved_before.
        saved_before: ISO timestamp — rollback to the latest version saved before this time.
    """
    if not code_hash and not saved_before:
        _input_error("Provide code_hash or saved_before")

    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Find the version to restore
            if code_hash:
                cur.execute(
                    "SELECT * FROM patrol_rule_versions WHERE code_hash = %s AND rule_id = %s",
                    (code_hash, rule_id),
                )
            else:
                cur.execute(
                    """SELECT * FROM patrol_rule_versions
                       WHERE rule_id = %s AND saved_at < %s::timestamptz
                       ORDER BY saved_at DESC LIMIT 1""",
                    (rule_id, saved_before),
                )
            version = cur.fetchone()
            if not version:
                return _domain_result("not_found", f"No version found for rule {rule_id}", entity="RuleVersion", rule_id=rule_id)

            version = dict(version)
            # Archive current version before rollback
            cur.execute(
                f"""INSERT INTO patrol_rule_versions (code_hash, rule_id, analyze_code, stage, saved_by)
                   SELECT md5(analyze_code), rule_id, analyze_code, stage, %s
                   FROM {_t('patrol_rules')} WHERE rule_id = %s
                   ON CONFLICT (code_hash, rule_id) DO NOTHING""",
                (f"rollback_rule(to_hash={version['code_hash']})", rule_id),
            )
            # Restore the old version
            cur.execute(
                f"""UPDATE {_t('patrol_rules')}
                   SET analyze_code = %s, stage = %s, updated_at = NOW()
                   WHERE rule_id = %s""",
                (version["analyze_code"], version["stage"], rule_id),
            )
            conn.commit()
        return json.dumps({"status": "rolled_back", "rule_id": rule_id, "restored_code_hash": version["code_hash"], "restored_saved_at": str(version["saved_at"])})
    except Exception as e:
        _raise_tool_error(e)


def list_collector_versions(name: str, limit: int = 10) -> str:
    """List version history for a collector. Each version is a snapshot saved before an update.

    Args:
        name: Collector name to list versions for.
        limit: Max versions to return (default 10, newest first).
    """
    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                """SELECT config_hash, name, schedule_sec, enabled, saved_at, saved_by
                   FROM collector_versions
                   WHERE name = %s
                   ORDER BY saved_at DESC LIMIT %s""",
                (name, limit),
            )
            rows = cur.fetchall()
            return json.dumps([dict(r) for r in rows], default=str)
    except Exception as e:
        _raise_tool_error(e)


def rollback_collector(name: str, config_hash: Optional[str] = None, saved_before: Optional[str] = None) -> str:
    """Rollback a collector to a previous version. Provide config_hash OR saved_before timestamp.

    Args:
        name: Collector name to rollback.
        config_hash: Specific config hash from list_collector_versions. Takes priority over saved_before.
        saved_before: ISO timestamp — rollback to the latest version saved before this time.
    """
    if not config_hash and not saved_before:
        _input_error("Provide config_hash or saved_before")

    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Find the version to restore
            if config_hash:
                cur.execute(
                    "SELECT * FROM collector_versions WHERE config_hash = %s AND name = %s",
                    (config_hash, name),
                )
            else:
                cur.execute(
                    """SELECT * FROM collector_versions
                       WHERE name = %s AND saved_at < %s::timestamptz
                       ORDER BY saved_at DESC LIMIT 1""",
                    (name, saved_before),
                )
            version = cur.fetchone()
            if not version:
                return _domain_result("not_found", f"No version found for collector {name}", entity="CollectorVersion", name=name)

            version = dict(version)
            # Archive current version before rollback (hash computed in Python for consistency)
            cur.execute(
                f"SELECT name, sources, target_filter, schedule_sec, enabled FROM {_t('collectors')} WHERE name = %s",
                (name,),
            )
            current = cur.fetchone()
            if current:
                config_hash = _compute_collector_hash(
                    current["name"], current["sources"], current["target_filter"],
                    current["schedule_sec"], current["enabled"],
                )
                cur.execute(
                    """INSERT INTO collector_versions (config_hash, name, sources, target_filter, schedule_sec, enabled, saved_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (config_hash, name) DO NOTHING""",
                    (config_hash, current["name"], json.dumps(current["sources"], sort_keys=True),
                     json.dumps(current["target_filter"], sort_keys=True),
                     current["schedule_sec"], current["enabled"],
                     f"rollback_collector(to_hash={version['config_hash']})"),
                )
            # Restore the old version
            cur.execute(
                f"""UPDATE {_t('collectors')}
                   SET sources = %s, target_filter = %s, schedule_sec = %s, enabled = %s, updated_at = NOW()
                   WHERE name = %s""",
                (json.dumps(version.get("sources")) if version.get("sources") else None,
                 json.dumps(version.get("target_filter")) if version.get("target_filter") else None,
                 version.get("schedule_sec"), version.get("enabled"), name),
            )
            conn.commit()
        return json.dumps({"status": "rolled_back", "name": name, "restored_config_hash": version["config_hash"], "restored_saved_at": str(version["saved_at"])})
    except Exception as e:
        _raise_tool_error(e)


# ── Findings ──────────────────────────────────────────────────────────────

def list_findings(
    rule_id: Optional[str] = None,
    target_id: Optional[str] = None,
    active: Optional[bool] = None,
    resolved: Optional[bool] = None,
    stage: Optional[str] = None,
    verdict: Optional[str] = None,
    since_days: Optional[int] = None,
    current_code_only: bool = False,
    order: str = "desc",
    limit: int = 50,
) -> str:
    """List patrol findings with optional filters.

    Args:
        rule_id: Filter by rule.
        target_id: Filter by target.
        active: Filter by active status. True = target still failing, False = target recovered.
            Most useful: list_findings(active=true) to see only current problems.
        resolved: Filter by resolved status (True/False/None for all).
        stage: Filter by rule stage.
        verdict: Filter by verdict ("confirmed", "rejected", "" for unjudged).
        since_days: Only return findings from last N days.
        current_code_only: If True, only return findings from the CURRENT rule code
            AND collector config (both hashes must match current). Essential for delegators —
            don't analyze findings from old code or old collector versions.
            For aggregated counts by rule, use query_patrol_db instead.
        order: "asc" or "desc" (default desc = newest first).
        limit: Max results (default 50).

    Each finding includes:
      from_old_code: True if rule or collector code has changed since this finding was created.
          Findings from old code should NOT be judged — their verdicts would not reflect current rule behavior.
      active: True if target is still failing, False if recovered.
      deactivated_at: When the target recovered (null if still active).
      active_duration_sec: How long the problem has been/was active.
    """
    try:
        conditions = []
        params = []

        if rule_id:
            conditions.append("f.rule_id = %s")
            params.append(rule_id)
        if target_id:
            conditions.append("f.target_id = %s")
            params.append(target_id)
        if active is not None:
            conditions.append("f.active = %s")
            params.append(active)
        if resolved is not None:
            conditions.append("f.resolved = %s")
            params.append(resolved)
        if verdict is not None:
            if verdict == "":
                conditions.append("f.verdict IS NULL")
            else:
                conditions.append("f.verdict = %s")
                params.append(verdict)
        if since_days is not None:
            conditions.append("f.detected_at > NOW() - make_interval(days => %s)")
            params.append(since_days)
        if stage:
            conditions.append("r.stage = %s")
            params.append(stage)
        if current_code_only:
            # Only findings where both rule_code_hash and collector_config_hash match current
            conditions.append(
                "f.rule_code_hash IS NOT NULL AND f.rule_code_hash = md5(r.analyze_code)"
                " AND f.collector_config_hash IS NOT NULL"
                " AND f.collector_config_hash = (SELECT config_hash FROM collector_versions cv WHERE cv.name = r.binds_to ORDER BY saved_at DESC LIMIT 1)"
            )

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)

        # Always JOIN patrol_rules for provenance (from_old_code via hash)
        query = f"""SELECT f.finding_id, f.rule_id, f.target_id, f.severity, f.action,
                           f.evidence, f.verdict, f.resolved, f.active, f.detected_at,
                           f.deactivated_at,
                           EXTRACT(EPOCH FROM (COALESCE(f.deactivated_at, NOW()) - f.detected_at))::int AS active_duration_sec,
                           f.rule_code_hash, f.collector_config_hash,
                           r.stage, r.updated_at AS rule_code_updated_at, r.binds_to,
                           md5(r.analyze_code) AS current_rule_code_hash
                    FROM {_t('patrol_findings')} f
                    JOIN patrol_rules r ON f.rule_id = r.rule_id
                    {where}
                    ORDER BY f.detected_at DESC
                    LIMIT %s"""

        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(query, params)
            rows = [dict(r) for r in cur.fetchall()]

            # Batch-load current collector hashes for bound collectors
            bound_collectors = {r.get("binds_to") for r in rows if r.get("binds_to")}
            collector_hashes = {}
            if bound_collectors:
                cur.execute(
                    "SELECT name, config_hash FROM collector_versions WHERE name = ANY(%s) ORDER BY saved_at DESC",
                    (list(bound_collectors),),
                )
                for cv in cur.fetchall():
                    # Keep only the latest hash per collector
                    collector_hashes.setdefault(cv["name"], cv["config_hash"])

            for r in rows:
                if r.get("detected_at"):
                    r["detected_at"] = str(r["detected_at"])
                if r.get("rule_code_updated_at"):
                    r["rule_code_updated_at"] = str(r["rule_code_updated_at"])
                binds_to = r.pop("binds_to", None)

                # from_old_code: true if rule OR collector hash doesn't match current
                rule_hash = r.pop("current_rule_code_hash", None)
                finding_rule_hash = r.get("rule_code_hash")
                finding_coll_hash = r.get("collector_config_hash")
                current_coll_hash = collector_hashes.get(binds_to) if binds_to else None

                rule_old = None
                coll_old = None
                if finding_rule_hash and rule_hash:
                    rule_old = finding_rule_hash != rule_hash
                elif finding_rule_hash is None and rule_hash:
                    rule_old = True  # no hash = old finding before provenance
                if finding_coll_hash and current_coll_hash:
                    coll_old = finding_coll_hash != current_coll_hash
                elif finding_coll_hash is None and current_coll_hash:
                    coll_old = True  # no hash = old finding before provenance

                if rule_old is True or coll_old is True:
                    r["from_old_code"] = True
                elif rule_old is False and coll_old is False:
                    r["from_old_code"] = False
                else:
                    r["from_old_code"] = None
            return json.dumps({"findings": rows}, default=str)
    except Exception as e:
        _raise_tool_error(e)


def record_verdict(finding_id: int, verdict: str, verdict_reason: str = None) -> str:
    """Record a verdict on a finding (confirmed or rejected).

    Args:
        finding_id: The finding to update.
        verdict: "confirmed" or "rejected".
        verdict_reason: Why this verdict. Essential for the detection feedback loop —
            common rejection reasons surface anti-patterns that improve future rules.
            Examples: "Stale syslog events from boot cycle, node already recovered",
            "Wrong node — Xid error was on a different node in same job",
            "NFF — no hardware fault found after full investigation",
            "Transient event, not recurring".
    """
    if verdict not in VALID_VERDICTS:
        _input_error(f"Invalid verdict: {verdict}. Must be one of {VALID_VERDICTS}")

    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE patrol_findings
                   SET verdict = %s, resolved = TRUE, verdict_reason = %s
                   WHERE finding_id = %s""",
                (verdict, verdict_reason, finding_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return _not_found("Finding", finding_id)
        return json.dumps({"status": "updated", "finding_id": finding_id, "verdict": verdict})
    except Exception as e:
        _raise_tool_error(e)


def get_finding_raw_data(finding_id: int, max_content: int = 5000) -> str:
    """Get raw source data for a finding. Returns collector snapshot data AND/OR
    investigation evidence, whichever is available.

    Use this to learn detection patterns from confirmed/rejected findings.
    Checks both data sources independently:
    - collector_data: the target payload the rule saw (from collector_snapshots)
    - investigation_data: diagnostic commands/outputs from agent investigation
    - provenance: what collector sources and rule code produced this finding

    Args:
        finding_id: The finding to look up raw data for.
        max_content: Truncate content fields to this many chars (default 5000).
    """
    try:
        result = {"finding_id": finding_id, "collector_data": None, "investigation_data": [],
                  "provenance": None}

        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"""SELECT f.finding_id, f.rule_id, f.target_id, f.evidence,
                           f.collector_snapshot_id, f.rule_code_hash, f.collector_config_hash
                    FROM {_t('patrol_findings')} f
                    WHERE f.finding_id = %s""",
                (finding_id,),
            )
            finding = cur.fetchone()
            if not finding:
                return _not_found("Finding", finding_id)

            result["rule_id"] = finding["rule_id"]
            result["target_id"] = finding["target_id"]

            # Build provenance: what code produced this finding?
            from_old_code = None
            rule_code_hash = finding.get("rule_code_hash")
            collector_config_hash = finding.get("collector_config_hash")

            rule_old = None
            coll_old = None

            if rule_code_hash:
                cur.execute(
                    f"SELECT md5(analyze_code) AS current_hash, binds_to FROM {_t('patrol_rules')} WHERE rule_id = %s",
                    (finding["rule_id"],),
                )
                rule_row = cur.fetchone()
                if rule_row and rule_row["current_hash"]:
                    rule_old = rule_code_hash != rule_row["current_hash"]
                    # Also check collector hash
                    binds_to = rule_row["binds_to"]
                    if collector_config_hash and binds_to:
                        cur.execute(
                            "SELECT config_hash FROM collector_versions WHERE name = %s ORDER BY saved_at DESC LIMIT 1",
                            (binds_to,),
                        )
                        cv = cur.fetchone()
                        if cv and cv["config_hash"]:
                            coll_old = collector_config_hash != cv["config_hash"]
            else:
                rule_old = True  # no hash = old finding before provenance
            if collector_config_hash is None:
                coll_old = True  # no hash = old finding before provenance

            if rule_old is True or coll_old is True:
                from_old_code = True
            elif rule_old is False and coll_old is False:
                from_old_code = False
            else:
                from_old_code = None

            provenance = {
                "rule_code_hash": rule_code_hash,
                "collector_config_hash": collector_config_hash,
                "from_old_code": from_old_code,
            }

            # 1. Collector snapshot: raw target payload the rule saw
            snapshot_id = finding.get("collector_snapshot_id")
            if snapshot_id:
                cur.execute(
                    f"""SELECT frozen_input
                        FROM {_t('collector_snapshots')}
                        WHERE snapshot_hash = %s""",
                    (snapshot_id,),
                )
                snapshot = cur.fetchone()
                if snapshot and snapshot.get("frozen_input"):
                    frozen = snapshot["frozen_input"]
                    targets = frozen.get("targets", [])
                    target_data = None
                    for t in targets:
                        if t.get("id") == finding["target_id"]:
                            target_data = t
                            break
                    if target_data:
                        payload = target_data.get("payload", {})
                        if isinstance(payload, dict):
                            for k, v in list(payload.items()):
                                if isinstance(v, str) and len(v) > max_content:
                                    payload[k] = v[:max_content] + f"... [truncated, {len(v)} chars total]"
                        result["collector_data"] = {
                            "collector_name": frozen.get("collector_name"),
                            "target_id": target_data.get("id"),
                            "payload": payload,
                        }

            # 2. Code provenance via hashes on the finding
            rule_code_hash = finding.get("rule_code_hash")
            collector_config_hash = finding.get("collector_config_hash")
            if rule_code_hash:
                cur.execute(
                    "SELECT rule_id, stage, saved_at FROM patrol_rule_versions WHERE code_hash = %s LIMIT 1",
                    (rule_code_hash,),
                )
                rv = cur.fetchone()
                if rv:
                    provenance["rule_code_at_creation"] = {
                        "code_hash": rule_code_hash,
                        "rule_id": rv["rule_id"],
                        "stage": rv["stage"],
                        "saved_at": str(rv["saved_at"]),
                    }
            if collector_config_hash:
                cur.execute(
                    "SELECT name, saved_at FROM collector_versions WHERE config_hash = %s LIMIT 1",
                    (collector_config_hash,),
                )
                cv = cur.fetchone()
                if cv:
                    provenance["collector_config_at_creation"] = {
                        "config_hash": collector_config_hash,
                        "name": cv["name"],
                        "saved_at": str(cv["saved_at"]),
                    }

            result["provenance"] = provenance

            # 2. Investigation evidence: raw diagnostic commands agent ran
            cur.execute(
                """SELECT id, node_name, collected_at, collected_by, source,
                          category, summary, content, metadata
                   FROM investigation_evidence
                   WHERE finding_id = %s
                   ORDER BY collected_at ASC""",
                (finding_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                if r.get("collected_at"):
                    r["collected_at"] = r["collected_at"].isoformat()
                if isinstance(r.get("metadata"), str):
                    try:
                        r["metadata"] = json.loads(r["metadata"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                if isinstance(r.get("content"), str) and len(r["content"]) > max_content:
                    r["content"] = r["content"][:max_content] + f"... [truncated, {len(r['content'])} chars total]"
            result["investigation_data"] = rows

        return json.dumps(result, default=str)
    except Exception as e:
        _raise_tool_error(e)


def reconcile_finding(finding_id: int, repair_outcome: str) -> str:
    """Reconcile a finding after repair/investigation outcome is known.

    Args:
        finding_id: The finding to reconcile.
        repair_outcome: One of VALID_REPAIR_OUTCOMES.
    """
    if repair_outcome not in VALID_REPAIR_OUTCOMES:
        return json.dumps({
            "error": f"Invalid repair_outcome: {repair_outcome}. Must be one of {VALID_REPAIR_OUTCOMES}",
        })

    verdict = None
    resolved = True
    if repair_outcome == "NO_FAULT_FOUND":
        verdict = "rejected_nff"
    elif repair_outcome == "MISCLASSIFIED":
        resolved = False

    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if repair_outcome == "NO_FAULT_FOUND":
                cur.execute(
                    f"""UPDATE {_t('patrol_findings')}
                       SET repair_outcome = %s,
                           repair_outcome_at = NOW(),
                           verdict = %s,
                           resolved = TRUE
                       WHERE finding_id = %s
                         AND repair_outcome IS NULL
                       RETURNING finding_id, repair_outcome, verdict, resolved""",
                    (repair_outcome, verdict, finding_id),
                )
            elif repair_outcome == "MISCLASSIFIED":
                cur.execute(
                    f"""UPDATE {_t('patrol_findings')}
                       SET repair_outcome = %s,
                           repair_outcome_at = NOW(),
                           resolved = FALSE
                       WHERE finding_id = %s
                         AND repair_outcome IS NULL
                       RETURNING finding_id, repair_outcome, verdict, resolved""",
                    (repair_outcome, finding_id),
                )
            else:
                cur.execute(
                    f"""UPDATE {_t('patrol_findings')}
                       SET repair_outcome = %s,
                           repair_outcome_at = NOW(),
                           resolved = TRUE
                       WHERE finding_id = %s
                         AND repair_outcome IS NULL
                       RETURNING finding_id, repair_outcome, verdict, resolved""",
                    (repair_outcome, finding_id),
                )
            updated = cur.fetchone()
            if updated:
                conn.commit()
                return json.dumps({
                    "status": "reconciled",
                    "finding_id": updated["finding_id"],
                    "repair_outcome": updated["repair_outcome"],
                    "verdict": updated.get("verdict"),
                    "resolved": updated["resolved"],
                })

            cur.execute(
                f"""SELECT finding_id, repair_outcome, verdict, resolved
                   FROM {_t('patrol_findings')}
                   WHERE finding_id = %s""",
                (finding_id,),
            )
            row = cur.fetchone()
            if not row:
                return _not_found("Finding", finding_id)

            existing_repair_outcome = row.get("repair_outcome")
            if existing_repair_outcome:
                return json.dumps({
                    "status": "already_reconciled",
                    "finding_id": row["finding_id"],
                    "repair_outcome": existing_repair_outcome,
                    "verdict": row.get("verdict"),
                    "resolved": row["resolved"],
                })
            return _domain_result("not_reconciled", f"Finding {finding_id} was not reconciled", finding_id=finding_id)
    except Exception as e:
        _raise_tool_error(e)


def create_finding(
    rule_id: str,
    target_id: str,
    target_type: str,
    severity: str,
    action: str,
    evidence: dict | None = None,
    confidence: float = 0.5,
    action_params: dict | None = None,
    collector_snapshot_id: str | None = None,
    raw_evidence_hash: str | None = None,
) -> str:
    """Create a finding manually (not from a rule execution).

    Use this when you discover an issue in collector logs that no rule covers.
    For example: automate-detection-pattern finds failures in raw output that
    no existing rule would catch — create a finding so inspect-infra-issue can
    investigate it.

    Args:
        rule_id: Use 'manual' for agent-discovered issues, or a real rule_id
                 if this finding should be attributed to a specific rule.
        target_id: What is affected (e.g. switch hostname, node hostname).
        target_type: 'switch' or 'node'.
        severity: 'critical', 'warning', or 'info'.
        action: 'cordon', 'alert', or 'log_only'.
        evidence: Dict with raw data supporting this finding.
        confidence: 0.0-1.0 how confident you are this is a real issue.
        action_params: Optional dict of action parameters.
        collector_snapshot_id: Optional collector run snapshot identifier.
        raw_evidence_hash: Optional deterministic hash of raw collector evidence.
    """
    if severity not in ("critical", "warning", "info"):
        _input_error(f"Invalid severity: {severity}. Must be critical/warning/info")
    if action not in ("cordon", "alert", "log_only"):
        _input_error(f"Invalid action: {action}. Must be cordon/alert/log_only")

    try:
        # Dedup: don't create duplicate findings for same rule+target+action in last 24h
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""SELECT 1 FROM {_t('patrol_findings')}
                   WHERE rule_id = %s AND target_id = %s AND action = %s
                     AND detected_at > NOW() - INTERVAL '24 hours'
                   LIMIT 1""",
                (rule_id, target_id, action),
            )
            if cur.fetchone():
                return json.dumps({"status": "duplicate", "note": "Same finding exists in last 24h"})

            # Compute hashes for provenance if a real rule is referenced
            rule_code_hash = None
            collector_config_hash = None
            if rule_id and rule_id != "manual":
                cur.execute(
                    f"SELECT md5(analyze_code) AS h, binds_to FROM {_t('patrol_rules')} WHERE rule_id = %s",
                    (rule_id,),
                )
                rh = cur.fetchone()
                if rh:
                    rule_code_hash = rh[0]
                    binds_to = rh[1]
                    if binds_to:
                        cur.execute(
                            f"SELECT name, sources, target_filter, schedule_sec, enabled FROM {_t('collectors')} WHERE name = %s",
                            (binds_to,),
                        )
                        ch = cur.fetchone()
                        if ch:
                            collector_config_hash = _compute_collector_hash(
                                ch[0], ch[1], ch[2], ch[3], ch[4],
                            )

            cur.execute(
                f"""INSERT INTO {_t('patrol_findings')}
                   (rule_id, target_id, target_type, severity, action,
                    action_params, evidence, confidence, collector_snapshot_id,
                    raw_evidence_hash, detected_at, rule_code_hash, collector_config_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s)
                   RETURNING finding_id""",
                (
                    rule_id,
                    target_id,
                    target_type,
                    severity,
                    action,
                    json.dumps(action_params or {}, default=str),
                    json.dumps(evidence or {}, default=str),
                    confidence,
                    collector_snapshot_id,
                    raw_evidence_hash,
                    rule_code_hash,
                    collector_config_hash,
                ),
            )
            finding_id = cur.fetchone()[0]
            conn.commit()
        return json.dumps({"status": "created", "finding_id": finding_id, "rule_id": rule_id, "target_id": target_id,
                           "severity": severity, "action": action,
                           "note": "Use save_evidence with this finding_id to attach raw investigation data"})
    except Exception as e:
        _raise_tool_error(e)


def _jsonable_row(row: dict) -> dict:
    result = dict(row)
    for key, value in list(result.items()):
        if hasattr(value, "isoformat"):
            result[key] = str(value)
    return result


def _get_replay_case(rule_id: str, replay_case_id: int) -> dict | None:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""SELECT *
               FROM {_t('rule_replay_cases')}
               WHERE rule_id = %s AND replay_case_id = %s""",
            (rule_id, replay_case_id),
        )
        row = cur.fetchone()
        return _jsonable_row(row) if row else None


def _get_replay_cases(rule_id: str, include_counterexamples: bool = True) -> list[dict]:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if include_counterexamples:
            cur.execute(
                f"""SELECT *
                   FROM {_t('rule_replay_cases')}
                   WHERE rule_id = %s
                   ORDER BY created_at ASC, replay_case_id ASC""",
                (rule_id,),
            )
        else:
            cur.execute(
                f"""SELECT *
                   FROM {_t('rule_replay_cases')}
                   WHERE rule_id = %s AND source = 'rma_bad_feedback'
                   ORDER BY created_at ASC, replay_case_id ASC""",
                (rule_id,),
            )
        return [_jsonable_row(row) for row in cur.fetchall()]


def _replay_coverage_counts(rule_id: str) -> dict:
    coverage = {"positive": 0, "negative": 0}
    for replay_case in _get_replay_cases(rule_id, include_counterexamples=True):
        expected = replay_case.get("expected_behavior") or {}
        if expected.get("should_fire") is True:
            coverage["positive"] += 1
        elif expected.get("should_fire") is False:
            coverage["negative"] += 1
    return coverage


def create_rule_replay_case(
    rule_id: str,
    source: str,
    frozen_input: dict,
    expected_behavior: dict,
    finding_id: int | None = None,
    case_id: int | None = None,
    repair_outcome: str | None = None,
    attribution: str | None = None,
    rule_version_at_detection: str | None = None,
    collector_snapshot_id: str | None = None,
    raw_evidence_hash: str | None = None,
    created_by: str = "agent",
) -> str:
    """Create an immutable frozen replay fixture for a patrol rule."""
    if source not in VALID_REPLAY_SOURCES:
        _input_error(f"Invalid source: {source}. Must be one of {sorted(VALID_REPLAY_SOURCES)}")
    if repair_outcome is not None and repair_outcome not in VALID_REPAIR_OUTCOMES:
        _input_error(f"Invalid repair_outcome: {repair_outcome}")
    if not isinstance(frozen_input, dict):
        _input_error("frozen_input must be a dict")
    if not isinstance(expected_behavior, dict):
        _input_error("expected_behavior must be a dict")

    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"""INSERT INTO {_t('rule_replay_cases')}
                   (case_id, finding_id, rule_id, source, repair_outcome, attribution,
                    rule_version_at_detection, collector_snapshot_id, raw_evidence_hash,
                    created_by, frozen_input, expected_behavior)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                   RETURNING replay_case_id""",
                (
                    case_id,
                    finding_id,
                    rule_id,
                    source,
                    repair_outcome,
                    attribution,
                    rule_version_at_detection,
                    collector_snapshot_id,
                    raw_evidence_hash,
                    created_by,
                    json.dumps(frozen_input, default=str),
                    json.dumps(expected_behavior, default=str),
                ),
            )
            row = cur.fetchone()
            conn.commit()
        return json.dumps({
            "status": "created",
            "replay_case_id": row["replay_case_id"],
            "rule_id": rule_id,
        })
    except Exception as e:
        _raise_tool_error(e)


def _feedback_examples_for_replay(rule_id: str) -> list[dict]:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""SELECT r.case_id,
                      r.finding_id,
                      r.rule_id,
                      r.repair_outcome,
                      r.attribution,
                      r.feedback_label,
                      r.expected_behavior,
                      f.target_id,
                      f.target_type,
                      f.evidence,
                      f.collector_snapshot_id,
                      f.raw_evidence_hash,
                      pr.binds_to AS collector_name
               FROM rma_finding_reconciliations r
               LEFT JOIN {_t('patrol_findings')} f ON f.finding_id = r.finding_id
               LEFT JOIN {_t('patrol_rules')} pr ON pr.rule_id = r.rule_id
               WHERE r.rule_id = %s
                 AND r.attribution = 'detection'
                 AND r.feedback_label IN ('positive', 'negative')
               ORDER BY r.reconciled_at ASC, r.case_id ASC, r.finding_id ASC""",
            (rule_id,),
        )
        return [_jsonable_row(row) for row in cur.fetchall()]


def _replay_case_exists(rule_id: str, finding_id: int | None, case_id: int | None) -> bool:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT 1
               FROM {_t('rule_replay_cases')}
               WHERE rule_id = %s
                 AND finding_id IS NOT DISTINCT FROM %s
                 AND case_id IS NOT DISTINCT FROM %s
               LIMIT 1""",
            (rule_id, finding_id, case_id),
        )
        return cur.fetchone() is not None


def _expected_behavior_from_feedback(example: dict) -> dict:
    expected = example.get("expected_behavior") or {}
    if expected:
        return expected
    if example.get("feedback_label") == "positive":
        return {"should_fire": True}
    if example.get("feedback_label") == "negative":
        return {"should_fire": False}
    return {}


def _source_from_feedback_label(feedback_label: str) -> str:
    if feedback_label == "positive":
        return "confirmed_counterexample"
    return "rma_bad_feedback"


def _frozen_input_from_feedback(example: dict) -> dict:
    """Create a frozen_input dict from feedback data.

    For job_metadata collectors, the finding's evidence is flat and doesn't match
    the real collector structure (which has taskRoles/taskStatuses). We try to
    load from collector_snapshots instead. Falls back to flat structure for other types.
    """
    collector_name = example.get("collector_name") or ""
    target_id = example.get("target_id") or ""
    target_type = example.get("target_type") or "node"

    # Try loading real collector snapshot for job_metadata collectors
    if "job" in collector_name or target_type == "job":
        snapshot = _snapshot_for_replay(collector_name, target_id)
        if snapshot:
            return snapshot

    return {
        "collector_name": collector_name,
        "collector_status": "success",
        "targets": [{
            "id": target_id,
            "type": target_type,
            "payload": example.get("evidence") or {},
            "meta": {},
        }],
        "errors": [],
        "duration": 0.0,
    }


def _snapshot_for_replay(collector_name: str, target_id: str) -> dict | None:
    """Load a collector snapshot and extract the target's data for replay."""
    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"""SELECT frozen_input
                    FROM {_t('collector_snapshots')}
                    WHERE collector_name = %s
                    ORDER BY created_at DESC LIMIT 1""",
                (collector_name,),
            )
            row = cur.fetchone()
            if not row:
                return None
            frozen = row["frozen_input"]
            if isinstance(frozen, str):
                import json as _json
                frozen = _json.loads(frozen)
            # Find the target in the snapshot
            targets = frozen.get("targets", [])
            # Match by target_id prefix (job names may have attempt suffixes)
            matching = [t for t in targets if target_id in t.get("id", "")]
            if not matching:
                # Return full snapshot — rule may need all targets for context
                return frozen
            return {
                "collector_name": frozen.get("collector_name", collector_name),
                "collector_status": frozen.get("collector_status", "success"),
                "targets": matching if len(matching) <= 5 else matching[:5],
                "errors": frozen.get("errors", []),
                "duration": frozen.get("duration", 0.0),
            }
    except Exception:
        return None


def create_rule_replay_cases_from_feedback(rule_id: str, created_by: str = "automate-detection-pattern") -> str:
    """Create frozen replay fixtures from detection-attributed labeled RMA feedback."""
    try:
        created = 0
        skipped = 0
        positive = 0
        negative = 0
        replay_case_ids = []

        for example in _feedback_examples_for_replay(rule_id):
            label = example.get("feedback_label")
            if label == "positive":
                positive += 1
            elif label == "negative":
                negative += 1
            else:
                skipped += 1
                continue

            if _replay_case_exists(rule_id, example.get("finding_id"), example.get("case_id")):
                skipped += 1
                continue

            created_result, parse_error = _parse_tool_json(
                create_rule_replay_case(
                    rule_id=rule_id,
                    source=_source_from_feedback_label(label),
                    frozen_input=_frozen_input_from_feedback(example),
                    expected_behavior=_expected_behavior_from_feedback(example),
                    finding_id=example.get("finding_id"),
                    case_id=example.get("case_id"),
                    repair_outcome=example.get("repair_outcome"),
                    attribution=example.get("attribution"),
                    collector_snapshot_id=example.get("collector_snapshot_id"),
                    raw_evidence_hash=example.get("raw_evidence_hash"),
                    created_by=created_by,
                ),
                "create replay case result",
            )
            if parse_error:
                _input_error(parse_error["error"])
            if created_result.get("error"):
                return json.dumps(created_result)
            if created_result.get("status") == "created":
                created += 1
                replay_case_ids.append(created_result.get("replay_case_id"))

        return json.dumps({
            "rule_id": rule_id,
            "created": created,
            "skipped": skipped,
            "positive": positive,
            "negative": negative,
            "replay_case_ids": replay_case_ids,
        })
    except Exception as e:
        _raise_tool_error(e)


def list_rule_replay_cases(rule_id: str, include_counterexamples: bool = True) -> str:
    """List frozen replay fixtures for a patrol rule."""
    try:
        cases = _get_replay_cases(rule_id, include_counterexamples=include_counterexamples)
        return json.dumps({"rule_id": rule_id, "replay_cases": cases}, default=str)
    except Exception as e:
        _raise_tool_error(e)


def _collection_from_frozen_input(frozen_input: dict):
    from patrol_cron.models import CollectionResult, TargetData

    return CollectionResult(
        collector_name=frozen_input.get("collector_name") or "",
        targets=[
            TargetData(
                id=target.get("id", ""),
                type=target.get("type") or "node",
                payload=target.get("payload", {}),
                meta=target.get("meta", {}),
            )
            for target in frozen_input.get("targets", [])
        ],
        errors=frozen_input.get("errors", []),
        duration=frozen_input.get("duration", 0.0),
    )


def _observation_to_dict(obs) -> dict:
    return {
        "signal_key": obs.signal_key,
        "target_id": obs.target_id,
        "status": obs.status,
        "severity": obs.severity,
        "action": obs.action,
        "action_params": obs.action_params,
        "evidence": obs.evidence,
        "confidence": obs.confidence,
        "lifecycle": obs.lifecycle,
    }


def _finding_to_dict(finding) -> dict:
    return {
        "target_id": finding.target_id,
        "severity": finding.severity,
        "action": finding.action,
        "action_params": finding.action_params,
        "evidence": finding.evidence,
        "confidence": finding.confidence,
    }


def _assert_replay_expected(findings: list[dict], expected: dict) -> tuple[bool, list[str]]:
    failures = []
    should_fire = expected.get("should_fire")
    if should_fire is False and findings:
        failures.append("expected no finding, got findings")
    if should_fire is True and not findings:
        failures.append("expected finding, got none")

    expected_action = expected.get("expected_action")
    expected_severity = expected.get("expected_severity")
    expected_target_id = expected.get("expected_target_id") or expected.get("target_id")
    match_fields = {
        "action": expected_action,
        "severity": expected_severity,
        "target_id": expected_target_id,
    }
    required_fields = {key: value for key, value in match_fields.items() if value}
    if required_fields and not any(
        all(finding.get(key) == value for key, value in required_fields.items())
        for finding in findings
    ):
        description = " and ".join(f"{key} {value}" for key, value in required_fields.items())
        failures.append(f"expected one finding matching {description}")

    max_stage = expected.get("max_stage")
    if max_stage == "log_only" and any(
        finding.get("action") not in ("log_only", "alert") for finding in findings
    ):
        failures.append("expected safe/log-only behavior")

    return not failures, failures


def _assert_observations_expected(observations: list[dict], expected: dict) -> tuple[bool, list[str]]:
    """Assert that observations match expected behavior (lifecycle-aware)."""
    failures = []
    bad_obs = [o for o in observations if o["status"] == "bad"]
    healthy_obs = [o for o in observations if o["status"] == "healthy"]

    should_fire = expected.get("should_fire")
    if should_fire is False and bad_obs:
        failures.append("expected no bad observation, got bad observations")
    if should_fire is True and not bad_obs:
        failures.append("expected bad observation, got none")

    expected_action = expected.get("expected_action")
    expected_severity = expected.get("expected_severity")
    expected_target_id = expected.get("expected_target_id") or expected.get("target_id")
    match_fields = {
        "action": expected_action,
        "severity": expected_severity,
        "target_id": expected_target_id,
    }
    required_fields = {key: value for key, value in match_fields.items() if value}
    if required_fields and not any(
        all(obs.get(key) == value for key, value in required_fields.items())
        for obs in bad_obs
    ):
        description = " and ".join(f"{key} {value}" for key, value in required_fields.items())
        failures.append(f"expected one observation matching {description}")

    expected_signal_key = expected.get("expected_signal_key")
    if expected_signal_key and not any(
        obs.get("signal_key") == expected_signal_key for obs in bad_obs
    ):
        failures.append(f"expected signal_key '{expected_signal_key}', not found")

    max_stage = expected.get("max_stage")
    if max_stage == "log_only" and any(
        obs.get("action") not in ("log_only", "alert") for obs in bad_obs
    ):
        failures.append("expected safe/log-only behavior")

    return not failures, failures


def _evaluate_replay_case(replay_case: dict, analyze_code: str) -> dict:
    from patrol_cron.analyzer import run_sandboxed

    frozen_input = replay_case.get("frozen_input") or {}
    if isinstance(frozen_input, str):
        frozen_input = json.loads(frozen_input)
    expected_behavior = replay_case.get("expected_behavior") or {}
    if isinstance(expected_behavior, str):
        expected_behavior = json.loads(expected_behavior)

    # Multi-cycle replay: frozen_input is a list of collection snapshots
    # with per-cycle expected behavior
    if isinstance(frozen_input, list):
        return _evaluate_multi_cycle_replay(replay_case, analyze_code,
                                             frozen_input, expected_behavior)

    # Single-cycle replay (legacy + lifecycle-aware)
    collected = _collection_from_frozen_input(frozen_input)
    rule_result = run_sandboxed(analyze_code, collected, {})
    observations_out = [_observation_to_dict(obs) for obs in rule_result.observations]
    findings_out = [_finding_to_dict(f) for f in rule_result.legacy_findings()]
    passed, failures = _assert_observations_expected(observations_out, expected_behavior)

    return {
        "rule_id": replay_case.get("rule_id"),
        "replay_case_id": replay_case.get("replay_case_id"),
        "passed": passed,
        "failures": failures,
        "observations": observations_out,
        "findings": findings_out,
        "new_state": rule_result.state,
        "lifecycle_enabled": rule_result.lifecycle_enabled,
    }


def _evaluate_multi_cycle_replay(
    replay_case: dict,
    analyze_code: str,
    frozen_inputs: list,
    expected_behavior: dict,
) -> dict:
    """Run a multi-cycle replay with lifecycle simulation.

    frozen_inputs: list of frozen collection snapshots (one per cycle)
    expected_behavior: {
        "cycles": [
            {"expected_action": "none|open|refresh|close", "expected_observations": [...]},
            ...
        ]
    }
    """
    from patrol_cron.analyzer import run_sandboxed
    from patrol_cron.lifecycle import apply_rule_result

    cycles_expected = expected_behavior.get("cycles") or []
    private_state = {}
    cycle_results = []
    all_failures = []

    for i, frozen_input in enumerate(frozen_inputs):
        if isinstance(frozen_input, str):
            frozen_input = json.loads(frozen_input)

        collected = _collection_from_frozen_input(frozen_input)
        rule_result = run_sandboxed(analyze_code, collected, private_state)
        private_state = rule_result.state

        observations_out = [_observation_to_dict(obs) for obs in rule_result.observations]
        findings_out = [_finding_to_dict(f) for f in rule_result.legacy_findings()]

        # Check per-cycle expectations
        cycle_expected = cycles_expected[i] if i < len(cycles_expected) else {}
        cycle_failures = []

        expected_action = cycle_expected.get("expected_action")
        if expected_action:
            if expected_action == "none" and findings_out:
                cycle_failures.append(f"cycle {i}: expected no findings, got {len(findings_out)}")
            elif expected_action == "open" and not findings_out:
                cycle_failures.append(f"cycle {i}: expected finding (open), got none")
            # "refresh" and "close" are lifecycle actions, not per-cycle finding assertions
            # They're verified by the lifecycle simulation below

        if cycle_expected.get("expected_observations"):
            obs_expected = cycle_expected["expected_observations"]
            passed, obs_failures = _assert_observations_expected(observations_out, obs_expected)
            if not passed:
                cycle_failures.extend(f"cycle {i}: {f}" for f in obs_failures)

        if cycle_failures:
            all_failures.extend(cycle_failures)

        cycle_results.append({
            "cycle": i,
            "observations": observations_out,
            "findings": findings_out,
            "expected_action": expected_action or "none",
            "failures": cycle_failures,
        })

    return {
        "rule_id": replay_case.get("rule_id"),
        "replay_case_id": replay_case.get("replay_case_id"),
        "passed": not all_failures,
        "failures": all_failures,
        "cycles": cycle_results,
        "lifecycle_enabled": True,
    }


def replay_rule_case(rule_id: str, replay_case_id: int, analyze_code: str | None = None) -> str:
    """Run current or candidate analyze_code against one frozen replay fixture."""
    try:
        from patrol_cron import db as patrol_db

        rule = patrol_db.get_rule(rule_id)
        if not rule:
            return _not_found("Rule", rule_id)
        if analyze_code is None:
            analyze_code = rule["analyze_code"]

        replay_case = _get_replay_case(rule_id, replay_case_id)
        if not replay_case:
            return _domain_result("not_found", f"Replay case {replay_case_id} not found for rule '{rule_id}'", entity="ReplayCase", id=replay_case_id, rule_id=rule_id)

        return json.dumps(_evaluate_replay_case(replay_case, analyze_code), default=str)
    except Exception as e:
        _raise_tool_error(e)


def run_rule_replay_suite(rule_id: str, analyze_code: str | None = None) -> str:
    """Run all frozen replay fixtures for a rule and return aggregate pass/fail."""
    try:
        from patrol_cron import db as patrol_db

        rule = patrol_db.get_rule(rule_id)
        if not rule:
            return _not_found("Rule", rule_id)
        if analyze_code is None:
            analyze_code = rule["analyze_code"]

        cases = _get_replay_cases(rule_id, include_counterexamples=True)
        if not cases:
            return json.dumps({
                "rule_id": rule_id,
                "passed": False,
                "total": 0,
                "failures": [],
                "error": f"No replay cases found for rule '{rule_id}'",
            })
        results = [_evaluate_replay_case(replay_case, analyze_code) for replay_case in cases]
        failures = [
            {
                "replay_case_id": result["replay_case_id"],
                "failures": result["failures"],
                "findings": result.get("findings", []),
                "cycles": result.get("cycles"),
            }
            for result in results
            if not result["passed"]
        ]

        return json.dumps({
            "rule_id": rule_id,
            "passed": not failures,
            "total": len(results),
            "failures": failures,
        }, default=str)
    except Exception as e:
        _raise_tool_error(e)


def update_finding(finding_id: int, evidence: dict | None = None,
                   severity: str | None = None,
                   action: str | None = None) -> str:
    """Update an existing finding's evidence, severity, or action.
    Use this when investigation reveals additional data after the finding was created."""
    try:
        with _conn() as conn:
            updates = []
            params: list = []
            if evidence is not None:
                updates.append("evidence = evidence || %s::jsonb")  # merge, not replace
                params.append(json.dumps(evidence, default=str))
            if severity is not None:
                updates.append("severity = %s")
                params.append(severity)
            if action is not None:
                updates.append("action = %s")
                params.append(action)
            if not updates:
                _input_error("No fields to update")
            params.append(finding_id)
            cur = conn.cursor()
            cur.execute(
                f"UPDATE {_t('patrol_findings')} SET {', '.join(updates)} WHERE finding_id = %s",
                params,
            )
            conn.commit()
            if cur.rowcount == 0:
                return _not_found("Finding", finding_id)
        return json.dumps({"status": "updated", "finding_id": finding_id})
    except Exception as e:
        _raise_tool_error(e)


# ── Accuracy ──────────────────────────────────────────────────────────────

def execute_node_action(hostname: str, action: str, triaged_label: str = "triaged_hardware", summary: str = "") -> str:
    """Execute a node action (cordon/drain/alert) via alert-manager API."""
    if action not in ("cordon", "drain", "alert"):
        _input_error(f"Invalid action: {action}")
    try:
        from patrol_cron.actions import _submit_triage_alert
        ok, err = _submit_triage_alert(hostname=hostname, action=action,
                                  alertname="patrol_manual_action",
                                  triaged_label=triaged_label,
                                  evidence={"summary": summary, "source": "inspect-infra-issue"})
        if ok:
            return json.dumps({"status": "sent", "hostname": hostname, "action": action})
        return json.dumps({"status": "failed", "hostname": hostname, "action": action,
                           "error": err})
    except Exception as e:
        _raise_tool_error(e)


def get_rule_accuracy(rule_id: str, window_days: int = 14, since_code_update: bool = True,
                      rule_code_hash: Optional[str] = None,
                      collector_config_hash: Optional[str] = None) -> str:
    """Get windowed accuracy for a rule from patrol_findings.

    Args:
        rule_id: The rule to check.
        window_days: Rolling window in days (default 14). Only used when
            since_code_update=False or the finding has no hashes.
        since_code_update: If True (default), only count findings produced by
            the CURRENT rule code AND collector config (both hashes must match current).
            Historical findings from old code or old collectors are irrelevant for current accuracy.
        rule_code_hash: Specific rule code hash to filter by (from list_rule_versions).
            Overrides since_code_update. Use to compare accuracy across versions.
        collector_config_hash: Specific collector config hash to filter by.
            Must be used together with rule_code_hash.
    """
    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            if rule_code_hash:
                # Query accuracy for a specific rule version
                extra_filter = ""
                params = [rule_id, rule_code_hash]
                if collector_config_hash:
                    extra_filter = "AND f.collector_config_hash = %s"
                    params.append(collector_config_hash)
                cur.execute(
                    f"""SELECT
                         count(*) AS total,
                         count(*) FILTER (WHERE verdict = 'confirmed') AS confirmed,
                         count(*) FILTER (WHERE verdict = 'rejected') AS rejected,
                         count(*) FILTER (WHERE verdict IS NOT NULL) AS judged
                       FROM {_t('patrol_findings')} f
                       WHERE f.rule_id = %s
                         AND f.rule_code_hash = %s
                         {extra_filter}""",
                    params,
                )
                row = cur.fetchone()
                total = row["total"] or 0
                judged = row["judged"] or 0
                confirmed = row["confirmed"] or 0
                accuracy = confirmed / judged if judged > 0 else None
                return json.dumps({
                    "rule_id": rule_id,
                    "rule_code_hash": rule_code_hash,
                    "collector_config_hash": collector_config_hash,
                    "total": total,
                    "judged": judged,
                    "confirmed": confirmed,
                    "rejected": row["rejected"] or 0,
                    "accuracy": accuracy,
                    "mode": "by_version",
                }, default=str)

            elif since_code_update:
                # Use hash-based filtering: only findings from current rule AND collector code
                cur.execute(
                    f"""SELECT
                         count(*) AS total,
                         count(*) FILTER (WHERE verdict = 'confirmed') AS confirmed,
                         count(*) FILTER (WHERE verdict = 'rejected') AS rejected,
                         count(*) FILTER (WHERE verdict IS NOT NULL) AS judged
                       FROM {_t('patrol_findings')} f
                       JOIN {_t('patrol_rules')} r ON f.rule_id = r.rule_id
                       WHERE f.rule_id = %s
                         AND f.rule_code_hash IS NOT NULL
                         AND f.rule_code_hash = md5(r.analyze_code)
                         AND f.collector_config_hash IS NOT NULL
                         AND f.collector_config_hash = (
                           SELECT cv.config_hash FROM collector_versions cv
                           WHERE cv.name = r.binds_to ORDER BY cv.saved_at DESC LIMIT 1
                         )""",
                    (rule_id,),
                )
            else:
                cur.execute(
                    f"""SELECT
                         count(*) AS total,
                         count(*) FILTER (WHERE verdict = 'confirmed') AS confirmed,
                         count(*) FILTER (WHERE verdict = 'rejected') AS rejected,
                         count(*) FILTER (WHERE verdict IS NOT NULL) AS judged
                       FROM {_t('patrol_findings')}
                       WHERE rule_id = %s
                         AND detected_at > NOW() - make_interval(days => %s)""",
                    (rule_id, window_days),
                )
            row = cur.fetchone()
            total = row["total"] or 0
            judged = row["judged"] or 0
            confirmed = row["confirmed"] or 0
            accuracy = confirmed / judged if judged > 0 else None
            result = {
                "rule_id": rule_id,
                "total": total,
                "judged": judged,
                "confirmed": confirmed,
                "rejected": row["rejected"] or 0,
                "accuracy": accuracy,
            }
            if since_code_update:
                result["mode"] = "since_code_update"
            else:
                result["mode"] = f"window_{window_days}d"
            return json.dumps(result)
    except Exception as e:
        _raise_tool_error(e)


def compare_rule_versions(rule_id: str) -> str:
    """Compare accuracy across rule code versions and collector config versions.

    Returns per-version accuracy, rejection reasons, finding volume, and collector
    change history so the agent can diagnose regressions. Does NOT interpret —
    the agent must analyze the data and decide rollback vs refine.

    Args:
        rule_id: The rule to compare versions for.
    """
    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # 1. Get version list
            cur.execute(
                """SELECT code_hash, stage, saved_at, saved_by, LENGTH(analyze_code) as code_length
                   FROM patrol_rule_versions WHERE rule_id = %s
                   ORDER BY saved_at DESC LIMIT 5""",
                (rule_id,),
            )
            versions = [dict(r) for r in cur.fetchall()]

            # 2. Get collector version list
            cur.execute(
                """SELECT r.binds_to FROM patrol_rules r WHERE r.rule_id = %s""",
                (rule_id,),
            )
            binds_row = cur.fetchone()
            collector_name = binds_row["binds_to"] if binds_row else None
            collector_versions = []
            if collector_name:
                cur.execute(
                    """SELECT config_hash, sources, target_filter, schedule_sec, enabled, saved_at, saved_by
                       FROM collector_versions WHERE name = %s
                       ORDER BY saved_at DESC LIMIT 5""",
                    (collector_name,),
                )
                collector_versions = [dict(r) for r in cur.fetchall()]

            # 3. Accuracy per version
            version_accuracy = []
            for v in versions:
                cur.execute(
                    f"""SELECT
                         count(*) AS total,
                         count(*) FILTER (WHERE verdict = 'confirmed') AS confirmed,
                         count(*) FILTER (WHERE verdict = 'rejected') AS rejected,
                         count(*) FILTER (WHERE verdict IS NOT NULL) AS judged
                       FROM {_t('patrol_findings')} f
                       WHERE f.rule_id = %s AND f.rule_code_hash = %s""",
                    (rule_id, v["code_hash"]),
                )
                row = cur.fetchone()
                judged = row["judged"] or 0
                version_accuracy.append({
                    "code_hash": v["code_hash"][:12],
                    "saved_at": str(v["saved_at"]),
                    "stage": v["stage"],
                    "total": row["total"] or 0,
                    "judged": judged,
                    "confirmed": row["confirmed"] or 0,
                    "rejected": row["rejected"] or 0,
                    "accuracy": (row["confirmed"] or 0) / judged if judged > 0 else None,
                })

            # 4. Current code accuracy
            cur.execute(
                f"""SELECT
                     count(*) AS total,
                     count(*) FILTER (WHERE verdict = 'confirmed') AS confirmed,
                     count(*) FILTER (WHERE verdict = 'rejected') AS rejected,
                     count(*) FILTER (WHERE verdict IS NOT NULL) AS judged
                   FROM {_t('patrol_findings')} f
                   JOIN {_t('patrol_rules')} r ON f.rule_id = r.rule_id
                   WHERE f.rule_id = %s
                     AND f.rule_code_hash = md5(r.analyze_code)""",
                (rule_id,),
            )
            current = cur.fetchone()
            current_judged = current["judged"] or 0
            current_accuracy = (current["confirmed"] or 0) / current_judged if current_judged > 0 else None

            # 5. Rejection reasons for current code
            cur.execute(
                f"""SELECT verdict_reason, count(*) as cnt
                   FROM {_t('patrol_findings')} f
                   JOIN {_t('patrol_rules')} r ON f.rule_id = r.rule_id
                   WHERE f.rule_id = %s AND f.verdict = 'rejected'
                     AND f.rule_code_hash = md5(r.analyze_code)
                     AND f.verdict_reason IS NOT NULL
                   GROUP BY verdict_reason ORDER BY cnt DESC LIMIT 5""",
                (rule_id,),
            )
            current_rejection_reasons = [dict(r) for r in cur.fetchall()]

            # 6. Rejection reasons for previous version
            prev_rejection_reasons = []
            if versions:
                prev_hash = versions[0]["code_hash"]
                cur.execute(
                    f"""SELECT verdict_reason, count(*) as cnt
                       FROM {_t('patrol_findings')} f
                       WHERE f.rule_id = %s AND f.verdict = 'rejected'
                         AND f.rule_code_hash = %s
                         AND f.verdict_reason IS NOT NULL
                       GROUP BY verdict_reason ORDER BY cnt DESC LIMIT 5""",
                    (rule_id, prev_hash),
                )
                prev_rejection_reasons = [dict(r) for r in cur.fetchall()]

            # 7. Volume: findings per day before and after code change
            code_change_time = versions[0]["saved_at"] if versions else None
            volume_before = volume_after = None
            if code_change_time:
                cur.execute(
                    f"""SELECT count(*) as cnt, MIN(detected_at) as earliest
                       FROM {_t('patrol_findings')} f
                       WHERE f.rule_id = %s AND f.detected_at < %s""",
                    (rule_id, code_change_time),
                )
                before_row = cur.fetchone()
                if before_row and before_row["cnt"] > 0 and before_row["earliest"]:
                    days = max((code_change_time - before_row["earliest"]).total_seconds() / 86400, 1)
                    volume_before = round(before_row["cnt"] / days, 2)

                cur.execute(
                    f"""SELECT count(*) as cnt FROM {_t('patrol_findings')} f
                       WHERE f.rule_id = %s AND f.detected_at >= %s""",
                    (rule_id, code_change_time),
                )
                after_row = cur.fetchone()
                if after_row and after_row["cnt"] > 0:
                    days_since = max((datetime.now(timezone.utc) - code_change_time).total_seconds() / 86400, 1)
                    volume_after = round(after_row["cnt"] / days_since, 2)

            result = {
                "rule_id": rule_id,
                "collector_name": collector_name,
                "current_accuracy": current_accuracy,
                "current_total": current["total"] or 0,
                "current_judged": current_judged,
                "version_accuracy": version_accuracy,
                "collector_versions": [{"config_hash": v["config_hash"][:12], "saved_at": str(v["saved_at"])} for v in collector_versions],
                "current_rejection_reasons": current_rejection_reasons,
                "previous_rejection_reasons": prev_rejection_reasons,
                "volume_before_per_day": volume_before,
                "volume_after_per_day": volume_after,
            }
            return json.dumps(result, default=str)
    except Exception as e:
        _raise_tool_error(e)


def get_rule_rejection_reasons(rule_id: str, limit: int = 20) -> str:
    """Get common rejection reasons for a rule's findings.

    Surfaces anti-patterns from the detection feedback loop. Use before
    refining a rule — common rejection reasons tell you WHY the rule fires
    falsely, which is more actionable than accuracy alone.

    Args:
        rule_id: The rule to check.
        limit: Max reasons to return (default 20).
    """
    try:
        with _conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"""SELECT verdict_reason, count(*) AS cnt
                   FROM {_t('patrol_findings')}
                   WHERE rule_id = %s
                     AND verdict = 'rejected'
                     AND verdict_reason IS NOT NULL
                     AND verdict_reason != ''
                   GROUP BY verdict_reason
                   ORDER BY cnt DESC
                   LIMIT %s""",
                (rule_id, limit),
            )
            rows = cur.fetchall()
            return json.dumps({"rule_id": rule_id, "rejection_reasons": rows})
    except Exception as e:
        _raise_tool_error(e)


def get_rule_bad_feedback_rate(rule_id: str) -> str:
    """Get detection-attributed bad RMA feedback rate for a rule.

    Args:
        rule_id: The rule to check.
    """
    try:
        from patrol_cron import db as patrol_db
        return json.dumps(patrol_db.get_rule_bad_feedback_rate(rule_id), default=str)
    except Exception as e:
        _raise_tool_error(e)


def list_dirty_reconciliation_rules(limit: int = 50) -> str:
    """List rules flagged for reconciliation follow-up.

    Args:
        limit: Max rules to return. Invalid values default to 50; max is 500.
    """
    try:
        from patrol_cron import db as patrol_db
        return json.dumps({"rules": patrol_db.list_dirty_reconciliation_rules(limit)}, default=str)
    except Exception as e:
        _raise_tool_error(e)


# ── MCP Registration ─────────────────────────────────────────────────────

# ── Data Source Tools (for investigation & debugging) ─────────────────────

def query_prometheus(query: str, step: str = "60s", lookback_sec: int = 600) -> str:
    """Run a PromQL query and return results.

    Key metrics (use list_prometheus_labels to check labels before querying):

    Per-node (have node_name = hostname):
      - pai_node_count{node_name, ready, unschedulable, virtual_cluster, disk_pressure, memory_pressure}
      - gpu_utilization{node_name, minor_number} / gpu_mem_utilization{node_name}
      - configured_gpu_count{node_name}
      - node_xid_error{node_name, gpu_id}

    Per-node (have instance = IP:port, NO node_name):
      - node_cpu_util{instance} / node_mem_util{instance}
      - node_filesystem_avail_bytes{instance, mountpoint}
      - dcgm_*{instance, gpu_id} (dcgm_gpu_utilization, dcgm_ecc_dbe_volatile_total, dcgm_power_usage, etc.)
      - ib_port_*{instance, ib_port} (ib_port_rcv_errors, ib_port_xmit_discards, ib_port_physical_state)

    Cluster-level (allocation capacity):
      - virtual_cluster_stat{vc_stat, sku, metric="resourcesTotal"|"resourcesGuaranteed"}
      - k8s_node_gpu_available{host_ip} / k8s_node_gpu_total{host_ip} / k8s_node_gpu_reserved{host_ip}

    Alerts:
      - ALERTS{alertname, alertstate="firing"|"pending", severity}
      - Use list_prometheus_labels("ALERTS") to discover available labels and active alert names

    Args:
        query: PromQL query string (e.g. 'pai_node_count{virtual_cluster="b300", ready="false"}').
        step: Resolution step (default "60s").
        lookback_sec: How far back to look in seconds (default 600).
    """
    try:
        from patrol_cron.collectors.nfd_data_sources import PrometheusClient
        client = PrometheusClient()
        import time
        end = int(time.time())
        start = end - lookback_sec
        data = client.query_range(query, start, end, step=step)
        if data is None:
            raise RuntimeError("Query returned None. Check PROMETHEUS_SERVER_URI and LTP_TOKEN.")
        results = data.get("result", [])
        targets = []
        for series in results[:50]:  # cap at 50 to avoid huge responses
            metric = series.get("metric", {})
            node = metric.get("node_name") or metric.get("instance", "").split(":")[0]
            values = series.get("values", [])
            targets.append({"node": node, "metric": metric, "values": values[-3:]})
        return json.dumps({"total": len(results), "targets": targets}, default=str)
    except Exception as e:
        _raise_tool_error(e)


def list_prometheus_metrics(filter: str = "") -> str:
    """List available Prometheus metric names. Optionally filter by substring.

    Use this to discover what metrics exist before writing queries.
    Common filters: 'node_', 'pai_', 'k8s_', 'gpu_', 'dcgm_', 'ib_', 'disk', 'mem'.

    Args:
        filter: Substring to filter metric names (case-insensitive). Empty = all metrics.
    """
    try:
        from patrol_cron.collectors.nfd_data_sources import PrometheusClient
        client = PrometheusClient()
        names = client.get_metric_names()
        if names is None:
            raise RuntimeError("Failed to fetch metrics. Check PROMETHEUS_SERVER_URI and LTP_TOKEN.")
        if filter:
            f = filter.lower()
            names = [n for n in names if f in n.lower()]
        return json.dumps({"total": len(names), "metrics": names[:100], "truncated": len(names) > 100}, default=str)
    except Exception as e:
        _raise_tool_error(e)


def list_prometheus_labels(metric: str) -> str:
    """List label names and sample values for a Prometheus metric.

    Use this AFTER list_prometheus_metrics to understand what labels and values
    a metric supports. Essential for building correct queries.

    Example: list_prometheus_labels("pai_node_count") returns labels like
      node_name: ["lg-cmc-..."], ready: ["true","false"], virtual_cluster: ["b300","h200","cpu"]

    Args:
        metric: Metric name (e.g. 'pai_node_count', 'node_filesystem_avail_bytes').
    """
    try:
        from patrol_cron.collectors.nfd_data_sources import PrometheusClient
        client = PrometheusClient()
        labels = client.get_metric_labels(metric)
        if labels is None:
            raise RuntimeError(f"Failed to fetch labels for '{metric}'. Check metric name and Prometheus connectivity.")
        return json.dumps({"metric": metric, "labels": labels, "label_count": len(labels)}, default=str, indent=2)
    except Exception as e:
        _raise_tool_error(e)


def ssh_run(hostname: str, commands: list, user: Optional[str] = None,
            interactive: bool = False, paging_cmd: str = "",
            timeout: int = 15) -> str:
    """Run SSH commands on a target and return raw output.

    Args:
        hostname: Target hostname or IP.
        commands: List of commands to run.
        user: SSH user (default from SSH_USER env).
        interactive: True for switches (pexpect + password), False for nodes (subprocess + agent).
        paging_cmd: Paging disable command for interactive mode (e.g. "terminal length 0").
        timeout: SSH timeout in seconds.
    """
    try:
        from patrol_cron.collectors.ssh import _ssh_run_commands, SshCollector, EVIDENCE_DB_URL
        import psycopg2

        # Resolve hostname to IP if needed
        ip = hostname
        if not hostname.replace(".", "").isdigit():
            # Try switch inventory
            try:
                with psycopg2.connect(EVIDENCE_DB_URL) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT ip FROM switch_inventory WHERE hostname = %s", (hostname,))
                    row = cur.fetchone()
                    if row:
                        ip = row[0]
            except Exception:
                pass
            # Try platform DB
            if ip == hostname:
                try:
                    from patrol_cron.collectors.ssh import PLATFORM_DB_URL
                    with psycopg2.connect(PLATFORM_DB_URL) as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT ip->>0 FROM ltp_sdk.physical_node_onboard_records WHERE hostname = %s ORDER BY timestamp DESC LIMIT 1", (hostname,))
                        row = cur.fetchone()
                        if row:
                            ip = row[0]
                except Exception:
                    pass

        ssh_user = user or os.environ.get("SSH_USER", "root")
        password = os.environ.get("SSH_PASSWORD", "") if not interactive else os.environ.get("SWITCH_SSH_PASSWORD", "")
        if interactive and not user:
            ssh_user = os.environ.get("SWITCH_SSH_USER", "admin")

        result = _ssh_run_commands(ip, ssh_user, password, commands, paging_cmd, timeout, interactive=interactive)
        return json.dumps({"hostname": hostname, "ip": ip, **result}, default=str)
    except Exception as e:
        _raise_tool_error(e)


def read_collector_log(collector_name: str, lines: int = 50, grep: Optional[str] = None) -> str:
    """Read a collector's log file.

    Args:
        collector_name: Collector name (e.g. "switch_health_ib").
        lines: Number of lines to return (default 50, from end of file).
        grep: Optional grep pattern to filter lines.
    """
    import subprocess
    log_path = f"/tmp/patrol_cron/logs/{collector_name}.log"
    try:
        if grep:
            proc = subprocess.run(
                ["grep", "-i", grep, log_path],
                capture_output=True, text=True, timeout=10,
            )
            output = proc.stdout.strip()
            result_lines = output.split("\n")[-lines:] if output else []
        else:
            proc = subprocess.run(
                ["tail", f"-{lines}", log_path],
                capture_output=True, text=True, timeout=10,
            )
            result_lines = proc.stdout.strip().split("\n") if proc.stdout else []
        return json.dumps({"collector": collector_name, "lines": result_lines, "total": len(result_lines)})
    except FileNotFoundError:
        raise FileNotFoundError(f"Log file not found: {log_path}")
    except Exception as e:
        _raise_tool_error(e)


def query_job_metadata(status: Optional[str] = None, lookback_hours: int = 2, limit: int = 20) -> str:
    """Query job metadata — find recent jobs matching filters.

    Args:
        status: Filter by job status ("failed", "running", etc.). None for all.
        lookback_hours: How far back to look (default 2 hours).
        limit: Max jobs to return (default 20).
    """
    try:
        from patrol_cron.collectors.nfd_data_sources import JobMetadataClient
        import time
        meta = JobMetadataClient()
        end = int(time.time())
        start = end - lookback_hours * 3600
        basic = meta.get_job_metadata()
        filters = {"status": [status]} if status else {}
        jobs = meta.get_filtered_job_attempts(start, end, filters, basic_jobs=basic)
        results = []
        for k, v in list(jobs.items())[:limit]:
            results.append({
                "key": k,
                "username": v.get("username"),
                "name": v.get("name"),
                "state": v.get("state"),
                "nodes": list(v.get("nodes", {}).keys())[:5],
                "node_count": len(v.get("nodes", {})),
            })
        return json.dumps({"total": len(jobs), "jobs": results}, default=str)
    except Exception as e:
        _raise_tool_error(e)


def fetch_job_logs(user: str, job_name: str, attempt_id: int = 0, patterns: Optional[list] = None,
                   max_entries: int = 20, log_type: str = "user-all") -> str:
    """Fetch and pattern-match logs for a specific job.

    Args:
        user: OpenPAI username (e.g. "yang.wang").
        job_name: Job name without user prefix or attempt suffix (e.g. "pxs-yiwen_hu-v3_bailing_cpt32k_600b_sqrt_8n_lr5e4_extA-daa84207").
        attempt_id: Attempt number, 0-based (default 0). Check query_job_metadata for available attempts.
        patterns: List of regex patterns to match (e.g. [{"regex": ".*ERROR.*"}]).
                  If None, returns raw log snippets.
        max_entries: Max matched entries per node.
        log_type: Which log stream to fetch (default "user-all").
                  "user-all"    — merged stdout+stderr of the user process.
                  "user-stdout" — stdout only.
                  "user-stderr" — stderr only.
                  "init"        — PAI container init script (env setup, trap handlers).
                                  Use when the container never started or env setup failed.
                  "runtime"     — OpenPAI runtime (pre-commands, apt installs, user script launch).
                                  Use when pre-user-code setup failed.
                  "barrier"     — Gang-scheduling barrier log.
                                  Use when a multi-node job never starts or one pod is stuck waiting.
    """
    # Mock replay support: if REPLAY_MOCK_DIR is set, check for a pre-recorded response.
    _mock_dir = os.environ.get("REPLAY_MOCK_DIR", "")
    if _mock_dir:
        _raw_key = f"{user}__{job_name}"
        _safe_key = _raw_key.replace("/", "_").replace(":", "_").replace(" ", "_").replace(".", "_")
        _mock_path = os.path.join(_mock_dir, f"fetch_job_logs__{_safe_key}.json")
        if os.path.isfile(_mock_path):
            with open(_mock_path) as _f:
                _data = json.load(_f)
            if "error" in _data:
                raise RuntimeError(_data["error"])
            return _data.get("result", "")
        if os.environ.get("REPLAY_MOCK_STRICT", "") == "1":
            raise RuntimeError(f"No mock data for fetch_job_logs({_raw_key}). Check {_mock_dir}/")

    try:
        from patrol_cron.collectors.nfd_data_sources import JobMetadataClient, JobLogsClient
        import time
        meta = JobMetadataClient()

        # Fast path: look up one job directly instead of scanning all failed jobs.
        full_job_name = f"{user}~{job_name}"
        job_key = f"{full_job_name}~{attempt_id}"

        # 1 API call: basic job metadata (cached 2 min)
        basic = meta.get_job_metadata()
        job_data = basic.get(job_key) or basic.get(full_job_name + "~0") or {}

        # 1-2 API calls: attempt metadata (list attempts, then get latest)
        if not job_data.get("taskRoles") or not job_data.get("frameworkName"):
            # Try to get attempt metadata directly with retry
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    attempt_ids = meta.get_job_attempts_list(full_job_name)
                except Exception:
                    attempt_ids = []
                if not attempt_ids:
                    attempt_ids = [attempt_id]
                latest_attempt = max(attempt_ids)
                job_data = meta.get_job_attempt_metadata(full_job_name, latest_attempt, job_data=job_data)
                if job_data:
                    break
                if attempt < max_retries - 1:
                    backoff = (2 ** attempt) * 3
                    logger.warning(f"fetch_job_logs: metadata retry {attempt+1}/{max_retries} for {full_job_name}, backoff {backoff}s")
                    time.sleep(backoff)
            if not job_data:
                raise RuntimeError(f"Could not fetch metadata for {full_job_name} attempt {attempt_id} after {max_retries} retries")

        # Now fetch logs with the metadata we have
        logs = JobLogsClient()
        result = logs.get_job_logs(job_name=full_job_name, job_meta=job_data, tail=True, log_type=log_type)

        if not result:
            return json.dumps({"job": full_job_name, "attempt": attempt_id, "nodes": {}, "note": "No logs available"})

        node_results = {}
        for node, content in result.items():
            if not content:
                node_results[node] = {"log_length": 0}
                continue
            if patterns:
                entries = logs.parse_logs_with_patterns(content, patterns, max_entries=max_entries)
                node_results[node] = {
                    "log_length": len(content),
                    "matched": len(entries),
                    "entries": [e.message[:500] for e in entries],
                }
            else:
                node_results[node] = {
                    "log_length": len(content),
                    "preview": content[:2000],
                }

        return json.dumps({"job": full_job_name, "attempt": attempt_id, "nodes": node_results}, default=str)
    except Exception as e:
        _raise_tool_error(e)


def fetch_node_logs(node_ip: str, log_paths: list, patterns: list,
                    max_entries: int = 20, lookback_hours: int = 2) -> str:
    """Fetch and pattern-match system logs from a node via log-manager.

    Args:
        node_ip: Node IP address.
        log_paths: Log files to read (e.g. ["kern.log", "syslog"]).
        patterns: Regex patterns to match (e.g. [{"regex": ".*NVRM.*Xid.*"}]).
        max_entries: Max entries to return.
        lookback_hours: How far back to search.
    """
    try:
        from patrol_cron.collectors.nfd_data_sources import NodeLogsClient
        import time
        client = NodeLogsClient()
        end = int(time.time())
        start = end - lookback_hours * 3600
        entries = client.collect_node_logs(
            node_ip=node_ip,
            log_paths=log_paths,
            patterns=patterns,
            max_entries=max_entries,
            start_time_ts=start,
            end_time_ts=end,
        )
        results = []
        for e in (entries or []):
            results.append({"message": e.message[:1000], "fields": getattr(e, "fields", {})})
        return json.dumps({"node_ip": node_ip, "total": len(results), "entries": results}, default=str)
    except Exception as e:
        _raise_tool_error(e)


# ── MCP Registration ─────────────────────────────────────────────────────

def run_collector_once(collector_name: str, sample: int = 0) -> str:
    """Run a collector once and return summary of results.

    Useful for testing a new or modified collector without waiting for cron.
    If sample > 0, temporarily limits target_filter to that many targets.

    Args:
        collector_name: Name of the collector to run.
        sample: Limit to N targets for testing (0 = no limit).
    """
    try:
        from patrol_cron import db as patrol_db
        from patrol_cron.engine import run_collector as _engine_run

        coll = patrol_db.get_collector_by_name(collector_name)
        if not coll:
            return _not_found("Collector", collector_name)

        if sample > 0:
            tf = coll.get("target_filter") or {}
            tf["sample"] = sample
            coll["target_filter"] = tf

        result = _engine_run(coll)

        ssh_statuses = [_ssh_payload_ok(t.payload) for t in result.targets]
        ssh_known = [status for status in ssh_statuses if status is not None]
        ok_count = sum(1 for status in ssh_known if status)
        fail_count = sum(1 for status in ssh_known if not status)

        sample_ok = None
        sample_fail = None
        for t in result.targets:
            ssh_ok = _ssh_payload_ok(t.payload)
            if ssh_ok is True and not sample_ok:
                sample_ok = {"id": t.id, "payload": t.payload}
            if ssh_ok is False and not sample_fail:
                sample_fail = {"id": t.id, "payload": t.payload}
            if sample_ok and sample_fail:
                break

        return json.dumps({
            "collector": collector_name,
            "total": len(result.targets),
            "ssh_ok": ok_count,
            "ssh_failed": fail_count,
            "errors": result.errors[:5],
            "duration": result.duration,
            "sample_ok": sample_ok,
            "sample_fail": sample_fail,
        }, default=str)
    except Exception as e:
        _raise_tool_error(e)


def test_rule_once(
    rule_id: str,
    collector_name: str = "",
    sample: int = 0,
    dry_run: bool = True,
    analyze_code: str | None = None,
) -> str:
    """Run a rule's analyze() against the latest collector data and return observations.

    This is the rule equivalent of run_collector_once — test immediately
    after updating analyze_code without waiting for the next cron cycle.

    Steps performed:
    1. Load rule (analyze_code, stage, state) from DB
    2. Load or run collector to get CollectionResult
    3. Execute analyze_code via run_sandboxed → RuleResult
    4. Return observations (lifecycle-aware) + legacy findings WITHOUT persisting (dry_run=True)

    If the rule is lifecycle-enabled, observations include signal_key, status
    (bad/healthy/unknown), and lifecycle metadata. If not, legacy findings are
    returned as observations with status="bad".

    Args:
        rule_id: The rule to test.
        collector_name: Override collector to run. If empty, uses rule's binds_to.
        sample: Limit collector to N targets (0 = no limit).
        dry_run: If True (default), do NOT persist findings. Just return them.
        analyze_code: Optional candidate code to run instead of current DB code.
    """
    try:
        from patrol_cron import db as patrol_db
        from patrol_cron.engine import run_collector as _engine_run
        from patrol_cron.analyzer import run_sandboxed

        # 1. Load rule
        rule = patrol_db.get_rule(rule_id)
        if not rule:
            return _not_found("Rule", rule_id)

        coll_name = collector_name or rule.get("binds_to", "")
        if not coll_name:
            _input_error(f"Rule {rule_id} has no binds_to and no collector_name given")

        # 2. Load collector and run
        coll = patrol_db.get_collector_by_name(coll_name)
        if not coll:
            return _not_found("Collector", coll_name)

        if sample > 0:
            tf = coll.get("target_filter") or {}
            tf["sample"] = sample
            coll["target_filter"] = tf

        result = _engine_run(coll)

        # 3. Run analyze → RuleResult
        state = patrol_db.load_rule_state(rule_id)
        code_to_run = analyze_code if analyze_code is not None else rule["analyze_code"]
        rule_result = run_sandboxed(code_to_run, result, state)

        # 4. Format observations and findings
        observations_out = [_observation_to_dict(obs) for obs in rule_result.observations]
        findings_out = [_finding_to_dict(f) for f in rule_result.legacy_findings()]

        ssh_statuses = [_ssh_payload_ok(t.payload) for t in result.targets]
        ssh_known = [status for status in ssh_statuses if status is not None]
        ok_count = sum(1 for status in ssh_known if status)
        fail_count = sum(1 for status in ssh_known if not status)

        # 5. Optionally persist via lifecycle engine or legacy path
        persisted = 0
        if not dry_run:
            import hashlib
            from patrol_cron.run_collector_job import _collector_config_hash

            rule_code_hash = hashlib.md5(code_to_run.encode()).hexdigest()
            provenance = {
                "collector_snapshot_id": "",
                "raw_evidence_hash": "",
                "rule_version_at": str(rule.get("updated_at", "")),
                "rule_code_hash": rule_code_hash,
                "collector_config_hash": _collector_config_hash(coll),
            }

            if rule_result.lifecycle_enabled:
                from patrol_cron.lifecycle import apply_rule_result
                apply_rule_result(rule_result, rule, coll, provenance)
                persisted = len(findings_out)
            else:
                # Legacy path: finding_exists + insert_finding
                for f in rule_result.legacy_findings():
                    if patrol_db.finding_exists(rule_id, f.target_id, f.action):
                        continue
                    from patrol_cron.run_collector_job import execute_finding
                    execute_finding(f, rule["stage"], rule_id=rule_id)
                    patrol_db.insert_finding(
                        rule_id=rule_id,
                        target_id=f.target_id,
                        target_type=coll.get("target_type", "unknown"),
                        severity=f.severity,
                        action=f.action,
                        action_params=f.action_params,
                        evidence=f.evidence,
                        confidence=f.confidence,
                        **provenance,
                    )
                    persisted += 1

        # 6. Summarize observation statuses
        status_counts = {}
        for obs in observations_out:
            status_counts[obs["status"]] = status_counts.get(obs["status"], 0) + 1

        return json.dumps({
            "rule_id": rule_id,
            "collector": coll_name,
            "stage": rule.get("stage"),
            "lifecycle_enabled": rule_result.lifecycle_enabled,
            "collection": {
                "total": len(result.targets),
                "ssh_ok": ok_count,
                "ssh_failed": fail_count,
            },
            "observations_count": len(observations_out),
            "observations": observations_out[:20],
            "findings_count": len(findings_out),
            "findings": findings_out[:20],
            "status_counts": status_counts,
            "new_state": rule_result.state,
            "dry_run": dry_run,
            "persisted": persisted,
        }, default=str)
    except Exception as e:
        import traceback
        raise RuntimeError(f"{e}\n{traceback.format_exc()}") from e


def query_patrol_db(sql: str) -> str:
    """Run a read-only SQL query against the patrol database (ltp_agent).

    Returns results as JSON array of objects. Only SELECT statements are allowed.

    Tables: patrol_rules, collectors, patrol_findings, rule_state, collector_snapshots,
    collector_versions, patrol_rule_versions, investigation_evidence, agent_memory,
    rule_replay_cases, case_memory, analysis_problems.

    Example queries:
        -- Unjudged findings by rule
        SELECT rule_id, count(*) as total, count(*) FILTER (WHERE active) as active,
               count(*) FILTER (WHERE verdict IS NULL AND NOT resolved) as unjudged
        FROM patrol_findings WHERE NOT resolved GROUP BY rule_id ORDER BY unjudged DESC;

        -- Recent findings
        SELECT finding_id, rule_id, target_id, active, verdict, detected_at
        FROM patrol_findings ORDER BY detected_at DESC LIMIT 20;

    Args:
        sql: SQL query (SELECT only; no INSERT/UPDATE/DELETE/DROP/ALTER)
    """
    sql_stripped = sql.strip().upper()
    for kw in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "VACUUM"):
        if sql_stripped.startswith(kw):
            _input_error(f"Only SELECT queries allowed (got {kw})")
    if not sql_stripped.startswith("SELECT") and not sql_stripped.startswith("WITH"):
        _input_error("Only SELECT/WITH queries allowed")

    try:
        conn = psycopg2.connect(EVIDENCE_DB_URL)
        conn.set_session(readonly=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql)
        rows = cur.fetchall()
        result = [dict(r) for r in rows]
        for row in result:
            for k, v in row.items():
                if isinstance(v, (datetime,)):
                    row[k] = v.isoformat()
                elif hasattr(v, "adapt"):
                    row[k] = str(v)
        cur.close()
        conn.close()
        return json.dumps({"rows": result, "count": len(result)}, default=str)
    except Exception as e:
        _raise_tool_error(e)


def register_patrol_tools(mcp):
    """Register all patrol tools with a FastMCP server instance."""
    # Collectors
    mcp.tool()(create_collector)
    mcp.tool()(list_collectors)
    mcp.tool()(get_collector)
    mcp.tool()(get_collector_health)
    mcp.tool()(update_collector)
    # Rules
    mcp.tool()(create_rule)
    mcp.tool()(update_rule_stage)
    mcp.tool()(update_rule_code)
    mcp.tool()(toggle_rule)
    mcp.tool()(list_rules)
    mcp.tool()(get_rule_detail)
    mcp.tool()(list_rule_versions)
    mcp.tool()(rollback_rule)
    mcp.tool()(list_collector_versions)
    mcp.tool()(rollback_collector)
    mcp.tool()(list_dirty_reconciliation_rules)
    # Findings
    mcp.tool()(list_findings)
    mcp.tool()(record_verdict)
    mcp.tool()(get_finding_raw_data)
    mcp.tool()(reconcile_finding)
    mcp.tool()(create_finding)
    mcp.tool()(update_finding)
    mcp.tool()(get_rule_accuracy)
    mcp.tool()(compare_rule_versions)
    mcp.tool()(get_rule_rejection_reasons)
    mcp.tool()(get_rule_bad_feedback_rate)
    mcp.tool()(create_rule_replay_case)
    mcp.tool()(create_rule_replay_cases_from_feedback)
    mcp.tool()(list_rule_replay_cases)
    mcp.tool()(replay_rule_case)
    mcp.tool()(run_rule_replay_suite)
    mcp.tool()(execute_node_action)
    mcp.tool()(execute_job_action)
    # Data sources (for investigation & debugging)
    mcp.tool()(query_patrol_db)
    mcp.tool()(query_prometheus)
    mcp.tool()(list_prometheus_metrics)
    mcp.tool()(list_prometheus_labels)
    mcp.tool()(ssh_run)
    mcp.tool()(read_collector_log)
    mcp.tool()(query_job_metadata)
    mcp.tool()(fetch_job_logs)
    mcp.tool()(fetch_node_logs)
    mcp.tool()(run_collector_once)
    mcp.tool()(test_rule_once)


def execute_job_action(job_name: str, action: str, summary: str = "") -> str:
    """Execute a job action via alert-manager.

    Args:
        job_name: Job identifier (user~jobname)
        action: "notify_abnormal_job" (email job owner) or "stop_abnormal_job" (terminate job)
        summary: Why this action is being taken
    """
    if action not in ("notify_abnormal_job", "stop_abnormal_job"):
        _input_error(f"Invalid job action: {action}")
    parts = job_name.split("~", 1)
    if len(parts) != 2:
        _input_error(f"Invalid job name: {job_name}")
    username, job = parts
    from patrol_cron.actions import _send_alert

    action_map = {"notify_abnormal_job": "none", "stop_abnormal_job": "terminate"}
    alert_action = action_map.get(action, "none")

    labels = {
        "alertname": "PAIAbnormalJob",
        "report_type": "abnormal-job",
        "severity": "warn",
        "trigger_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "username": username,
        "job_name": job_name,
        "action": alert_action,
    }
    annotations = {
        "summary": summary or f"Job {job} abnormal",
        "action": alert_action,
        "reason": summary or "Manual action from patrol-cron",
        "notification": "",
    }
    ok = _send_alert(labels, annotations)
    return json.dumps({"status": "sent" if ok else "failed", "job_name": job_name, "action": action})
