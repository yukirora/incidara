"""Core data types for patrol_cron engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TargetData:
    """One entity returned by a collector."""
    id: str           # hostname, switch_name, job_name
    type: str         # "node" | "switch" | "job"
    payload: Any      # collector-specific raw data
    meta: dict = field(default_factory=dict)  # {ip, switch_type, ...}


@dataclass
class CollectionResult:
    """Output of a single collector run."""
    collector_name: str
    targets: list[TargetData] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration: float = 0.0
    run_time: float = 0.0  # Unix timestamp when collector ran; used for time-relative filtering in analyze()


@dataclass
class Finding:
    """One detected issue produced by analyze()."""
    target_id: str
    severity: str         # "critical" | "warning" | "info"
    action: str           # "cordon_node" | "drain_node" | "cordon_switch_nodes" | "alert" | "create_task" | "stop_abnormal_job" | "notify_abnormal_job"
    action_params: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    confidence: float = 1.0


@dataclass
class Observation:
    """One current rule observation.

    This is not a DB finding. The engine interprets it through lifecycle
    metadata to open, refresh, or close persisted findings.
    """
    signal_key: str
    target_id: str
    status: str                 # "bad" | "healthy" | "unknown"
    action: str
    severity: str = "warning"
    action_params: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    confidence: float = 1.0
    event_id: str | None = None
    lifecycle: dict = field(default_factory=lambda: {
        "kind": "condition",
        "open_after_consecutive": 1,
        "close_after_healthy": 1,
        "unknown_keeps_active": True,
    })
    dedup_key: tuple[str, ...] = ("rule_id", "target_id", "signal_key", "action")

    def __post_init__(self):
        if self.status not in ("bad", "healthy", "unknown"):
            raise ValueError("Observation.status must be bad, healthy, or unknown")
        lifecycle = {
            "kind": "condition",
            "open_after_consecutive": 1,
            "close_after_healthy": 1,
            "unknown_keeps_active": True,
        }
        lifecycle.update(self.lifecycle or {})
        self.lifecycle = lifecycle

    def to_finding(self) -> Finding:
        return Finding(
            target_id=self.target_id,
            severity=self.severity,
            action=self.action,
            action_params=self.action_params,
            evidence=self.evidence,
            confidence=self.confidence,
        )


@dataclass
class RuleResult:
    """Structured rule output plus private rule state."""
    observations: list[Observation] = field(default_factory=list)
    state: dict = field(default_factory=dict)
    lifecycle_enabled: bool = True

    def legacy_findings(self) -> list[Finding]:
        return [obs.to_finding() for obs in self.observations if obs.status == "bad"]

    def __iter__(self):
        """Allow legacy callers to unpack `findings, state = run_sandboxed(...)`."""
        yield self.legacy_findings()
        yield self.state
