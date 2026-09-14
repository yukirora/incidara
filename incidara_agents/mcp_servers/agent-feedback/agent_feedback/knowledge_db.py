"""Knowledge store DB — case_memory read/write on .23:5434.

Separate from platform DB (.19). Used by feedback agent to populate
case_memory, and by triage/repair agents (readonly) to query past outcomes.
"""

from __future__ import annotations

import os
import json
import logging
from contextlib import contextmanager
from decimal import Decimal
from typing import Optional

import psycopg2
import psycopg2.extras
import re

logger = logging.getLogger(__name__)

EVIDENCE_DB_URL = os.getenv(
    "EVIDENCE_DB_URL",
    ""
)

AGENT_NAME = os.getenv("AGENT_NAME", "unknown")


def _is_commit_hash(value: str | None) -> bool:
    """Return True if value looks like a git commit hash (7-40 hex chars),
    not a branch name like 'gap-15-nvswitch-differential-diagnosis'."""
    if not value:
        return False
    return bool(re.fullmatch(r'[0-9a-f]{7,40}', value))

VALID_REPAIR_OUTCOMES = (
    "REPAIR_CONFIRMED",
    "NO_FAULT_FOUND",
    "MISCLASSIFIED",
    "MAINTENANCE_FIX",
    "CONFIG_TASK",
)

VALID_RMA_ATTRIBUTIONS = (
    "detection",
    "triage",
    "inspect",
    "repair",
    "automation",
    "vendor_uncertain",
    "unknown",
)

VALID_ATTRIBUTION_CONFIDENCES = ("high", "medium", "low")

VALID_FIX_ROUTES = (
    "rule_code",
    "triage_skill",
    "inspect_skill",
    "repair_skill",
    "automation_skill",
    "attention",
    "observe",
)

VALID_FEEDBACK_LABELS = ("positive", "negative", "unknown")
VALID_LABEL_SOURCES = ("auto", "case_diagnosis", "human")

DEFAULT_UNRECONCILED_LIMIT = 100
MIN_UNRECONCILED_LIMIT = 1
MAX_UNRECONCILED_LIMIT = 500


def _sanitize(obj):
    """Convert non-JSON-serializable types for MCP output."""
    from datetime import datetime, date
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


@contextmanager
def _conn():
    conn = psycopg2.connect(EVIDENCE_DB_URL)
    try:
        yield conn
    finally:
        conn.close()


def ensure_table():
    """Create case_memory table if it doesn't exist."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS case_memory (
                id                  SERIAL PRIMARY KEY,
                hostname            TEXT NOT NULL,
                onboard_id          INT,
                rma_ticket_id       TEXT,
                our_classification  TEXT NOT NULL,
                our_reason          TEXT,
                our_evidence        JSONB DEFAULT '{}',
                our_investigation   JSONB DEFAULT '{}',
                vendor_verdict      TEXT NOT NULL,
                vendor_confidence   TEXT DEFAULT 'MEDIUM',
                vendor_answer_quality TEXT DEFAULT 'MODERATE',
                vendor_repair_raw   TEXT,
                vendor_repair_type  TEXT,
                vendor_component    TEXT,
                rma_completed_at    TIMESTAMPTZ,
                collected_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                collected_by        TEXT NOT NULL,
                claude_session_id   JSONB DEFAULT '[]'
            );
        """)
        # Add vendor_answer_quality column if missing (migration for existing DB)
        cur.execute("""
            ALTER TABLE case_memory ADD COLUMN IF NOT EXISTS vendor_answer_quality TEXT DEFAULT 'MODERATE';
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_case_memory_classif_verdict
            ON case_memory(our_classification, vendor_verdict);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_case_memory_component
            ON case_memory(vendor_component);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_case_memory_hostname
            ON case_memory(hostname, collected_at DESC);
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rma_finding_reconciliations (
                case_id                 INT NOT NULL,
                finding_id              INT NOT NULL,
                rule_id                 TEXT NOT NULL,
                repair_outcome          TEXT NOT NULL CHECK (
                    repair_outcome IN (
                        'REPAIR_CONFIRMED',
                        'NO_FAULT_FOUND',
                        'MISCLASSIFIED',
                        'MAINTENANCE_FIX',
                        'CONFIG_TASK'
                    )
                ),
                attribution             TEXT CHECK (
                    attribution IS NULL OR attribution IN (
                        'detection',
                        'triage',
                        'inspect',
                        'repair',
                        'automation',
                        'vendor_uncertain',
                        'unknown'
                    )
                ),
                attribution_confidence  TEXT CHECK (
                    attribution_confidence IS NULL OR attribution_confidence IN (
                        'high',
                        'medium',
                        'low'
                    )
                ),
                fix_route               TEXT CHECK (
                    fix_route IS NULL OR fix_route IN (
                        'rule_code',
                        'triage_skill',
                        'inspect_skill',
                        'repair_skill',
                        'automation_skill',
                        'attention',
                        'observe'
                    )
                ),
                feedback_label          TEXT DEFAULT 'unknown' CHECK (
                    feedback_label IN ('positive', 'negative', 'unknown')
                ),
                expected_behavior       JSONB DEFAULT '{}'::jsonb,
                label_source            TEXT DEFAULT 'auto' CHECK (
                    label_source IN ('auto', 'case_diagnosis', 'human')
                ),
                reconciled_at           TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY(case_id, finding_id)
            );
        """)
        for col, typ in [
            ("feedback_label", "TEXT DEFAULT 'unknown'"),
            ("expected_behavior", "JSONB DEFAULT '{}'::jsonb"),
            ("label_source", "TEXT DEFAULT 'auto'"),
        ]:
            cur.execute(f"ALTER TABLE rma_finding_reconciliations ADD COLUMN IF NOT EXISTS {col} {typ}")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_rma_reconciliations_rule
            ON rma_finding_reconciliations(rule_id, reconciled_at DESC);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_rma_reconciliations_attribution
            ON rma_finding_reconciliations(attribution, attribution_confidence);
        """)
        conn.commit()
    logger.info("case_memory table ensured")


def _validate_choice(name: str, value: str, valid_values: tuple[str, ...]) -> None:
    if value not in valid_values:
        raise ValueError(f"Invalid {name} '{value}'. Must be one of {valid_values}")


def _default_feedback_label(repair_outcome: str) -> str:
    if repair_outcome == "NO_FAULT_FOUND":
        return "negative"
    if repair_outcome == "REPAIR_CONFIRMED":
        return "positive"
    return "unknown"


def _default_expected_behavior(feedback_label: str) -> dict:
    if feedback_label == "negative":
        return {"should_fire": False}
    if feedback_label == "positive":
        return {"should_fire": True}
    return {}


def clamp_unreconciled_limit(limit) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_UNRECONCILED_LIMIT
    return max(MIN_UNRECONCILED_LIMIT, min(value, MAX_UNRECONCILED_LIMIT))


def insert_rma_finding_reconciliation(
    case_id: int,
    finding_id: int,
    rule_id: str,
    repair_outcome: str,
    feedback_label: str = "",
    expected_behavior: Optional[dict] = None,
    label_source: str = "auto",
) -> bool:
    """Insert or refresh the matched RMA outcome for a finding."""
    _validate_choice("repair_outcome", repair_outcome, VALID_REPAIR_OUTCOMES)
    feedback_label = feedback_label or _default_feedback_label(repair_outcome)
    expected_behavior = expected_behavior if expected_behavior is not None else _default_expected_behavior(feedback_label)
    _validate_choice("feedback_label", feedback_label, VALID_FEEDBACK_LABELS)
    _validate_choice("label_source", label_source, VALID_LABEL_SOURCES)

    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO rma_finding_reconciliations
                (case_id, finding_id, rule_id, repair_outcome,
                 feedback_label, expected_behavior, label_source)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (case_id, finding_id) DO UPDATE SET
                rule_id = EXCLUDED.rule_id,
                repair_outcome = EXCLUDED.repair_outcome,
                feedback_label = EXCLUDED.feedback_label,
                expected_behavior = EXCLUDED.expected_behavior,
                label_source = EXCLUDED.label_source
        """, (
            case_id,
            finding_id,
            rule_id,
            repair_outcome,
            feedback_label,
            json.dumps(expected_behavior),
            label_source,
        ))
        conn.commit()
        return True


