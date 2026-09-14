"""Agent evidence DB — investigation artifacts stored on .23:5434.

Separate from the platform DB (.19). Used by triage and repair agents
to persist raw investigation evidence (probe output, nvidia-smi, dmesg,
job logs, etc.) so it survives across sessions and is shareable between agents.
"""

import os
import json
import logging
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

EVIDENCE_DB_URL = os.getenv(
    "EVIDENCE_DB_URL",
    ""
)

# Which agent is calling — set in container env (AGENT_NAME=repair-draft|repair|ticket-replay|triage)
AGENT_NAME = os.getenv("AGENT_NAME", os.getenv("AGENT_ROLE", "unknown"))


@contextmanager
def _conn():
    """Get a short-lived connection to the evidence DB."""
    conn = psycopg2.connect(EVIDENCE_DB_URL)
    try:
        yield conn
    finally:
        conn.close()


def ensure_table():
    """Create investigation_evidence table if it doesn't exist.
    Called once at MCP server startup.
    """
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS investigation_evidence (
                id           BIGSERIAL PRIMARY KEY,
                node_name    VARCHAR NOT NULL,
                collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                collected_by VARCHAR NOT NULL,
                source       VARCHAR NOT NULL,
                category     VARCHAR,
                summary      TEXT,
                content      TEXT NOT NULL,
                metadata     JSONB DEFAULT '{}',
                finding_id   INTEGER REFERENCES patrol_findings(finding_id) ON DELETE SET NULL
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_evidence_node
            ON investigation_evidence(node_name, collected_at DESC);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_evidence_source
            ON investigation_evidence(node_name, source);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_evidence_finding
            ON investigation_evidence(finding_id) WHERE finding_id IS NOT NULL;
        """)
        conn.commit()
    logger.info("investigation_evidence table ensured")


def save_evidence(
    node_name: str,
    source: str,
    content: str,
    category: str = None,
    summary: str = None,
    metadata: dict = None,
    finding_id: int = None,
) -> int:
    """INSERT one evidence row. Returns the row id.

    Args:
        node_name: Node hostname
        source: Evidence source (probe_ssh, nvidia_smi, dmesg, ib_stat,
                job_log, alert, nvlink, fabricmanager, other)
        content: Raw output from the investigation tool/command
        category: Fault subsystem (gpu, ib, nvlink, pcie, cpu, memory,
                  platform, unknown)
        summary: 1-line interpretation of what was found
        metadata: Optional structured fields
                  (e.g., {"gpu_index": 4, "ecc_count": 42, "baseline": 55.25})
        finding_id: Optional patrol_findings ID to link this evidence to a finding

    collected_by is auto-set from AGENT_NAME env var — cannot be overridden.
    """
    collected_by = AGENT_NAME
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO investigation_evidence
                (node_name, collected_by, source, category, summary, content, metadata, finding_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            node_name,
            collected_by,
            source,
            category,
            summary,
            content,
            json.dumps(metadata or {}),
            finding_id,
        ))
        conn.commit()
        row_id = cur.fetchone()[0]
        logger.info(f"Saved evidence id={row_id} node={node_name} source={source} finding={finding_id}")
        return row_id


def get_node_evidence(
    node_name: str,
    source: str = None,
    category: str = None,
    collected_by: str = None,
    limit: int = 100,
) -> list[dict]:
    """SELECT evidence rows for a node. Newest first.

    Args:
        node_name: Node hostname
        source: Filter by evidence source
        category: Filter by fault subsystem
        collected_by: Filter by collecting agent (e.g., 'repair-draft', 'repair', 'triage')
        limit: Max rows to return (default 100)
    """
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        query = """
            SELECT id, node_name, collected_at, collected_by, source,
                   category, summary, content, metadata
            FROM investigation_evidence
            WHERE node_name = %s
        """
        params = [node_name]

        if source:
            query += " AND source = %s"
            params.append(source)
        if category:
            query += " AND category = %s"
            params.append(category)
        if collected_by:
            query += " AND collected_by = %s"
            params.append(collected_by)

        query += " ORDER BY collected_at DESC LIMIT %s"
        params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()
        # Convert datetime to string for JSON serialization
        result = []
        for row in rows:
            row = dict(row)
            if row.get("collected_at"):
                row["collected_at"] = row["collected_at"].isoformat()
            if isinstance(row.get("metadata"), str):
                try:
                    row["metadata"] = json.loads(row["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(row)
        return result


def search_evidence(
    category: str = None,
    source: str = None,
    summary_like: str = None,
    collected_by: str = None,
    since_days: int = 30,
    limit: int = 100,
) -> list[dict]:
    """Search evidence across ALL nodes. Used for pattern detection.

    Args:
        category: Filter by fault subsystem (gpu, ib, nvlink, pcie, platform, etc.)
        source: Filter by evidence source (probe_ssh, dmesg, alert, etc.)
        summary_like: Case-insensitive substring match on summary
        collected_by: Filter by collecting agent
        since_days: Only evidence from last N days (default 30)
        limit: Max rows to return (default 100)
    """
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        query = """
            SELECT id, node_name, collected_at, collected_by, source,
                   category, summary, metadata
            FROM investigation_evidence
            WHERE collected_at > NOW() - interval '%s days'
        """
        params: list = [since_days]

        if category:
            query += " AND category = %s"
            params.append(category)
        if source:
            query += " AND source = %s"
            params.append(source)
        if summary_like:
            query += " AND summary ILIKE %s"
            params.append(f"%{summary_like}%")
        if collected_by:
            query += " AND collected_by = %s"
            params.append(collected_by)

        query += " ORDER BY collected_at DESC LIMIT %s"
        params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()
        result = []
        for row in rows:
            row = dict(row)
            if row.get("collected_at"):
                row["collected_at"] = row["collected_at"].isoformat()
            if isinstance(row.get("metadata"), str):
                try:
                    row["metadata"] = json.loads(row["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(row)
        return result


def delete_node_evidence(node_name: str, before: str = None) -> int:
    """Delete evidence for a node. Optionally only before a timestamp.

    Args:
        node_name: Node hostname
        before: ISO timestamp — only delete evidence older than this

    Returns:
        Number of rows deleted
    """
    with _conn() as conn:
        cur = conn.cursor()
        query = "DELETE FROM investigation_evidence WHERE node_name = %s"
        params = [node_name]
        if before:
            query += " AND collected_at < %s"
            params.append(before)
        cur.execute(query, params)
        deleted = cur.rowcount
        conn.commit()
        return deleted


def get_finding_evidence(finding_id: int, limit: int = 100) -> list[dict]:
    """Get all investigation evidence linked to a finding.

    Args:
        finding_id: patrol_findings ID
        limit: Max rows to return

    Returns:
        List of evidence dicts (newest first)
    """
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """SELECT id, node_name, collected_at, collected_by, source,
                      category, summary, content, metadata
               FROM investigation_evidence
               WHERE finding_id = %s
               ORDER BY collected_at DESC
               LIMIT %s""",
            (finding_id, limit),
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
        return rows
        logger.info(f"Deleted {deleted} evidence rows for node={node_name}")
        return deleted
