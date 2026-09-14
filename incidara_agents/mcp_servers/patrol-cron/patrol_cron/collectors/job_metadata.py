"""Job metadata collector — fetches job attempts from LTP REST API.

Two-level filtering:
  job_filter:     runs on basic job list (1 API call for all jobs). Fast.
                  Safe fields: totalTaskNumber, username, name, virtualCluster, tags, etc.
                  NOT safe: state, duration (vary per attempt).
  attempt_filter: runs on per-attempt data (1 API call per candidate). Slow but accurate.
                  All fields: state, duration, gpu_count, nodes, taskRoles, etc.

Collector config:
  target_filter:
    lookback_days: 60
    job_filter: "totalTaskNumber > 32 and virtualCluster == 'vc1'"
    attempt_filter: "state == 'FAILED' and duration > d(14)"

Available in both filters:
  now_ms, d(n), h(n), m(n), int, str, len, abs, min, max
  Plus all job/attempt metadata fields by name.
For time checks, write explicitly:
  launchedTime and now_ms - launchedTime > d(14)
  NOT: duration > d(14)  (duration is not a real field)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from patrol_cron.collectors.base import BaseCollector
from patrol_cron.collectors.nfd_data_sources import JobMetadataClient
from patrol_cron.models import CollectionResult, TargetData

logger = logging.getLogger(__name__)


class JobMetadataCollector(BaseCollector):
    """Collects job attempt metadata with two-level eval filtering."""

    def collect(self, collector_cfg: dict) -> CollectionResult:
        start_t = time.time()
        name = collector_cfg["name"]
        target_filter = collector_cfg.get("target_filter") or {}
        sources = collector_cfg.get("sources") or []

        src = next((s for s in sources if s.get("type") == "job_metadata"), None)
        if not src:
            return CollectionResult(collector_name=name, errors=["No job_metadata source configured"])

        lookback_days = target_filter.get("lookback_days", 60)
        job_filter = target_filter.get("job_filter", "")
        attempt_filter = target_filter.get("attempt_filter", "")

        try:
            client = JobMetadataClient()
            now = datetime.now(timezone.utc)
            end_ts = int(now.timestamp())
            start_ts = int((now - timedelta(days=lookback_days)).timestamp())

            attempts = client.get_filtered_job_attempts(
                start_ts, end_ts,
                job_filter=job_filter,
                attempt_filter=attempt_filter,
            )
        except Exception as e:
            return CollectionResult(collector_name=name, errors=[f"Failed: {e}"])

        if not attempts:
            return CollectionResult(collector_name=name, targets=[], errors=[])

        targets: list[TargetData] = []
        for attempt_key, attempt in attempts.items():
            gpu_count = attempt.get("totalGpuNumber", 0)

            targets.append(TargetData(
                id=attempt_key,
                type="job",
                payload=dict(attempt, gpu_count=gpu_count),
                meta={"state": attempt.get("state", ""), "gpu_count": gpu_count},
            ))

        # Sample limit for testing
        sample = target_filter.get("sample")
        if sample and int(sample) > 0:
            targets = targets[:int(sample)]

        duration = time.time() - start_t
        logger.info(f"[{name}] Collected {len(targets)} targets in {duration:.1f}s")
        return CollectionResult(collector_name=name, targets=targets,
                                errors=[], duration=duration)