def update_rma_finding_attribution(
    case_id: int,
    finding_id: int,
    attribution: str,
    attribution_confidence: str,
    fix_route: str,
    feedback_label: str = "",
    expected_behavior: Optional[dict] = None,
    label_source: str = "case_diagnosis",
) -> bool:
    """Update diagnosis attribution for an existing reconciled finding."""
    _validate_choice("attribution", attribution, VALID_RMA_ATTRIBUTIONS)
    _validate_choice(
        "attribution_confidence",
        attribution_confidence,
        VALID_ATTRIBUTION_CONFIDENCES,
    )
    _validate_choice("fix_route", fix_route, VALID_FIX_ROUTES)
    if feedback_label:
        _validate_choice("feedback_label", feedback_label, VALID_FEEDBACK_LABELS)
    _validate_choice("label_source", label_source, VALID_LABEL_SOURCES)

    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT repair_outcome, feedback_label, expected_behavior
            FROM rma_finding_reconciliations
            WHERE case_id = %s
              AND finding_id = %s
        """, (case_id, finding_id))
        current = cur.fetchone()
        if not current:
            return False
        repair_outcome, current_label, current_expected = current
        if feedback_label:
            next_label = feedback_label
        elif attribution != "detection":
            next_label = "unknown"
        else:
            next_label = current_label or _default_feedback_label(repair_outcome)
        next_expected = (
            expected_behavior
            if expected_behavior is not None
            else (current_expected or _default_expected_behavior(next_label))
        )
        cur.execute("""
            UPDATE rma_finding_reconciliations
            SET attribution = %s,
                attribution_confidence = %s,
                fix_route = %s,
                feedback_label = %s,
                expected_behavior = %s::jsonb,
                label_source = %s
            WHERE case_id = %s
              AND finding_id = %s
        """, (
            attribution,
            attribution_confidence,
            fix_route,
            next_label,
            json.dumps(next_expected),
            label_source,
            case_id,
            finding_id,
        ))
        conn.commit()
        return cur.rowcount > 0


def get_rma_finding_reconciliation(case_id: int, finding_id: int) -> Optional[dict]:
    """Return one reconciled finding row, if present."""
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT *
            FROM rma_finding_reconciliations
            WHERE case_id = %s
              AND finding_id = %s
        """, (case_id, finding_id))
        row = cur.fetchone()
        if not row:
            return None
        row = dict(row)
        if row.get("reconciled_at"):
            row["reconciled_at"] = row["reconciled_at"].isoformat()
        return _sanitize(row)


