from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .fingerprints import error_fingerprint, problem_title_for_tool
from .models import AttentionCategory, AttentionItem, EvidenceRef, RawSignal, SignalKind, TaskDraft


def _parse_timestamp(ts: str | None) -> datetime | None:
    """Parse ISO timestamp string, return None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _recency_buckets(signals: list[RawSignal]) -> tuple[int, int, int]:
    """Return (last_1h, last_24h, older) counts from signal timestamps."""
    now = datetime.now(timezone.utc)
    h1, h24, old = 0, 0, 0
    for s in signals:
        dt = _parse_timestamp(s.timestamp)
        if not dt:
            old += 1
        elif now - dt <= timedelta(hours=1):
            h1 += 1
        elif now - dt <= timedelta(hours=24):
            h24 += 1
        else:
            old += 1
    return h1, h24, old


def _category_for_signal(signal: RawSignal) -> AttentionCategory:
    if signal.kind in {
        SignalKind.TOOL_FAILURE,
        SignalKind.DELEGATION_FAILURE,
        SignalKind.ALERT_SUBMISSION_FAILURE,
        SignalKind.HEALTH_FAILURE,
    }:
        return AttentionCategory.BROKEN_CAPABILITY
    if signal.kind in {SignalKind.WAITING_INPUT, SignalKind.QUESTION_REQUESTED}:
        return AttentionCategory.HUMAN_DECISION
    if signal.kind is SignalKind.REPEATED_WORKFLOW:
        return AttentionCategory.FLEET_ANOMALY
    if signal.kind is SignalKind.TOOL_HUNG:
        return AttentionCategory.BROKEN_CAPABILITY
    return AttentionCategory.BAD_AGENT_BEHAVIOR


def _group_key(signal: RawSignal) -> tuple[str, str]:
    if signal.kind in {SignalKind.WAITING_INPUT, SignalKind.QUESTION_REQUESTED}:
        text = signal.text.lower()
        if "classif" in text or "hardware" in text or "platform" in text:
            return ("decision", "classification_or_policy")
        return ("decision", "human_input")
    fp = error_fingerprint(signal.text)
    tool = signal.tool_name or signal.workflow or signal.kind.value
    return (tool, fp)


def _title_for_group(signals: list[RawSignal]) -> str:
    first = signals[0]
    if first.kind in {SignalKind.WAITING_INPUT, SignalKind.QUESTION_REQUESTED}:
        return "Human decision needed: classification or policy choice"
    return problem_title_for_tool(first.tool_name, error_fingerprint(first.text))


def _task_draft(title: str, signals: list[RawSignal]) -> TaskDraft:
    sessions = ", ".join(sorted({s.session_id for s in signals if s.session_id})[:5])
    return TaskDraft(
        title=f"Investigate: {title}",
        owner_hint="agent/tool owner",
        prompt=(
            f"Investigate attention item '{title}'. "
            f"Use these sessions as starting evidence: {sessions or 'none listed'}. "
            "Confirm impact, root cause, and the safest next action."
        ),
    )


def group_signals(signals: list[RawSignal]) -> list[AttentionItem]:
    grouped: dict[tuple[str, str], list[RawSignal]] = defaultdict(list)
    for signal in signals:
        grouped[_group_key(signal)].append(signal)

    items: list[AttentionItem] = []
    for bucket in grouped.values():
        title = _title_for_group(bucket)
        agents = sorted({s.agent_id for s in bucket if s.agent_id})
        tasks = sorted({s.task_id for s in bucket if s.task_id})
        nodes = sorted({s.node for s in bucket if s.node})
        evidence = [
            EvidenceRef(
                type=s.kind.value,
                session_id=s.session_id,
                task_id=s.task_id,
                tool_name=s.tool_name,
                node=s.node,
                summary=(s.text or s.kind.value)[:220],
            )
            for s in bucket[:5]
        ]
        count = sum(max(1, s.window_count) for s in bucket)
        last_1h, last_24h, older = _recency_buckets(bucket)
        items.append(
            AttentionItem(
                title=title,
                category=_category_for_signal(bucket[0]),
                impact_score=0,
                impact_reason=f"{count} related signal(s) across {len(agents)} agent(s).",
                affected_agents=agents,
                affected_tasks=tasks,
                affected_nodes=nodes,
                evidence=evidence,
                suggested_next_task=_task_draft(title, bucket),
                last_1h=last_1h,
                last_24h=last_24h,
                older=older,
            )
        )
    return items
