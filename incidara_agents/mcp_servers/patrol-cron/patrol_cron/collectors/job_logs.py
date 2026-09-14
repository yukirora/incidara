"""Job logs collector — fetches failed job logs and pattern-matches.

Uses NFD's JobMetadataClient to find recent failed jobs, then JobLogsClient
to fetch and pattern-match their logs.

group_by config option:
  "job" (default):  per-JOB targets, target.id = job_key, payload = {"matched_nodes": {node: entries}}
  "node":           per-NODE targets, target.id = hostname, payload = {"entries": [...], "source_jobs": [...]}
                    Use "node" when combining with SSH/Prometheus in a multi-source collector —
                    per-node targets merge correctly with SSH/Prometheus targets.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from patrol_cron.collectors.base import BaseCollector
from patrol_cron.collectors.nfd_data_sources import JobLogsClient, JobMetadataClient
from patrol_cron.models import CollectionResult, TargetData

logger = logging.getLogger(__name__)


class JobLogsCollector(BaseCollector):
    """Collects job logs, pattern-matches, and returns per-job or per-node targets."""

    def collect(self, collector_cfg: dict) -> CollectionResult:
        start = time.time()
        name = collector_cfg["name"]
        target_filter = collector_cfg.get("target_filter") or {}
        sources = collector_cfg.get("sources") or []

        src = next((s for s in sources if s.get("type") == "job_logs"), None)
        if not src:
            return CollectionResult(collector_name=name, errors=["No job_logs source configured"])

        if JobLogsClient is None or JobMetadataClient is None:
            return CollectionResult(collector_name=name, errors=["NFD data_sources not available"])

        config = src.get("config", {})
        patterns = config.get("patterns", [])
        tail = config.get("tail", True)
        max_entries = config.get("max_entries")
        group_by = config.get("group_by", "job")  # "job" (default) or "node"

        targets: list[TargetData] = []
        errors: list[str] = []

        # For group_by="node": aggregate entries per node across all jobs
        node_entries = defaultdict(list)       # {node_name: [entry, ...]}
        node_source_jobs = defaultdict(list)   # {node_name: [job_key, ...]}

        try:
            meta_client = JobMetadataClient()
            log_client = JobLogsClient()

            end_time = int(time.time())
            lookback = collector_cfg.get("schedule_sec", 3600) * 2
            start_time = end_time - lookback

            # Get failed jobs with full metadata (frameworkName, nodes, etc.)
            filters = target_filter or {"status": ["failed"]}
            basic_jobs = meta_client.get_job_metadata()
            jobs = meta_client.get_filtered_job_attempts(
                start_time, end_time, filters, basic_jobs=basic_jobs,
            )

            logger.info(f"[{name}] Found {len(jobs)} jobs matching filters")

            # Sample limit for testing
            sample = target_filter.get("sample")
            if sample and int(sample) > 0:
                jobs = dict(list(jobs.items())[:int(sample)])

            for job_key, job_meta in jobs.items():
                try:
                    job_name = f'{job_meta.get("username")}~{job_meta.get("name")}'
                    log_contents = log_client.get_job_logs(
                        job_name=job_name, job_meta=job_meta, tail=tail,
                    )
                    if not log_contents:
                        logger.debug(f"[{name}] No logs for job {job_key}")
                        continue

                    # Pattern-match per node
                    matched_nodes = {}
                    total_lines = sum(len(c.splitlines()) for c in log_contents.values() if c)
                    for node_name, content in log_contents.items():
                        if not content:
                            continue
                        entries = log_client.parse_logs_with_patterns(
                            log_content=content,
                            patterns=patterns,
                            max_entries=max_entries,
                        )
                        if entries:
                            parsed = [
                                {"message": e.message, "fields": getattr(e, "fields", {})}
                                for e in entries
                            ]
                            matched_nodes[node_name] = parsed

                            if group_by == "node":
                                node_entries[node_name].extend(parsed)
                                node_source_jobs[node_name].append(job_key)

                    if matched_nodes and group_by == "job":
                        targets.append(TargetData(
                            id=job_key,
                            type="job",
                            payload={"matched_nodes": matched_nodes},
                            meta={
                                "username": job_meta.get("username"),
                                "job_name": job_meta.get("name"),
                                "state": job_meta.get("state"),
                                "nodes": list(job_meta.get("nodes", {}).keys()),
                            },
                        ))

                except Exception as e:
                    errors.append(f"{job_key}: {e}")
                    logger.debug(f"Error processing job {job_key}: {e}")

        except Exception as e:
            errors.append(f"Failed to fetch jobs: {e}")
            logger.error(f"[{name}] Failed: {e}")

        # group_by="node": emit per-node targets with aggregated entries
        if group_by == "node":
            for node_name in sorted(node_entries.keys()):
                entries = node_entries[node_name]
                source_jobs = node_source_jobs[node_name]
                targets.append(TargetData(
                    id=node_name,
                    type="node",
                    payload={
                        "entries": entries,
                        "source_jobs": source_jobs,
                    },
                    meta={"source_job_count": len(source_jobs)},
                ))

        duration = time.time() - start
        log_jobs = sum(1 for j in jobs if j)  # count non-None
        logger.info(f"[{name}] Collected {len(targets)} targets (group_by={group_by}) in {duration:.1f}s — {len(jobs)} jobs checked, {log_jobs} had logs, {len(node_entries)} nodes matched" if group_by == "node" else f"[{name}] Collected {len(targets)} targets (group_by={group_by}) in {duration:.1f}s")
        return CollectionResult(collector_name=name, targets=targets,
                                errors=errors, duration=duration)