def list_rule_feedback_examples(rule_id: str, feedback_label: str = "") -> list[dict]:
    """List detection-attributed positive/negative examples for replay generation."""
    if feedback_label:
        _validate_choice("feedback_label", feedback_label, VALID_FEEDBACK_LABELS)

    conditions = [
        "r.rule_id = %s",
        "r.attribution = 'detection'",
        "r.feedback_label IN ('positive', 'negative')",
    ]
    params = [rule_id]
    if feedback_label:
        conditions.append("r.feedback_label = %s")
        params.append(feedback_label)
    where = " AND ".join(conditions)

    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"""
            SELECT
                r.case_id,
                r.finding_id,
                r.rule_id,
                r.repair_outcome,
                r.attribution,
                r.attribution_confidence,
                r.fix_route,
                r.feedback_label,
                r.expected_behavior,
                r.label_source,
                f.target_id,
                f.target_type,
                f.evidence,
                f.collector_snapshot_id,
                f.raw_evidence_hash,
                pr.binds_to AS collector_name
            FROM rma_finding_reconciliations r
            LEFT JOIN patrol_findings f ON f.finding_id = r.finding_id
            LEFT JOIN patrol_rules pr ON pr.rule_id = r.rule_id
            WHERE {where}
            ORDER BY r.reconciled_at ASC, r.case_id ASC, r.finding_id ASC
        """, params)
        return [_sanitize(dict(row)) for row in cur.fetchall()]


