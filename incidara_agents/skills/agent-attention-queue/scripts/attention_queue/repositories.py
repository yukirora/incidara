from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .models import RawSignal, SignalKind

# ── Question classification ──────────────────────────────────────────────

_ROUTINE_KEYWORDS = re.compile(
    r"\b(approve|proceed|continue|confirm|OK to|ready to|go ahead)\b", re.I
)
_OPERATIONAL_KEYWORDS = re.compile(
    r"\b(classify|categorize|hardware or|platform or|diagnose|root cause|route this|"
    r"what should I|how should I|reallocate|recycl)\b", re.I
)


def _is_operational_question(text: str) -> bool:
    """Return True if this question is operational (needs human judgment),
    not routine (approve/continue)."""
    if not text:
        return False
    if _OPERATIONAL_KEYWORDS.search(text):
        return True
    if _ROUTINE_KEYWORDS.search(text):
        return False
    return False


# ── Event file readers ─────────────────────────────────────────────────────

def _last_line(path: Path) -> str | None:
    """Read the last non-empty line of a file efficiently."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)  # end
            size = f.tell()
            if size == 0:
                return None
            # Read last 8KB
            chunk_size = min(size, 8192)
            f.seek(size - chunk_size)
            data = f.read(chunk_size).decode("utf-8", errors="replace")
            lines = [l for l in data.splitlines() if l.strip()]
            return lines[-1] if lines else None
    except OSError:
        return None


def _event_files(root: Path, max_age_hours: int = 24) -> Iterable[Path]:
    """Return event files from sessions updated within max_age_hours."""
    if not root.exists():
        return []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    result = []
    for session_dir in sorted(root.iterdir()):
        if not session_dir.is_dir():
            continue
        events_file = session_dir / "events.jsonl"
        if not events_file.exists():
            continue
        # Check meta.json for recency
        meta_file = session_dir / "meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(errors="replace"))
                updated = meta.get("updatedAt") or meta.get("createdAt")
                if updated:
                    updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    if updated_dt < cutoff:
                        continue
            except (json.JSONDecodeError, ValueError):
                pass  # Can't determine time — include anyway
        # Also read the last line of events.jsonl for a more precise timestamp
        try:
            last_line = _last_line(events_file)
            if last_line:
                event = json.loads(last_line)
                ts = event.get("timestamp")
                if ts:
                    event_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if event_dt < cutoff:
                        continue
        except (json.JSONDecodeError, ValueError, OSError):
            pass
        result.append(events_file)
    return result


def _payload_text(payload: dict) -> str:
    for key in ("content", "result", "error", "message", "question"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return json.dumps(payload, sort_keys=True)[:500]


# Regex for detecting errors embedded in successful tool output
_ERROR_IN_OUTPUT = re.compile(
    r'"error":|"errors":|"failed":|"Error":|"Errors":|"Failed":|timeout|ConnectionRefused|'
    r'HTTP 401|HTTP 500|HTTP 502|HTTP 503|read timed out|Connection refused',
    re.I
)


def _has_embedded_error(result_text: str) -> bool:
    """Check if a tool result (successful output) contains embedded errors.
    
    Some MCP tools return HTTP 200 with errors in the JSON body
    instead of raising exceptions. This catches those.
    """
    if not result_text:
        return False
    return bool(_ERROR_IN_OUTPUT.search(result_text))


def _result_text(payload: dict) -> str:
    """Extract result text from a tool.finished payload.
    
    Handles double-encoded JSON (result is a JSON string containing another JSON string).
    """
    result = payload.get("result")
    if not isinstance(result, str):
        return ""
    # Try to decode nested JSON (MCP tools sometimes double-encode)
    try:
        decoded = json.loads(result)
        if isinstance(decoded, dict):
            return json.dumps(decoded)
        if isinstance(decoded, str):
            return decoded
    except (json.JSONDecodeError, TypeError):
        pass
    return result


def _last_assistant_text(payload: dict) -> str:
    """Extract text from assistant message content (which is a list of blocks)."""
    content = payload.get("content")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if isinstance(t, str):
                    parts.append(t)
        return " ".join(parts)
    return str(content) if content else ""


def _find_session_text(session_roots: dict[str, str], session_id: str) -> str | None:
    """Find events.jsonl for a session_id — returns full text or None."""
    for root_str in session_roots.values():
        path = Path(root_str) / session_id / "events.jsonl"
        if path.exists():
            return path.read_text(errors="replace")
    return None


def read_hung_tool_signals(session_roots: dict[str, str]) -> list[RawSignal]:
    """Detect tools that were started but never finished (hanging tools).

    Scans ALL sessions (no time window) for tool.started without matching
    tool.finished. A hanging tool blocks the agent and wastes tokens until timeout.
    """
    signals: list[RawSignal] = []
    for agent_id, root_str in session_roots.items():
        root = Path(root_str)
        if not root.exists():
            continue
        for session_dir in sorted(root.iterdir()):
            if not session_dir.is_dir():
                continue
            events_file = session_dir / "events.jsonl"
            if not events_file.exists():
                continue
            session_id = session_dir.name
            started_tools: dict[str, str] = {}  # tool_use_id -> tool_name|timestamp

            for line in events_file.read_text(errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("event_type")
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                tool_id = payload.get("tool_use_id", "")
                tool_name = payload.get("tool_name", "")

                if event_type == "tool.started" and tool_id:
                    ts = event.get("timestamp", "")
                    started_tools[tool_id] = f"{tool_name}|{ts}"

                elif event_type == "tool.finished" and tool_id in started_tools:
                    del started_tools[tool_id]

            # Remaining started_tools never got a matching tool.finished
            for tool_id, info in started_tools.items():
                parts = info.split("|", 1)
                tool_name = parts[0]
                ts = parts[1] if len(parts) > 1 else ""
                signals.append(RawSignal(
                    kind=SignalKind.TOOL_HUNG,
                    agent_id=agent_id,
                    session_id=session_id,
                    tool_name=tool_name,
                    text=f"Tool {tool_name} started but never finished (hung) in session {session_id}",
                    timestamp=ts,
                ))

    return signals


# ── Main scanner entry point ───────────────────────────────────────────────

def read_gateway_event_signals(session_roots: dict[str, str], max_age_hours: int = 24) -> list[RawSignal]:
    """Scan all gateway events for tool failures and question events.
    
    Only scans sessions updated within max_age_hours."""
    signals: list[RawSignal] = []
    fingerprint_counts: dict[tuple[str, str, str], int] = defaultdict(int)

    for agent_id, root_str in session_roots.items():
        for path in _event_files(Path(root_str), max_age_hours=max_age_hours):
            session_signals: list[RawSignal] = []
            # Track tool_use_id -> tool_name from tool.started events
            tool_name_map: dict[str, str] = {}

            for line in path.read_text(errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("event_type")
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                session_id = event.get("session_id") or path.parent.name
                timestamp = event.get("timestamp")
                tool_id = payload.get("tool_use_id", "")

                # Track tool.started to map tool_use_id -> tool_name
                if event_type == "tool.started" and tool_id:
                    tn = payload.get("tool_name", "")
                    if tn:
                        tool_name_map[tool_id] = tn

                # Resolve tool_name from tool.started map (Chat UI correlation)
                resolved_tool_name = tool_name_map.get(tool_id, payload.get("tool_name", "") or "unknown")

                # Tool failure — explicit error
                if event_type == "tool.finished" and (payload.get("is_error") or payload.get("error")):
                    session_signals.append(RawSignal(
                        kind=SignalKind.TOOL_FAILURE,
                        agent_id=agent_id,
                        session_id=session_id,
                        tool_name=resolved_tool_name,
                        text=_payload_text(payload),
                        timestamp=timestamp,
                    ))

                # Tool finished successfully but output contains embedded errors
                elif event_type == "tool.finished":
                    result = _result_text(payload)
                    if _has_embedded_error(result):
                        session_signals.append(RawSignal(
                            kind=SignalKind.TOOL_FAILURE,
                            agent_id=agent_id,
                            session_id=session_id,
                            tool_name=resolved_tool_name,
                            text=f"Embedded error in output: {result[:300]}",
                            timestamp=timestamp,
                        ))

                # Explicit question from tool — filter routine at script level
                elif event_type == "question.requested":
                    text = _payload_text(payload)
                    if _is_operational_question(text):
                        session_signals.append(RawSignal(
                            kind=SignalKind.QUESTION_REQUESTED,
                            agent_id=agent_id,
                            session_id=session_id,
                            text=text,
                            timestamp=timestamp,
                        ))

                # Permission requested — filter routine at script level
                elif event_type == "permission.requested":
                    text = _payload_text(payload)
                    if _is_operational_question(text):
                        session_signals.append(RawSignal(
                            kind=SignalKind.WAITING_INPUT,
                            agent_id=agent_id,
                            session_id=session_id,
                            tool_name=resolved_tool_name,
                            text=text,
                            timestamp=timestamp,
                        ))

            for sig in session_signals:
                from .fingerprints import error_fingerprint
                fp = error_fingerprint(sig.text)
                key = (agent_id, fp, sig.session_id or "")
                fingerprint_counts[key] += 1

            signals.extend(session_signals)

    # ── Burst annotation ──────────────────────────────────────────────────

    for i, sig in enumerate(signals):
        from .fingerprints import error_fingerprint
        fp = error_fingerprint(sig.text)
        key = (sig.agent_id, fp, sig.session_id or "")
        count = fingerprint_counts.get(key, 1)
        if count >= 2:
            signals[i] = RawSignal(
                kind=sig.kind,
                agent_id=sig.agent_id,
                session_id=sig.session_id,
                task_id=sig.task_id,
                tool_name=sig.tool_name,
                text=sig.text,
                timestamp=sig.timestamp,
                node=sig.node,
                workflow=sig.workflow,
                baseline_count=0,
                window_count=count,
            )

    return signals


def read_waiting_session_questions(
    session_roots: dict[str, str],
    waiting_session_ids: list[str],
) -> list[RawSignal]:
    """Read the last assistant message from sessions that are in waiting_input state.

    Called after behavior-scan queries Chat UI DB for waiting_input tasks.
    Each session_id is the gateway_session_id from the tasks table.

    Returns signals only for sessions with operational questions (not routine approval).
    """
    signals: list[RawSignal] = []
    for session_id in waiting_session_ids:
        content = _find_session_text(session_roots, session_id)
        if not content:
            continue

        # Find the last message.agent event
        last_agent_text = ""
        last_agent_timestamp = ""
        last_agent_name = ""
        for line in reversed(content.splitlines()):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event_type") == "message.agent":
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                last_agent_text = _last_assistant_text(payload)
                last_agent_timestamp = event.get("timestamp", "")
                last_agent_name = event.get("agent_id") or ""
                break

        if last_agent_text:
            # Pass all waiting sessions to the LLM for classification.
            # The LLM decides: operational (needs human judgment) vs routine (approve/continue).
            signals.append(RawSignal(
                kind=SignalKind.WAITING_INPUT,
                agent_id=last_agent_name,
                session_id=session_id,
                text=last_agent_text[:500],
                timestamp=last_agent_timestamp,
            ))

    return signals


def signals_from_task_rows(rows: list[dict]) -> list[RawSignal]:
    """Extract task-level signals from Chat UI DB rows.

    Called by behavior-scan sub-skill after querying the DB.
    """
    signals: list[RawSignal] = []
    for row in rows:
        status = str(row.get("status") or "")
        kind = None
        if status == "error":
            kind = SignalKind.TASK_ERROR
        elif status == "waiting_input":
            kind = SignalKind.WAITING_INPUT
        if kind is None:
            continue
        text = " ".join(
            str(row.get(key) or "") for key in ("title", "prompt") if row.get(key)
        ).strip()
        signals.append(RawSignal(
            kind=kind,
            agent_id=str(row.get("agent_id") or "unknown"),
            session_id=str(row.get("gateway_session_id") or row.get("session_id") or ""),
            task_id=str(row.get("task_id") or row.get("id") or ""),
            text=text,
            timestamp=str(row.get("created_at") or ""),
        ))
    return signals