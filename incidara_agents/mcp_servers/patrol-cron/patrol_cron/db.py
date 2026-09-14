"""DB access for patrol_cron — collectors, rules, state, findings.

Role-based table names via PATROL_ROLE env var:
  - infrastructure (default): collectors, patrol_rules, rule_state, patrol_findings
  - job_detection: job_detection_collectors, job_detection_rules, etc.

Add new role by prefixing tables: {role}_{table}.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

EVIDENCE_DB_URL = os.environ.get(
    "EVIDENCE_DB_URL",
    "",
)

ROLE = os.environ.get("PATROL_ROLE", "infrastructure")

def _t(name: str) -> str:
    """Get role-specific table name. infrastructure uses bare names."""
    if ROLE == "infrastructure":
        return name
    return f"{ROLE}_{name}"


def _conn():
    return psycopg2.connect(EVIDENCE_DB_URL)


# ── Collectors ────────────────────────────────────────────────────────────

def get_enabled_collectors() -> list[dict]:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT * FROM {_t('collectors')} WHERE enabled ORDER BY name")
        return [dict(r) for r in cur.fetchall()]


def get_collector_by_name(name: str) -> dict | None:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT * FROM {_t('collectors')} WHERE name = %s", (name,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_collector_last_run(name: str):
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE {_t('collectors')} SET last_run = NOW() WHERE name = %s", (name,))
        conn.commit()


def get_first_target_for_collector(collector_name: str) -> str | None:
    """Get the first target ID for a collector from switch_inventory."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT hostname FROM switch_inventory
               WHERE type = (SELECT target_type FROM {_t('collectors')} WHERE name = %s)
               LIMIT 1""",
            (collector_name,),
        )
        row = cur.fetchone()
        return row[0] if row else None


# ── Rules ─────────────────────────────────────────────────────────────────

def get_rules_for_collector(collector_name: str) -> list[dict]:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"SELECT * FROM {_t('patrol_rules')} WHERE binds_to = %s AND enabled ORDER BY rule_id",
            (collector_name,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_rule(rule_id: str) -> dict | None:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT * FROM {_t('patrol_rules')} WHERE rule_id = %s", (rule_id,))
        row = cur.fetchone()
        return dict(row) if row else None


# ── Rule State ────────────────────────────────────────────────────────────

def load_rule_state(rule_id: str) -> dict:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT state_data FROM {_t('rule_state')} WHERE rule_id = %s", (rule_id,))
        row = cur.fetchone()
        if row and row["state_data"]:
            return row["state_data"]
        return {}


def save_rule_state(rule_id: str, state: dict):
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""INSERT INTO {_t('rule_state')} (rule_id, state_data, updated_at)
               VALUES (%s, %s, NOW())
               ON CONFLICT (rule_id) DO UPDATE
                 SET state_data = EXCLUDED.state_data, updated_at = NOW()""",
            (rule_id, json.dumps(state, default=str)),
        )
        conn.commit()


# ── Collector Snapshots ────────────────────────────────────────────────────

def save_collector_snapshot(snapshot_hash: str, collector_name: str,
                            frozen_input: dict, target_count: int):
    """Save a collector run snapshot. Idempotent — skips if hash already exists."""
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""INSERT INTO {_t('collector_snapshots')}
                       (snapshot_hash, collector_name, frozen_input, target_count)
                   VALUES (%s, %s, %s::jsonb, %s)
                   ON CONFLICT (snapshot_hash) DO NOTHING""",
                (snapshot_hash, collector_name,
                 json.dumps(frozen_input, default=str), target_count),
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to save collector snapshot: {e}")


def get_collector_snapshot(snapshot_hash: str) -> dict | None:
    """Look up a collector snapshot by hash."""
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"SELECT * FROM {_t('collector_snapshots')} WHERE snapshot_hash = %s",
            (snapshot_hash,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


# ── Findings ──────────────────────────────────────────────────────────────

def finding_exists(rule_id: str, target_id: str, action: str) -> bool:
    """Check if an active finding exists for this rule+target+action.

    Blocks new findings while the target is still failing, regardless of
    whether a human has judged the existing finding (same ongoing problem).
    """
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT 1 FROM {_t('patrol_findings')}
               WHERE rule_id = %s AND target_id = %s AND action = %s AND active
               LIMIT 1""",
            (rule_id, target_id, action),
        )
        return cur.fetchone() is not None


