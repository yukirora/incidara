"""DB query collector — runs a SQL query and returns rows as targets.

Each row becomes a TargetData. The collector config specifies:
  - query: SQL to run (must be SELECT)
  - target_id_column: which column is the target ID (default: first column)
  - target_type: inherited from collector config

Example collector config:
    sources:
      - type: db_query
        name: switch_state
        config:
          query: "SELECT hostname, last_ok, last_severity, last_reason, fail_count, port_error_baseline, updated_at FROM switch_state"
          target_id_column: hostname
"""

from __future__ import annotations

import json
import logging
import os
import time

import psycopg2
import psycopg2.extras

from patrol_cron.collectors.base import BaseCollector
from patrol_cron.models import CollectionResult, TargetData

logger = logging.getLogger(__name__)

EVIDENCE_DB_URL = os.environ.get(
    "EVIDENCE_DB_URL",
    "",
)


class DbQueryCollector(BaseCollector):
    """Runs a SQL query and returns each row as a TargetData."""

    def collect(self, collector_cfg: dict) -> CollectionResult:
        start = time.time()
        name = collector_cfg["name"]
        target_type = collector_cfg.get("target_type", "query_result")
        sources = collector_cfg.get("sources") or []

        src = next((s for s in sources if s.get("type") == "db_query"), None)
        if not src:
            return CollectionResult(collector_name=name, errors=["No db_query source configured"])

        config = src.get("config", {})
        query = config.get("query", "")
        target_id_column = config.get("target_id_column")
        db_url = config.get("db_url", EVIDENCE_DB_URL)

        if not query:
            return CollectionResult(collector_name=name, errors=["No query specified"])

        # Safety: only allow SELECT
        if not query.strip().upper().startswith("SELECT"):
            return CollectionResult(collector_name=name, errors=["Only SELECT queries allowed"])

        targets: list[TargetData] = []
        errors: list[str] = []

        try:
            with psycopg2.connect(db_url) as conn:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(query)
                rows = cur.fetchall()

                for row in rows:
                    row_dict = dict(row)
                    # Serialize non-JSON-safe types
                    for k, v in row_dict.items():
                        if hasattr(v, 'isoformat'):
                            row_dict[k] = v.isoformat()

                    # Determine target ID
                    if target_id_column and target_id_column in row_dict:
                        tid = str(row_dict[target_id_column])
                    else:
                        # Use first column value
                        tid = str(list(row_dict.values())[0]) if row_dict else "unknown"

                    targets.append(TargetData(
                        id=tid,
                        type=target_type,
                        payload=row_dict,
                        meta={},
                    ))

        except Exception as e:
            errors.append(f"Query failed: {e}")
            logger.error(f"[{name}] DB query failed: {e}")

        duration = time.time() - start
        logger.info(f"[{name}] Collected {len(targets)} rows in {duration:.1f}s")
        return CollectionResult(collector_name=name, targets=targets,
                                errors=errors, duration=duration)