def list_unreconciled_rma_outcomes(limit: int = 100) -> list[dict]:
    """List completed RMA outcomes that have not been matched to findings yet."""
    limit = clamp_unreconciled_limit(limit)
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT
                cm.id as case_id,
                cm.hostname,
                cm.vendor_verdict,
                cm.rma_ticket_id,
                cm.rma_completed_at,
                cm.our_classification,
                cm.vendor_component,
                cm.vendor_answer_quality,
                cm.claude_session_id
            FROM case_memory cm
            WHERE cm.vendor_verdict IN (
                'REPAIR_CONFIRMED',
                'NO_FAULT_FOUND',
                'MISCLASSIFIED',
                'MAINTENANCE_FIX',
                'CONFIG_TASK'
            )
              AND cm.rma_completed_at IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM rma_finding_reconciliations r
                  WHERE r.case_id = cm.id
              )
            ORDER BY cm.rma_completed_at ASC
            LIMIT %s
        """, (limit,))
        return [_sanitize(dict(row)) for row in cur.fetchall()]


def insert_case(
    hostname: str,
    our_classification: str,
    our_reason: str,
    vendor_verdict: str,
    vendor_repair_raw: str = None,
    vendor_repair_type: str = None,
    vendor_component: str = None,
    vendor_confidence: str = "MEDIUM",
    vendor_answer_quality: str = "MODERATE",
    our_evidence: dict = None,
    our_investigation: dict = None,
    rma_ticket_id: str = None,
    onboard_id: int = None,
    rma_completed_at: str = None,
    claude_session_id: str = "[]",
) -> int:
    """INSERT one case into case_memory. Returns the row id.

    collected_by is auto-set from AGENT_NAME env var — cannot be overridden.
    """
    collected_by = AGENT_NAME
    # Convert empty strings to None for nullable/timestamp columns
    rma_ticket_id = rma_ticket_id or None
    rma_completed_at = rma_completed_at or None
    onboard_id = onboard_id or None
    vendor_repair_raw = vendor_repair_raw or None
    vendor_repair_type = vendor_repair_type or None
    vendor_component = vendor_component or None
    # claude_session_id: parse JSON array of sessions, or wrap a bare string
    if claude_session_id:
        try:
            sessions_val = json.loads(claude_session_id) if isinstance(claude_session_id, str) else claude_session_id
        except (json.JSONDecodeError, TypeError):
            # Bare session ID string — wrap as single-element array
            sessions_val = [{"claude_session_id": claude_session_id}]
    else:
        sessions_val = []
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO case_memory
                (hostname, onboard_id, rma_ticket_id, our_classification,
                 our_reason, our_evidence, our_investigation, vendor_verdict,
                 vendor_confidence, vendor_answer_quality, vendor_repair_raw,
                 vendor_repair_type, vendor_component, rma_completed_at,
                 collected_by, claude_session_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (hostname, rma_ticket_id) DO UPDATE SET
                our_classification = EXCLUDED.our_classification,
                our_reason = EXCLUDED.our_reason,
                our_evidence = EXCLUDED.our_evidence,
                our_investigation = EXCLUDED.our_investigation,
                vendor_verdict = EXCLUDED.vendor_verdict,
                vendor_confidence = EXCLUDED.vendor_confidence,
                vendor_answer_quality = EXCLUDED.vendor_answer_quality,
                vendor_repair_raw = EXCLUDED.vendor_repair_raw,
                vendor_repair_type = EXCLUDED.vendor_repair_type,
                vendor_component = EXCLUDED.vendor_component,
                rma_completed_at = EXCLUDED.rma_completed_at,
                collected_by = EXCLUDED.collected_by,
                claude_session_id = EXCLUDED.claude_session_id
            RETURNING id
        """, (
            hostname, onboard_id, rma_ticket_id, our_classification,
            our_reason, json.dumps(our_evidence or {}),
            json.dumps(our_investigation or {}),
            vendor_verdict, vendor_confidence, vendor_answer_quality,
            vendor_repair_raw, vendor_repair_type, vendor_component,
            rma_completed_at, collected_by, json.dumps(sessions_val),
        ))
        conn.commit()
        row_id = cur.fetchone()[0]
        logger.info(f"Inserted case id={row_id} hostname={hostname}")
        return row_id


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
    our_evidence: dict = None,
    our_investigation: dict = None,
) -> bool:
    """UPDATE any combination of fields on an existing case_memory row.
    Only non-None fields are updated. Returns True if a row was updated.
    """
    fields = []
    params = []
    if vendor_verdict is not None:
        fields.append("vendor_verdict = %s"); params.append(vendor_verdict)
    if vendor_confidence is not None:
        fields.append("vendor_confidence = %s"); params.append(vendor_confidence)
    if vendor_answer_quality is not None:
        fields.append("vendor_answer_quality = %s"); params.append(vendor_answer_quality)
    if vendor_repair_type is not None:
        fields.append("vendor_repair_type = %s"); params.append(vendor_repair_type)
    if vendor_component is not None:
        fields.append("vendor_component = %s"); params.append(vendor_component)
    if vendor_repair_raw is not None:
        fields.append("vendor_repair_raw = %s"); params.append(vendor_repair_raw)
    if our_classification is not None:
        fields.append("our_classification = %s"); params.append(our_classification)
    if our_reason is not None:
        fields.append("our_reason = %s"); params.append(our_reason)
    if our_evidence is not None:
        fields.append("our_evidence = %s"); params.append(json.dumps(our_evidence))
    if our_investigation is not None:
        fields.append("our_investigation = %s"); params.append(json.dumps(our_investigation))
    if not fields:
        return False
    params.append(case_id)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            UPDATE case_memory
            SET {', '.join(fields)}
            WHERE id = %s
        """, params)
        conn.commit()
        return cur.rowcount > 0


def record_finding_verdict_from_case(
    case_id: int,
    hostname: str,
    vendor_verdict: str,
    vendor_answer_quality: str = "",
    rma_completed_at: str = None,
) -> dict:
    """After inserting a case, find unjudged patrol_findings for the same
    hostname and record verdict + reconciliation. Closes the feedback loop
    from RMA outcomes back to detection rules.

    Returns summary: {"matched": N, "confirmed": N, "rejected": N, "skipped": N}
    """
    # Map vendor_verdict + vendor_answer_quality → finding verdict
    CONFIRMED_OUTCOMES = {"REPAIR_CONFIRMED", "MAINTENANCE_FIX", "CONFIG_TASK"}
    verdict_map = {"confirmed": 0, "rejected": 0, "skipped": 0}
    matched = 0

    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Find unjudged findings for this hostname before the RMA date
        time_clause = "AND f.detected_at < %s" if rma_completed_at else ""
        params = [hostname]
        if rma_completed_at:
            params.append(rma_completed_at)
        cur.execute(f"""
            SELECT f.finding_id, f.rule_id, f.target_id
            FROM patrol_findings f
            WHERE f.target_id = %s
              AND f.verdict IS NULL
              AND f.resolved = FALSE
              {time_clause}
        """, params)
        findings = cur.fetchall()

        for finding in findings:
            finding_id = finding["finding_id"]
            rule_id = finding["rule_id"]
            matched += 1

            # Determine verdict
            if vendor_verdict in CONFIRMED_OUTCOMES:
                finding_verdict = "confirmed"
                reason = f"RMA {vendor_verdict}"
            elif vendor_verdict == "MISCLASSIFIED":
                finding_verdict = "rejected"
                reason = "RMA MISCLASSIFIED"
            elif vendor_verdict == "NO_FAULT_FOUND":
                if vendor_answer_quality in ("DETAILED", "MODERATE"):
                    finding_verdict = "rejected"
                    reason = f"RMA NO_FAULT_FOUND (vendor quality: {vendor_answer_quality})"
                else:
                    # Unreliable NFF — can't trust it, skip
                    verdict_map["skipped"] += 1
                    continue
            else:
                verdict_map["skipped"] += 1
                continue

            # Update patrol_findings
            cur2 = conn.cursor()
            cur2.execute("""
                UPDATE patrol_findings
                SET verdict = %s, resolved = TRUE, verdict_reason = %s,
                    repair_outcome = %s, repair_outcome_at = now()
                WHERE finding_id = %s AND verdict IS NULL
            """, (finding_verdict, reason, vendor_verdict, finding_id))

            # Create reconciliation link
            repair_outcome = vendor_verdict
            try:
                insert_rma_finding_reconciliation(
                    case_id=case_id,
                    finding_id=finding_id,
                    rule_id=rule_id or "",
                    repair_outcome=repair_outcome,
                    label_source="auto_collect",
                )
            except Exception:
                pass  # reconciliation is best-effort

            verdict_map[finding_verdict] += 1

        conn.commit()

    return {"matched": matched, **verdict_map}


def update_investigation(case_id: int, our_investigation: dict) -> bool:
    """UPDATE our_investigation field for an existing case (P2)."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE case_memory
            SET our_investigation = %s
            WHERE id = %s
        """, (json.dumps(our_investigation), case_id))
        conn.commit()
        return cur.rowcount > 0


def query_similar_cases(
    classification: str = "",
    alert_names: list = None,
    limit: int = 10,
) -> list[dict]:
    """Find past cases with similar symptoms.

    Coarse filter: classification matches our_classification (exact) OR
    fault_type (part after ' / '). If classification does not contain ' / ',
    it is treated as a fault_type and matches all cases regardless of
    status path prefix (e.g. "NodeCrash" matches both
    "cordoned-triaged_hardware / NodeCrash" and
    "triaged_unknown-triaged_hardware / NodeCrash").
    Fine ranking: alert_types overlap score.
    """
    alert_names = alert_names or []
    # Build WHERE clause: exact match on our_classification, OR fault_type match
    if classification and ' / ' not in classification:
        # e.g. "NodeCrash" → match any classification ending with " / NodeCrash"
        where_clause = "our_classification LIKE '%%/ ' || %(classification)s OR our_classification = %(classification)s"
    elif classification:
        # Full classification like "cordoned-triaged_hardware / NodeCrash"
        where_clause = "our_classification = %(classification)s"
    else:
        where_clause = "TRUE"

    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if alert_names:
            cur.execute(f"""
                SELECT *, (
                    SELECT count(*)
                    FROM jsonb_array_elements_text(our_evidence->'alert_types') a
                    WHERE a = ANY(%(alert_names)s)
                ) as alert_overlap
                FROM case_memory
                WHERE ({where_clause})
                ORDER BY alert_overlap DESC, collected_at DESC
                LIMIT %(limit)s
            """, {
                "classification": classification,
                "alert_names": alert_names,
                "limit": limit,
            })
        else:
            cur.execute(f"""
                SELECT *, 0 as alert_overlap
                FROM case_memory
                WHERE ({where_clause})
                ORDER BY collected_at DESC
                LIMIT %(limit)s
            """, {
                "classification": classification,
                "limit": limit,
            })

        rows = cur.fetchall()
        result = []
        for row in rows:
            row = dict(row)
            for dt_field in ("collected_at", "rma_completed_at"):
                if row.get(dt_field):
                    row[dt_field] = row[dt_field].isoformat()
            for json_field in ("our_evidence", "our_investigation"):
                if isinstance(row.get(json_field), str):
                    try:
                        row[json_field] = json.loads(row[json_field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            result.append(row)
        return _sanitize(result)


def get_cases_by_hostname(hostname: str) -> list[dict]:
    """Get all cases for a hostname, newest first."""
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT * FROM case_memory
            WHERE hostname = %s
            ORDER BY collected_at DESC
        """, (hostname,))
        rows = cur.fetchall()
        result = []
        for row in rows:
            row = dict(row)
            for dt_field in ("collected_at", "rma_completed_at"):
                if row.get(dt_field):
                    row[dt_field] = row[dt_field].isoformat()
            for json_field in ("our_evidence", "our_investigation"):
                if isinstance(row.get(json_field), str):
                    try:
                        row[json_field] = json.loads(row[json_field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            result.append(row)
        return _sanitize(result)


def get_rule_stats() -> list[dict]:
    """Compute accuracy stats per fault type — live query, no table.

    Groups by fault type (part after ' / ' in our_classification), not the full
    classification string. The status transition prefix (cordoned→, triaged_unknown→)
    is irrelevant for accuracy analysis.

    Accuracy = (REPAIR_CONFIRMED + MAINTENANCE_FIX + CONFIG_TASK) / total
    These all mean our classification identified a real issue.
    Wrong = MISCLASSIFIED + NO_FAULT_FOUND.
    NFF split by vendor_answer_quality: nff_reliable (DETAILED/MODERATE) vs
    nff_unreliable (MINIMAL/NONE) — unreliable NFF should not drive skill changes.
    RecallForUpgrade excluded (vendor-initiated bulk recall, not our diagnostic).
    """
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT
                CASE
                    WHEN our_classification LIKE '%%/ %%' THEN SPLIT_PART(our_classification, ' / ', 2)
                    ELSE our_classification
                END as fault_type,
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE vendor_verdict IN ('REPAIR_CONFIRMED','CONFIRMED_HARDWARE','MAINTENANCE_FIX','CONFIG_TASK')) as correct,
                COUNT(*) FILTER (WHERE vendor_verdict IN ('MISCLASSIFIED','NO_FAULT_FOUND')) as wrong,
                ROUND(
                    COUNT(*) FILTER (WHERE vendor_verdict IN ('REPAIR_CONFIRMED','CONFIRMED_HARDWARE','MAINTENANCE_FIX','CONFIG_TASK'))
                    ::numeric / NULLIF(COUNT(*), 0) * 100, 1
                ) as accuracy_pct,
                COUNT(*) FILTER (WHERE vendor_verdict = 'MISCLASSIFIED') as misclassified,
                COUNT(*) FILTER (WHERE vendor_verdict = 'NO_FAULT_FOUND' AND vendor_answer_quality IN ('DETAILED','MODERATE')) as nff_reliable,
                COUNT(*) FILTER (WHERE vendor_verdict = 'NO_FAULT_FOUND' AND vendor_answer_quality IN ('MINIMAL','NONE')) as nff_unreliable,
                COUNT(*) FILTER (WHERE vendor_verdict = 'MAINTENANCE_FIX') as maintenance_fix,
                COUNT(*) FILTER (WHERE vendor_verdict = 'CONFIG_TASK') as config_task,
                COUNT(*) FILTER (WHERE collected_at > NOW() - INTERVAL '90 days') as total_90d,
                ROUND(
                    COUNT(*) FILTER (WHERE vendor_verdict IN ('REPAIR_CONFIRMED','CONFIRMED_HARDWARE','MAINTENANCE_FIX','CONFIG_TASK')
                    AND collected_at > NOW() - INTERVAL '90 days')
                    ::numeric / NULLIF(COUNT(*) FILTER (WHERE collected_at > NOW() - INTERVAL '90 days'), 0) * 100, 1
                ) as accuracy_90d_pct
            FROM case_memory
            WHERE our_classification NOT LIKE '%%RecallForUpgrade%%'
              AND our_classification != 'test'
            GROUP BY fault_type
            ORDER BY accuracy_pct ASC
        """)
        rows = cur.fetchall()
        return [_sanitize(dict(r)) for r in rows]


def get_misclass_paths() -> list[dict]:
    """Get misclassification paths: fault_type → vendor_component with counts.
    
    Only returns cases where vendor_verdict = 'MISCLASSIFIED'.
    Each row shows: we said X fault type, but vendor actually fixed Y component.
    """
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT
                CASE
                    WHEN our_classification LIKE '%%/ %%' THEN SPLIT_PART(our_classification, ' / ', 2)
                    ELSE our_classification
                END as fault_type,
                vendor_component,
                COUNT(*) as cnt,
                string_agg(hostname, ', ' ORDER BY hostname) as hostnames
            FROM case_memory
            WHERE vendor_verdict = 'MISCLASSIFIED'
            GROUP BY fault_type, vendor_component
            ORDER BY cnt DESC
        """)
        return [_sanitize(dict(r)) for r in cur.fetchall()]# ---------------------------------------------------------------------------
