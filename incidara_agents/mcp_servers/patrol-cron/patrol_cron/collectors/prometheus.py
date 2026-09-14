"""Prometheus collector — queries Prometheus and returns per-node/per-target results.

Uses PrometheusClient from nfd_data_sources (single source of truth).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from patrol_cron.collectors.base import BaseCollector
from patrol_cron.collectors.nfd_data_sources import PrometheusClient
from patrol_cron.models import CollectionResult, TargetData

logger = logging.getLogger(__name__)


class PrometheusCollector(BaseCollector):
    """Collects metric data from Prometheus.

    Each Prometheus result series becomes a TargetData entry, with the
    target ID extracted from the 'instance', 'node_name', or 'host_ip' label.
    """

    def collect(self, collector_cfg: dict) -> CollectionResult:
        start = time.time()
        name = collector_cfg["name"]
        target_type = collector_cfg.get("target_type", "node")
        sources = collector_cfg.get("sources") or []

        prom_src = next((s for s in sources if s.get("type") == "prometheus"), None)
        if not prom_src:
            return CollectionResult(collector_name=name, errors=["No prometheus source"])

        config = prom_src.get("config", {})
        query = config.get("query", "")
        step = config.get("step", "30s")

        client = self._get_client()
        end_time = int(time.time())
        lookback = collector_cfg.get("schedule_sec", 60) * 2  # 2x schedule as window
        start_time = end_time - lookback

        data = client.query_range(query, start_time, end_time, step=step)

        targets: list[TargetData] = []
        errors: list[str] = []

        if data and "result" in data:
            for series in data["result"]:
                metric = series.get("metric", {})
                target_id = (
                    metric.get("node_name")
                    or metric.get("hostname")
                    or metric.get("instance", "").split(":")[0]
                    or "unknown"
                )
                targets.append(TargetData(
                    id=target_id,
                    type=target_type,
                    payload=series,
                    meta=metric,
                ))
        elif data is None:
            errors.append(f"Prometheus query returned None for: {query}")

        duration = time.time() - start
        logger.info(f"[{name}] Collected {len(targets)} targets in {duration:.1f}s")
        return CollectionResult(collector_name=name, targets=targets,
                                errors=errors, duration=duration)

    @staticmethod
    def _get_client() -> PrometheusClient:
        return PrometheusClient()
