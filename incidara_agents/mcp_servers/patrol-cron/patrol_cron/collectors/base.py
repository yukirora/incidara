"""Base collector interface."""

from __future__ import annotations

import abc
from typing import Any

from patrol_cron.models import CollectionResult


class BaseCollector(abc.ABC):
    """All collectors implement collect()."""

    @abc.abstractmethod
    def collect(self, collector_cfg: dict) -> CollectionResult:
        """Run collection and return results.

        Args:
            collector_cfg: Row from the collectors table (dict).
                           Has: name, target_type, target_filter, sources, etc.
        """
        ...