# Phase 3 — analysis_problems (cross-session problem tracking)
# ---------------------------------------------------------------------------

VALID_PROBLEM_STATUSES = ('open', 'patch_created', 'monitoring', 'resolved', 'unresolved', 'blocked', 'wont_fix')


def ensure_analysis_problems_table():
    """Create/alter analysis_problems table — append-only, each update inserts a new row."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS analysis_problems (
                id                  SERIAL PRIMARY KEY,
                problem_id          INTEGER NOT NULL,
                title               TEXT,
                fault_type          TEXT,
                prompt              TEXT NOT NULL,
                case_ids            INTEGER[] NOT NULL,
                status              TEXT DEFAULT 'open',
                diagnosis           JSONB DEFAULT '{}',
                patch_summary       TEXT,
                patch_commit        TEXT,
                monitor_expectation TEXT,
                pr_url              TEXT,
                created_at          TIMESTAMPTZ DEFAULT now()
            );
        """)
        # Add new columns to existing table
        for col, typ in [
            ('problem_id', 'INTEGER'),
            ('title', 'TEXT'),
            ('fault_type', 'TEXT'),
            ('diagnosis', 'JSONB DEFAULT \'{}\''),
            ('patch_summary', 'TEXT'),
            ('patch_commit', 'TEXT'),
            ('monitor_expectation', 'TEXT'),
            ('pr_url', 'TEXT'),
        ]:
            cur.execute(f"ALTER TABLE analysis_problems ADD COLUMN IF NOT EXISTS {col} {typ}")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_problems_pid ON analysis_problems(problem_id)")
        # Backfill: existing rows where problem_id is NULL get problem_id = id
        cur.execute("UPDATE analysis_problems SET problem_id = id WHERE problem_id IS NULL")
        cur.execute("ALTER TABLE analysis_problems ALTER COLUMN problem_id SET NOT NULL")
        conn.commit()
    logger.info("analysis_problems table ensured")


