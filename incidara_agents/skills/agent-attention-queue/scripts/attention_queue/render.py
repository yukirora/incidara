from __future__ import annotations

from .models import AttentionItem


def _join(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def render_report(items: list[AttentionItem], generated_at: str) -> str:
    lines = [f"# Agent Attention Queue - {generated_at}", ""]
    if not items:
        lines.extend(["No attention items found.", ""])
        return "\n".join(lines)

    for idx, item in enumerate(items, start=1):
        lines.append(f"## {idx}. {item.title}")
        affected = f"{_join(item.affected_agents)}; {len(item.affected_tasks)} tasks"
        if item.affected_nodes:
            affected += f"; {len(item.affected_nodes)} nodes"
        lines.append(f"Affected: {affected}")
        lines.append(f"Impact: {item.impact_reason}")
        recency_parts = []
        if item.last_1h:
            recency_parts.append(f"{item.last_1h} last hour")
        if item.last_24h:
            recency_parts.append(f"{item.last_24h} last 24h")
        if item.older:
            recency_parts.append(f"{item.older} older")
        lines.append(f"Recency: {', '.join(recency_parts) if recency_parts else 'unknown'}")
        lines.append(f"Why attention: {item.category.value.replace('_', ' ')}; impact score {item.impact_score}.")
        if item.evidence:
            evidence_bits = []
            for evidence in item.evidence[:3]:
                prefix = evidence.session_id or evidence.task_id or evidence.type
                if evidence.tool_name:
                    evidence_bits.append(f"{prefix} `{evidence.tool_name}`: {evidence.summary}")
                else:
                    evidence_bits.append(f"{prefix}: {evidence.summary}")
            lines.append("Evidence: " + "; ".join(evidence_bits))
        if item.suggested_next_task:
            lines.append(f"Suggested next task: {item.suggested_next_task.title}")
        lines.append("")
    return "\n".join(lines)
