"""Collection engine — dispatches to collectors based on source type.

Single source: dispatches to one collector, returns its result directly.
Multi source: runs each source collector, merges results by target ID.
  Each target's payload is keyed by source name:
    {source_name: source_payload, ...}
"""

from __future__ import annotations

import logging
import time

from patrol_cron.models import CollectionResult, TargetData

logger = logging.getLogger(__name__)

# Lazy-loaded collector registry
_COLLECTORS: dict | None = None


def _get_collectors():
    global _COLLECTORS
    if _COLLECTORS is None:
        from patrol_cron.collectors.ssh import SshCollector
        from patrol_cron.collectors.prometheus import PrometheusCollector
        _COLLECTORS = {
            "ssh": SshCollector(),
            "prometheus": PrometheusCollector(),
        }
        try:
            from patrol_cron.collectors.job_logs import JobLogsCollector
            _COLLECTORS["job_logs"] = JobLogsCollector()
        except Exception:
            pass
        try:
            from patrol_cron.collectors.node_logs import NodeLogsCollector
            _COLLECTORS["node_logs"] = NodeLogsCollector()
        except Exception:
            pass
        try:
            from patrol_cron.collectors.job_metadata import JobMetadataCollector
            _COLLECTORS["job_metadata"] = JobMetadataCollector()
        except Exception:
            pass
        try:
            from patrol_cron.collectors.db_query import DbQueryCollector
            _COLLECTORS["db_query"] = DbQueryCollector()
        except Exception:
            pass
    return _COLLECTORS


def run_collector(collector_cfg: dict) -> CollectionResult:
    """Run collector(s) based on source types in config.

    Single source: dispatch to one collector, return result as-is.
    Multi source: run each source, merge targets by ID.
    """
    name = collector_cfg["name"]
    sources = collector_cfg.get("sources") or []
    if not sources:
        return CollectionResult(collector_name=name, errors=["No sources configured"])

    if len(sources) == 1:
        return _run_single(collector_cfg, sources[0])

    return _run_multi(collector_cfg, sources)


def _run_single(collector_cfg: dict, source: dict) -> CollectionResult:
    """Run a single source collector."""
    name = collector_cfg["name"]
    source_type = source.get("type", "")
    collectors = _get_collectors()
    collector = collectors.get(source_type)

    if not collector:
        return CollectionResult(
            collector_name=name,
            errors=[f"Unknown source type: {source_type}. Available: {list(collectors.keys())}"],
        )

    return collector.collect(collector_cfg)


def _run_multi(collector_cfg: dict, sources: list) -> CollectionResult:
    """Run multiple source collectors and merge results by target ID.

    Each source runs with a modified collector_cfg that has only its source.
    Results are merged: each target's payload is keyed by source name.
    A target appears in the final result if ANY source produced it.
    """
    name = collector_cfg["name"]
    collectors = _get_collectors()
    start = time.time()

    all_errors: list[str] = []
    # {target_id: {source_name: payload}}
    merged: dict[str, dict] = {}
    # {target_id: TargetData} for meta
    target_meta: dict[str, TargetData] = {}

    for source in sources:
        source_type = source.get("type", "")
        source_name = source.get("name", source_type)
        collector = collectors.get(source_type)

        if not collector:
            all_errors.append(f"Unknown source type: {source_type}")
            continue

        # Build a single-source config for this collector
        single_cfg = dict(collector_cfg)
        single_cfg["sources"] = [source]

        try:
            result = collector.collect(single_cfg)
        except Exception as e:
            all_errors.append(f"Source {source_name} ({source_type}) failed: {e}")
            continue

        all_errors.extend(result.errors)

        for t in result.targets:
            if t.id not in merged:
                merged[t.id] = {}
                target_meta[t.id] = t
            merged[t.id][source_name] = t.payload

    # Build merged targets
    targets = []
    for tid, payloads in merged.items():
        base = target_meta[tid]
        targets.append(TargetData(
            id=tid,
            type=base.type,
            payload=payloads,
            meta=base.meta,
        ))

    duration = time.time() - start
    logger.info(f"[{name}] Multi-source collected {len(targets)} targets from {len(sources)} sources in {duration:.1f}s")
    return CollectionResult(collector_name=name, targets=targets,
                            errors=all_errors, duration=duration)