def insert_analysis_problem(
    prompt: str,
    case_ids: list,
    title: str = "",
    fault_type: str = "",
) -> int:
    """INSERT one analysis problem. problem_id = id (first row). Returns problem_id."""
    with _conn() as conn:
        cur = conn.cursor()
        # Reserve one id and use it for both id and problem_id. This avoids a
        # temporary NULL problem_id, so fresh schemas can enforce NOT NULL.
        cur.execute("""
            WITH next_problem AS (
                SELECT nextval(pg_get_serial_sequence('analysis_problems', 'id')) AS id
            )
            INSERT INTO analysis_problems
                (id, problem_id, title, fault_type, prompt, case_ids)
            SELECT id, id, %s, %s, %s, %s
            FROM next_problem
            RETURNING problem_id
        """, (title, fault_type, prompt, case_ids))
        problem_id = cur.fetchone()[0]
        conn.commit()
        logger.info(f"Inserted problem problem_id={problem_id} title={title}")
        return problem_id


def get_problems(
    status: str = "",
    fault_type: str = "",
) -> list[dict]:
    """Return problems (latest row per problem_id), optionally filtered.

    If status is empty, returns all problems (for monitoring overview).
    """
    conditions = []
    params = []
    if status:
        conditions.append("status = %s")
        params.append(status)
    if fault_type:
        conditions.append("fault_type = %s")
        params.append(fault_type)

    where = " AND ".join(conditions) if conditions else "TRUE"

    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Get latest row per problem_id, THEN filter by status
        # (must subquery first, otherwise WHERE filters out newer non-matching
        #  rows and DISTINCT ON falls back to older matching rows)
        cur.execute(f"""
            SELECT * FROM (
                SELECT DISTINCT ON (problem_id) *
                FROM analysis_problems
                ORDER BY problem_id, id DESC
            ) latest
            WHERE {where}
        """, params)
        rows = cur.fetchall()
        result = []
        for row in rows:
            row = dict(row)
            # Remove `id` — agents should use `problem_id`, not the row `id`
            row.pop("id", None)
            for dt_field in ("created_at",):
                if row.get(dt_field):
                    row[dt_field] = row[dt_field].isoformat()
            if isinstance(row.get("diagnosis"), str):
                try:
                    row["diagnosis"] = json.loads(row["diagnosis"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(row)
        # Sort by status priority then created_at
        result.sort(key=lambda r: (
            {'open': 1, 'patch_created': 2, 'monitoring': 3, 'unresolved': 4, 'blocked': 5, 'resolved': 6}.get(r.get('status', ''), 7),
            r.get('created_at', ''), ), )
        return _sanitize(result)


def update_problem(
    problem_id: int,
    status: str = "",
    diagnosis: dict = None,
    patch_summary: str = "",
    patch_commit: str = "",
    monitor_expectation: str = "",
    pr_url: str = "",
    add_case_ids: list = None,
) -> bool:
    """Append a new row for this problem with updated fields.
    Copies unchanged fields from the latest row. Returns True if inserted."""
    if status and status not in VALID_PROBLEM_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of {VALID_PROBLEM_STATUSES}")

    # Enforce valid status transitions
    VALID_TRANSITIONS = {
        'open':         {'open', 'patch_created', 'blocked', 'wont_fix'},
        'patch_created': {'patch_created', 'monitoring', 'open', 'blocked', 'wont_fix'},
        'monitoring':   {'monitoring', 'resolved', 'unresolved', 'open', 'wont_fix'},
        'unresolved':   {'unresolved', 'open', 'blocked', 'wont_fix'},
        'blocked':      {'blocked', 'open', 'wont_fix'},
        'resolved':     {'resolved', 'open'},  # can reopen if issue recurs
        'wont_fix':     {'wont_fix', 'open'},  # can reopen if decision changes
    }

    if status:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT status, pr_url, patch_commit FROM analysis_problems
                WHERE problem_id = %s ORDER BY id DESC LIMIT 1
            """, (problem_id,))
            row = cur.fetchone()
            if row:
                current_status = row[0]
                current_pr_url = row[1] if len(row) > 1 else None
                current_patch_commit = row[2] if len(row) > 2 else None
                allowed = VALID_TRANSITIONS.get(current_status, set())
                if status not in allowed:
                    raise ValueError(
                        f"Invalid transition: '{current_status}' → '{status}'. "
                        f"Allowed from '{current_status}': {sorted(allowed)}"
                    )
                # Guard: patch_created → monitoring requires patch deployed
                # - pr_url must exist (MR submitted)
                # - patch_commit must be a deployed commit hash, not a branch name
                #   Deploy skill merges PR then calls update_problem with the
                #   main-branch commit SHA in patch_commit.
                if current_status == 'patch_created' and status == 'monitoring':
                    if not current_pr_url:
                        raise ValueError(
                            f"Cannot move problem #{problem_id} to monitoring: "
                            f"pr_url is empty. Patch must be submitted as a MR/PR "
                            f"and deployed before monitoring can begin. "
                            f"Run deploy-feedback-agent-patches first."
                        )
                    if not _is_commit_hash(current_patch_commit):
                        raise ValueError(
                            f"Cannot move problem #{problem_id} to monitoring: "
                            f"patch_commit is '{current_patch_commit}' "
                            f"(branch name, not a deployed commit hash). "
                            f"Patch must be merged to main and deployed first. "
                            f"Run deploy-feedback-agent-patches to merge and deploy, "
                            f"then update_problem with the main-branch commit SHA."
                        )

    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Get current row — try problem_id first, fall back to id
        cur.execute("""
            SELECT * FROM analysis_problems
            WHERE problem_id = %s ORDER BY id DESC LIMIT 1
        """, (problem_id,))
        current = cur.fetchone()
        if not current:
            cur.execute("""
                SELECT * FROM analysis_problems
                WHERE id = %s ORDER BY id DESC LIMIT 1
            """, (problem_id,))
            current = cur.fetchone()
        if not current:
            return False
        current = dict(current)

        # Merge: new values override current
        new_title = current.get('title', '')
        new_fault_type = current.get('fault_type', '')
        new_prompt = current.get('prompt', '')
        new_case_ids = current.get('case_ids', [])
        new_status = status or current.get('status', 'open')
        new_diagnosis = json.dumps(diagnosis) if diagnosis is not None else json.dumps(current.get('diagnosis', {}))
        new_patch_summary = patch_summary or current.get('patch_summary', '')
        new_patch_commit = patch_commit or current.get('patch_commit', '')
        new_monitor_expectation = monitor_expectation or current.get('monitor_expectation', '')
        new_pr_url = pr_url or current.get('pr_url', '')

        if add_case_ids:
            existing = set(new_case_ids) if new_case_ids else set()
            new_case_ids = list(existing | set(add_case_ids))

        cur.execute("""
            INSERT INTO analysis_problems
                (problem_id, title, fault_type, prompt, case_ids, status,
                 diagnosis, patch_summary, patch_commit, monitor_expectation, pr_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (problem_id, new_title, new_fault_type, new_prompt,
              new_case_ids, new_status, new_diagnosis,
              new_patch_summary, new_patch_commit, new_monitor_expectation, new_pr_url))
        conn.commit()
        return cur.rowcount > 0


def get_repeat_offenders(min_cases: int = 2) -> list:
    """Find hostnames with multiple cases across any fault types.
    Returns hostname, case count, list of fault_types, list of verdicts, list of case_ids."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT hostname,
                   COUNT(*) as case_count,
                   array_agg(DISTINCT our_classification ORDER BY our_classification) as classifications,
                   array_agg(DISTINCT vendor_verdict ORDER BY vendor_verdict) as verdicts,
                   array_agg(id ORDER BY id) as case_ids
            FROM case_memory
            GROUP BY hostname
            HAVING COUNT(*) >= %s
            ORDER BY COUNT(*) DESC
        """, (min_cases,))
        rows = cur.fetchall()
        results = []
        for r in rows:
            results.append({
                "hostname": r[0],
                "case_count": r[1],
                "classifications": r[2],
                "verdicts": r[3],
                "case_ids": r[4],
            })
        return results


def get_problem_history(problem_id: int) -> list[dict]:
    """Return all rows for a problem, ordered by id (timeline)."""
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, problem_id, status, patch_summary, patch_commit,
                   monitor_expectation, created_at
            FROM analysis_problems
            WHERE problem_id = %s
            ORDER BY id ASC
        """, (problem_id,))
        rows = cur.fetchall()
        result = []
        for row in rows:
            row = dict(row)
            if row.get("created_at"):
                row["created_at"] = row["created_at"].isoformat()
            result.append(row)
        return _sanitize(result)


def ensure_rejected_proposals_table():
    """Create rejected_proposals table if it doesn't exist."""
    with _conn() as conn:
        cur = conn.cursor()
        # Check if table already exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'rejected_proposals'
            );
        """)
        if cur.fetchone()[0]:
            conn.commit()
            logger.info("rejected_proposals table already exists")
            return

        # Create without FK first (FK may fail if analysis_problems schema doesn't match)
        try:
            cur.execute("""
                CREATE TABLE rejected_proposals (
                    id              SERIAL PRIMARY KEY,
                    problem_id      INT REFERENCES analysis_problems(problem_id),
                    skill_file      TEXT NOT NULL,
                    edit_type       TEXT NOT NULL,
                    edit_summary    TEXT NOT NULL,
                    edit_detail     TEXT,
                    rationale       TEXT,
                    outcome         TEXT NOT NULL,
                    before_metric   JSONB DEFAULT '{}',
                    after_metric    JSONB DEFAULT '{}',
                    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                    deployed_at     TIMESTAMPTZ,
                    note            TEXT
                );
            """)
        except Exception:
            conn.rollback()
            # Fallback: create without FK constraint
            cur.execute("""
                CREATE TABLE rejected_proposals (
                    id              SERIAL PRIMARY KEY,
                    problem_id      INT,
                    skill_file      TEXT NOT NULL,
                    edit_type       TEXT NOT NULL,
                    edit_summary    TEXT NOT NULL,
                    edit_detail     TEXT,
                    rationale       TEXT,
                    outcome         TEXT NOT NULL,
                    before_metric   JSONB DEFAULT '{}',
                    after_metric    JSONB DEFAULT '{}',
                    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                    deployed_at     TIMESTAMPTZ,
                    note            TEXT
                );
            """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_rejected_proposals_skill
            ON rejected_proposals(skill_file, observed_at DESC);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_rejected_proposals_problem
            ON rejected_proposals(problem_id);
        """)
        conn.commit()
    logger.info("rejected_proposals table ensured")