def deactivate_recovered_findings(rule_id: str, still_failing_ids: set[str]):
    """Mark findings as inactive for targets that are no longer failing.

    If a rule returned findings for some targets but NOT others, the targets
    NOT in the findings list have recovered. Deactivate their findings.
    If no targets are still failing, deactivate ALL active findings for this rule.
    This does NOT set resolved or verdict — those are only for human judgment.
    Sets deactivated_at so we can compute how long the problem was active.
    """
    with _conn() as conn:
        cur = conn.cursor()
        if still_failing_ids:
            placeholders = ",".join(["%s"] * len(still_failing_ids))
            cur.execute(
                f"""UPDATE {_t('patrol_findings')}
                   SET active = false, deactivated_at = NOW()
                   WHERE rule_id = %s
                     AND active
                     AND target_id NOT IN ({placeholders})""",
                [rule_id] + list(still_failing_ids),
            )
        else:
            # All targets recovered — deactivate everything
            cur.execute(
                f"""UPDATE {_t('patrol_findings')}
                   SET active = false, deactivated_at = NOW()
                   WHERE rule_id = %s AND active""",
                (rule_id,),
            )
        count = cur.rowcount
        conn.commit()
        return count


def get_lifecycle_state(
    rule_id: str,
    target_id: str,
    signal_key: str,
    action: str,
) -> dict | None:
    """Return persisted lifecycle counters for one observation key."""
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """SELECT *
               FROM detection_lifecycle_state
               WHERE rule_id = %s
                 AND target_id = %s
                 AND signal_key = %s
                 AND action = %s""",
            (rule_id, target_id, signal_key, action),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_lifecycle_state(row: dict):
    """Persist one sparse lifecycle state row."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO detection_lifecycle_state
                  (rule_id, target_id, signal_key, action, bad_count, healthy_count,
                   active_finding_id, last_status, first_seen_at, last_seen_at,
                   updated_at, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                       COALESCE(%s, NOW()), COALESCE(%s, NOW()), NOW(), %s)
               ON CONFLICT (rule_id, target_id, signal_key, action) DO UPDATE
                 SET bad_count = EXCLUDED.bad_count,
                     healthy_count = EXCLUDED.healthy_count,
                     active_finding_id = EXCLUDED.active_finding_id,
                     last_status = EXCLUDED.last_status,
                     last_seen_at = EXCLUDED.last_seen_at,
                     updated_at = NOW(),
                     expires_at = EXCLUDED.expires_at""",
            (
                row["rule_id"],
                row["target_id"],
                row["signal_key"],
                row["action"],
                row.get("bad_count", 0),
                row.get("healthy_count", 0),
                row.get("active_finding_id"),
                row.get("last_status"),
                row.get("first_seen_at"),
                row.get("last_seen_at"),
                row.get("expires_at"),
            ),
        )
        conn.commit()


def delete_lifecycle_state(
    rule_id: str,
    target_id: str,
    signal_key: str,
    action: str,
) -> int:
    """Delete one lifecycle state row after it has fully recovered."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """DELETE FROM detection_lifecycle_state
               WHERE rule_id = %s
                 AND target_id = %s
                 AND signal_key = %s
                 AND action = %s""",
            (rule_id, target_id, signal_key, action),
        )
        count = cur.rowcount
        conn.commit()
        return count


def delete_expired_lifecycle_state() -> int:
    """Remove expired inactive lifecycle rows; active rows are always retained."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """DELETE FROM detection_lifecycle_state
               WHERE active_finding_id IS NULL
                 AND expires_at IS NOT NULL
                 AND expires_at < NOW()"""
        )
        count = cur.rowcount
        conn.commit()
        return count


def find_active_finding(
    rule_id: str,
    target_id: str,
    signal_key: str,
    action: str,
) -> dict | None:
    """Return the active node issue finding for this rule+target+action.

    signal_key is accepted for caller compatibility, but findings are not
    split by signal. Signals live in detection_lifecycle_state.
    """
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""SELECT *
               FROM {_t('patrol_findings')}
               WHERE rule_id = %s
                 AND target_id = %s
                 AND action = %s
                 AND active
               ORDER BY detected_at DESC
               LIMIT 1""",
            (rule_id, target_id, action),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def has_active_lifecycle_signals(
    rule_id: str,
    target_id: str,
    action: str,
    finding_id: int,
    exclude_signal_key: str = "",
) -> bool:
    """Return whether another lifecycle signal still keeps a finding active."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT 1
               FROM detection_lifecycle_state
               WHERE rule_id = %s
                 AND target_id = %s
                 AND action = %s
                 AND active_finding_id = %s
                 AND signal_key <> %s
               LIMIT 1""",
            (rule_id, target_id, action, finding_id, exclude_signal_key),
        )
        return cur.fetchone() is not None


def refresh_active_finding(
    finding_id: int,
    evidence: dict | None,
    confidence: float,
) -> int:
    """Refresh evidence and heartbeat fields for an existing active finding."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""UPDATE {_t('patrol_findings')}
               SET evidence = %s,
                   confidence = %s,
                   last_seen_at = NOW(),
                   seen_count = COALESCE(seen_count, 1) + 1
               WHERE finding_id = %s
                 AND active""",
            (json.dumps(evidence or {}, default=str), confidence, finding_id),
        )
        count = cur.rowcount
        conn.commit()
        return count


