"""Node logs collector — fetches system logs from nodes via log-manager.

Ported from NFD data_sources.py NodeLogsClient.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from patrol_cron.collectors.base import BaseCollector
from patrol_cron.collectors.nfd_data_sources import NodeLogsClient
from patrol_cron.models import CollectionResult, TargetData

logger = logging.getLogger(__name__)


class NodeLogsCollector(BaseCollector):
    """Collects system logs from nodes and returns per-node targets."""

    def collect(self, collector_cfg: dict) -> CollectionResult:
        start = time.time()
        name = collector_cfg["name"]
        target_filter = collector_cfg.get("target_filter") or {}
        sources = collector_cfg.get("sources") or []

        src = next((s for s in sources if s.get("type") == "node_logs"), None)
        if not src:
            return CollectionResult(collector_name=name, errors=["No node_logs source configured"])

        config = src.get("config", {})
        log_paths = config.get("log_paths", [])
        patterns = config.get("patterns", [])
        max_entries = config.get("max_entries")
        tail_lines = config.get("tail_lines", 1000)

        nodes = self._resolve_nodes(target_filter)
        if not nodes:
            return CollectionResult(collector_name=name, errors=["No nodes resolved"])

        # Sample limit for testing
        sample = target_filter.get("sample")
        if sample and int(sample) > 0:
            nodes = nodes[:int(sample)]

        targets: list[TargetData] = []
        errors: list[str] = []

        end_time = int(time.time())
        lookback = collector_cfg.get("schedule_sec", 3600) * 2
        start_time = end_time - lookback

        for hostname, ip in nodes:
            try:
                client = NodeLogsClient()
                entries = client.collect_node_logs(
                    node_ip=ip,
                    log_paths=log_paths,
                    patterns=patterns,
                    max_entries=max_entries,
                    tail_lines=tail_lines,
                    start_time_ts=start_time,
                    end_time_ts=end_time,
                )

                if entries:
                    targets.append(TargetData(
                        id=hostname,
                        type="node",
                        payload={
                            "log_entries": [
                                {"message": e.message, "fields": e.fields}
                                for e in entries
                            ],
                        },
                        meta={"ip": ip},
                    ))

            except Exception as e:
                errors.append(f"{hostname}: {e}")
                logger.debug(f"Error collecting logs from {hostname}: {e}")

        duration = time.time() - start
        logger.info(f"[{name}] Collected logs from {len(targets)}/{len(nodes)} nodes in {duration:.1f}s")
        return CollectionResult(collector_name=name, targets=targets,
                                errors=errors, duration=duration)

    @staticmethod
    def _resolve_nodes(target_filter: dict) -> list[tuple[str, str]]:
        """Resolve nodes — delegates to SSH collector's resolver."""
        from patrol_cron.collectors.ssh import SshCollector
        targets = SshCollector._resolve_targets(target_filter)
        return [(h, ip) for h, ip, meta in targets]