def insert_rejected_proposal(
    skill_file: str,
    edit_type: str,
    edit_summary: str,
    outcome: str,
    edit_detail: str = "",
    rationale: str = "",
    before_metric: Optional[dict] = None,
    after_metric: Optional[dict] = None,
    problem_id: Optional[int] = None,
    deployed_at: Optional[str] = None,
    note: str = "",
) -> int:
    """Record a deployed skill change that didn't improve outcomes. Returns the inserted row ID.

    outcome: no_improvement (accuracy stayed same), regression (accuracy dropped),
             reverted (explicitly rolled back), superseded (replaced by better change).
    """
    _validate_choice("outcome", outcome, ("no_improvement", "regression", "reverted", "superseded"))
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO rejected_proposals
                (problem_id, skill_file, edit_type, edit_summary, edit_detail,
                 rationale, outcome, before_metric, after_metric, deployed_at, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (problem_id, skill_file, edit_type, edit_summary, edit_detail,
              rationale, outcome,
              json.dumps(before_metric or {}), json.dumps(after_metric or {}),
              deployed_at, note))
        row_id = cur.fetchone()[0]
        conn.commit()
        logger.info("Recorded rejected proposal #%d: %s/%s — %s (%s)",
                     row_id, skill_file, edit_type, edit_summary[:80], outcome)
        return row_id