def deactivate_finding(finding_id: int) -> int:
    """Deactivate one active finding after lifecycle recovery threshold passes."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""UPDATE {_t('patrol_findings')}
               SET active = false,
                   deactivated_at = NOW()
               WHERE finding_id = %s
                 AND active""",
            (finding_id,),
        )
        count = cur.rowcount
        conn.commit()
        return count


def insert_finding(
    rule_id: str, target_id: str, target_type: str, severity: str,
    action: str, action_params: dict | None, evidence: dict | None, confidence: float,
    collector_snapshot_id: str | None = None, raw_evidence_hash: str | None = None,
    rule_version_at: str | None = None,
    rule_code_hash: str | None = None, collector_config_hash: str | None = None,
    signal_key: str | None = None, event_id: str | None = None,
) -> int | None:
    """Insert a finding and return its finding_id.
    
    rule_version_at: timestamp of the rule code that produced this finding.
    rule_code_hash: md5 hash of the rule's analyze_code at creation time → links to patrol_rule_versions.
    collector_config_hash: md5 hash of the collector's sources config at creation time → links to collector_versions.
    """
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""INSERT INTO {_t('patrol_findings')}
               (rule_id, target_id, target_type, severity, action,
                action_params, evidence, confidence, collector_snapshot_id,
                raw_evidence_hash, detected_at, rule_version_at,
                rule_code_hash, collector_config_hash, signal_key, event_id,
                last_seen_at, seen_count)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s,
                       %s, %s, NOW(), 1)
               RETURNING finding_id""",
            (rule_id, target_id, target_type, severity, action,
             json.dumps(action_params or {}, default=str),
             json.dumps(evidence or {}, default=str), confidence,
             collector_snapshot_id, raw_evidence_hash,
             rule_version_at, rule_code_hash, collector_config_hash,
             signal_key, event_id),
        )
        finding_id = cur.fetchone()[0]
        conn.commit()
        return finding_id


def get_rule_accuracy(rule_id: str, window_days: int = 14) -> dict:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""SELECT count(*) AS total,
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
        return {"total": total, "judged": judged, "confirmed": confirmed,
                "rejected": row["rejected"] or 0, "accuracy": accuracy}


def get_rule_bad_feedback_rate(rule_id: str) -> dict:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""SELECT count(*) AS rma_total,
                     count(*) FILTER (WHERE rfr.repair_outcome = 'REPAIR_CONFIRMED') AS repair_confirmed,
                     count(*) FILTER (WHERE rfr.repair_outcome = 'CONFIG_TASK') AS config_task,
                     count(*) FILTER (
                         WHERE rfr.repair_outcome = 'NO_FAULT_FOUND'
                           AND rfr.attribution = 'detection'
                           AND rfr.attribution_confidence IN ('high', 'medium')
                     ) AS detection_nff,
                     count(*) FILTER (
                         WHERE rfr.repair_outcome = 'MISCLASSIFIED'
                           AND rfr.attribution = 'detection'
                           AND rfr.attribution_confidence IN ('high', 'medium')
                     ) AS detection_misclassified
               FROM {_t('patrol_findings')} pf
               LEFT JOIN rule_reconciliation_state rrs
                 ON rrs.rule_id = pf.rule_id
               JOIN rma_finding_reconciliations rfr
                 ON rfr.finding_id = pf.finding_id
               WHERE pf.rule_id = %s
                 AND pf.detected_at > COALESCE(rrs.nff_baseline_at, '-infinity'::timestamptz)""",
            (rule_id,),
        )
        row = cur.fetchone() or {}
        rma_total = row.get("rma_total") or 0
        repair_confirmed = row.get("repair_confirmed") or 0
        config_task = row.get("config_task") or 0
        detection_nff = row.get("detection_nff") or 0
        detection_misclassified = row.get("detection_misclassified") or 0
        bad_count = detection_nff + detection_misclassified
        bad_feedback_rate = bad_count / rma_total if rma_total else None
        return {
            "rule_id": rule_id,
            "rma_total": rma_total,
            "total_count": rma_total,
            "repair_confirmed": repair_confirmed,
            "config_task": config_task,
            "detection_nff": detection_nff,
            "detection_misclassified": detection_misclassified,
            "bad_count": bad_count,
            "bad_feedback_rate": bad_feedback_rate,
        }


def list_dirty_reconciliation_rules(limit: int = 50) -> list[dict]:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    if limit < 1:
        limit = 50
    limit = min(limit, 500)

    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""SELECT *
               FROM rule_reconciliation_state
               WHERE reconciliation_dirty = TRUE
               ORDER BY updated_at ASC
               LIMIT %s""",
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            for key, value in list(row.items()):
                if hasattr(value, "isoformat"):
                    row[key] = str(value)
        return rows
