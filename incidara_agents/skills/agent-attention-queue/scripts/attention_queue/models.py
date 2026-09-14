from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AttentionCategory(str, Enum):
    BROKEN_CAPABILITY = "broken_capability"
    BAD_AGENT_BEHAVIOR = "bad_agent_behavior"
    FLEET_ANOMALY = "fleet_anomaly"
    HUMAN_DECISION = "human_decision"


class SignalKind(str, Enum):
    TOOL_FAILURE = "tool_failure"
    DELEGATION_FAILURE = "delegation_failure"
    ALERT_SUBMISSION_FAILURE = "alert_submission_failure"
    TASK_ERROR = "task_error"
    WAITING_INPUT = "waiting_input"
    QUESTION_REQUESTED = "question_requested"
    REPEATED_WORKFLOW = "repeated_workflow"
    HEALTH_FAILURE = "health_failure"
    TOOL_HUNG = "tool_hung"


@dataclass(frozen=True)
class RawSignal:
    kind: SignalKind
    agent_id: str
    session_id: str | None = None
    task_id: str | None = None
    tool_name: str | None = None
    text: str = ""
    timestamp: str | None = None
    node: str | None = None
    workflow: str | None = None
    baseline_count: int = 0
    window_count: int = 1


@dataclass(frozen=True)
class EvidenceRef:
    type: str
    summary: str
    session_id: str | None = None
    task_id: str | None = None
    tool_name: str | None = None
    node: str | None = None


@dataclass(frozen=True)
class TaskDraft:
    title: str
    owner_hint: str
    prompt: str


@dataclass
class AttentionItem:
    title: str
    category: AttentionCategory
    impact_score: int
    impact_reason: str
    affected_agents: list[str] = field(default_factory=list)
    affected_tasks: list[str] = field(default_factory=list)
    affected_nodes: list[str] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    suggested_next_task: TaskDraft | None = None
    last_1h: int = 0
    last_24h: int = 0
    older: int = 0
