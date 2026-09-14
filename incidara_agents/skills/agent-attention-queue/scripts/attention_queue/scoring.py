from __future__ import annotations

from .models import AttentionCategory, AttentionItem


_CATEGORY_WEIGHT = {
    AttentionCategory.BROKEN_CAPABILITY: 30,
    AttentionCategory.FLEET_ANOMALY: 24,
    AttentionCategory.HUMAN_DECISION: 22,
    AttentionCategory.BAD_AGENT_BEHAVIOR: 16,
}


def score_item(item: AttentionItem) -> AttentionItem:
    agents = len(item.affected_agents)
    tasks = len(item.affected_tasks)
    nodes = len(item.affected_nodes)
    evidence = len(item.evidence)
    score = _CATEGORY_WEIGHT[item.category]
    score += min(25, tasks * 2)
    score += min(25, nodes * 2)
    score += min(10, agents * 4)
    score += min(10, evidence * 2)
    if item.category is AttentionCategory.BROKEN_CAPABILITY and agents >= 2:
        score += 10
    item.impact_score = min(100, score)
    item.impact_reason = (
        f"{tasks} task(s), {nodes} node(s), {agents} agent(s), "
        f"{evidence} evidence reference(s); category={item.category.value}."
    )
    return item


def rank_items(items: list[AttentionItem]) -> list[AttentionItem]:
    return sorted((score_item(item) for item in items), key=lambda i: i.impact_score, reverse=True)