def get_rejected_proposals(skill_file: str = "", limit: int = 20) -> list[dict]:
    """Query rejected proposals, optionally filtered by skill file.
    Returns most recent first. Used by feedback agent to avoid re-proposing bad edits."""
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if skill_file:
            cur.execute("""
                SELECT id, problem_id, skill_file, edit_type, edit_summary,
                       edit_detail, rationale, outcome,
                       before_metric, after_metric,
                       observed_at, deployed_at, note
                FROM rejected_proposals
                WHERE skill_file = %s
                ORDER BY observed_at DESC
                LIMIT %s
            """, (skill_file, limit))
        else:
            cur.execute("""
                SELECT id, problem_id, skill_file, edit_type, edit_summary,
                       edit_detail, rationale, outcome,
                       before_metric, after_metric,
                       observed_at, deployed_at, note
                FROM rejected_proposals
                ORDER BY observed_at DESC
                LIMIT %s
            """, (limit,))
        rows = cur.fetchall()
        result = []
        for row in rows:
            row = dict(row)
            if row.get("observed_at"):
                row["observed_at"] = row["observed_at"].isoformat()
            if row.get("deployed_at"):
                row["deployed_at"] = row["deployed_at"].isoformat()
            result.append(row)
        return _sanitize(result)


# ── Agent Memory ──────────────────────────────────────────────────────────────

def ensure_agent_memory_table():
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_memory (
                id BIGSERIAL PRIMARY KEY,
                target TEXT NOT NULL,
                domain TEXT NOT NULL,
                action TEXT NOT NULL,
                outcome TEXT NOT NULL,
                reason TEXT,
                insight TEXT,
                detail TEXT,
                before_metric JSONB DEFAULT '{}',
                after_metric JSONB DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_agent_memory_target ON agent_memory(target);
            CREATE INDEX IF NOT EXISTS idx_agent_memory_domain ON agent_memory(domain);
            CREATE INDEX IF NOT EXISTS idx_agent_memory_insight ON agent_memory(insight) WHERE insight IS NOT NULL;
        """)
        conn.commit()


def insert_agent_memory(
    target: str,
    domain: str,
    action: str,
    outcome: str,
    reason: str = "",
    insight: str = "",
    detail: str = "",
    before_metric: dict = None,
    after_metric: dict = None,
) -> int:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO agent_memory (target, domain, action, outcome, reason, insight, detail, before_metric, after_metric)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (target, domain, action, outcome, reason or None, insight or None,
              detail or None,
              json.dumps(before_metric or {}), json.dumps(after_metric or {})))
        row_id = cur.fetchone()[0]
        conn.commit()
        return row_id


def query_agent_memory(
    target: str = "",
    domain: str = "",
    with_insight_only: bool = False,
    limit: int = 20,
) -> list[dict]:
    """Query agent memory. Filter by target and/or domain."""
    conditions = []
    params = []
    if target:
        conditions.append("target = %s")
        params.append(target)
    if domain:
        conditions.append("domain = %s")
        params.append(domain)
    if with_insight_only:
        conditions.append("insight IS NOT NULL")
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"""
            SELECT id, target, domain, action, outcome, reason, insight, detail,
                   before_metric, after_metric, created_at
            FROM agent_memory
            {where}
            ORDER BY created_at DESC
            LIMIT %s
        """, params)
        rows = cur.fetchall()
        result = []
        for row in rows:
            row = dict(row)
            if row.get("created_at"):
                row["created_at"] = row["created_at"].isoformat()
            result.append(row)
        return _sanitize(result)
