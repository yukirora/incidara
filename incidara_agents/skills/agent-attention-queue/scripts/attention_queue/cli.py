from __future__ import annotations

import argparse
from datetime import datetime, timezone

from .grouping import group_signals
from .render import render_report
from .repositories import read_gateway_event_signals, read_waiting_session_questions, read_hung_tool_signals
from .scoring import rank_items


DEFAULT_SESSION_ROOTS = {
    "triage": "/mnt/sessions/triage",
    "repair": "/mnt/sessions/repair",
    "recycler": "/mnt/sessions/recycler",
    "detection": "/mnt/sessions/detection",
    "feedback": "/mnt/sessions/feedback",
}


def build_report(
    session_roots: dict[str, str],
    waiting_session_ids: list[str] | None = None,
    max_age_hours: int = 24,
    generated_at: str | None = None,
) -> str:
    signals = read_gateway_event_signals(session_roots, max_age_hours=max_age_hours)
    signals.extend(read_hung_tool_signals(session_roots))
    if waiting_session_ids:
        signals.extend(read_waiting_session_questions(session_roots, waiting_session_ids))
    items = rank_items(group_signals(signals))
    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return render_report(items, stamp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the Agent Attention Queue report.")
    parser.add_argument("--session-root", action="append", default=[], help="Agent session root in agent_id=/path form.")
    parser.add_argument("--waiting-sessions", nargs="*", default=None, help="Session IDs in waiting_input state (from behavior-scan).")
    parser.add_argument("--max-age-hours", type=int, default=24, help="Only scan sessions updated within this many hours (default: 24).")
    args = parser.parse_args(argv)
    roots = dict(DEFAULT_SESSION_ROOTS)
    for item in args.session_root:
        agent_id, sep, path = item.partition("=")
        if not sep or not agent_id or not path:
            parser.error("--session-root must use agent_id=/path")
        roots[agent_id] = path
    print(build_report(roots, waiting_session_ids=args.waiting_sessions, max_age_hours=args.max_age_hours))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())