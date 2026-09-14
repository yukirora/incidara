#!/usr/bin/env python3
"""Mine a Claude SDK session transcript and produce a structured investigation trace.

Usage:
  # By case ID (uses EVIDENCE_DB_URL env var, or pass --db-url)
  python3 mine_transcript.py --case-id 406

  # By hostname (searches all transcripts for hostname)
  python3 mine_transcript.py --hostname h200-000102 --transcript-dir /mnt/transcripts

  # By session file directly
  python3 mine_transcript.py --session-file /mnt/transcripts/triage/-app-workspace/2b3de090.jsonl

Output: Markdown table with Who column (agent vs user), tool calls, results, and decisions.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


# ── DB lookup ──────────────────────────────────────────────────────────────

def lookup_sessions_by_case(case_id: int, db_url: str) -> list[dict]:
    """Look up claude_session_id from case_memory for a given case ID."""
    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 required for --case-id. Install with: pip install psycopg2-binary", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT claude_session_id FROM case_memory WHERE id = %s", (case_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row or not row[0]:
        return []

    sessions = row[0] if isinstance(row[0], list) else json.loads(row[0])
    return sessions


def find_transcript_by_hostname(hostname: str, transcript_dirs: list[str]) -> list[str]:
    """Search transcript files for a hostname, return matching file paths."""
    matches = []
    for base_dir in transcript_dirs:
        if not os.path.isdir(base_dir):
            continue
        for root, dirs, files in os.walk(base_dir):
            for fname in files:
                if not fname.endswith(".jsonl"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath) as f:
                        # Only read first 100 lines — hostname usually in early user prompt
                        for i, line in enumerate(f):
                            if i >= 100:
                                break
                            if hostname in line:
                                matches.append(fpath)
                                break
                except Exception:
                    pass
    return matches


# ── Transcript parsing ────────────────────────────────────────────────────

def parse_transcript(filepath: str) -> list[dict]:
    """Parse a Claude SDK JSONL transcript into a structured trace."""
    events = []

    with open(filepath) as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            evt_type = d.get("type", "")
            msg = d.get("message", {})
            role = msg.get("role", "")
            content = msg.get("content", [])
            tool_use_result = d.get("toolUseResult")

            if evt_type == "user" and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_result":
                            # Tool result — link back to the tool_use
                            tool_use_id = block.get("tool_use_id", "")
                            result_text = _extract_text(block.get("content", ""))
                            is_error = block.get("is_error", False)
                            events.append({
                                "who": "tool",
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "result": result_text,
                                "is_error": is_error,
                                "line": line_no,
                            })
                        elif block.get("type") == "text" and not tool_use_result:
                            # Actual user message (not tool result)
                            text = block["text"].strip()
                            if text and not _is_skill_autoload(text):
                                events.append({
                                    "who": "user",
                                    "type": "user_message",
                                    "text": text,
                                    "line": line_no,
                                })

            elif evt_type == "assistant" and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_use":
                            tool_name = block.get("name", "")
                            tool_input = block.get("input", {})
                            tool_use_id = block.get("id", "")
                            events.append({
                                "who": "agent",
                                "type": "tool_use",
                                "tool_name": tool_name,
                                "tool_input": _format_tool_input(tool_name, tool_input),
                                "tool_use_id": tool_use_id,
                                "line": line_no,
                            })
                        elif block.get("type") == "text":
                            text = block["text"].strip()
                            if text:
                                # Extract agent decisions/conclusions
                                events.append({
                                    "who": "agent",
                                    "type": "agent_text",
                                    "text": text,
                                    "line": line_no,
                                })

    return events


def _is_skill_autoload(text: str) -> bool:
    """Check if a user message is just the skill auto-load content (not real user input)."""
    return text.startswith("Base directory for this skill:") or \
           text.startswith("# ") and "Optimize" in text[:50]


def _extract_text(content) -> str:
    """Extract text from tool result content (can be str or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content)





def _unwrap_result(result_text: str, is_error: bool) -> str:
    """Unwrap MCP {"result":"..."} wrappers, otherwise return full text."""
    if not result_text:
        return ""

    text = result_text.strip()

    if is_error:
        return "❌ " + text

    # MCP results wrapped in {"result": "..."} — unwrap (may be nested)
    for _ in range(3):
        if '"result"' in text[:80] and text.startswith("{"):
            try:
                d = json.loads(text)
                inner = d.get("result", "")
                if isinstance(inner, str) and len(inner) > 2:
                    text = inner
                else:
                    break
            except (json.JSONDecodeError, TypeError):
                # Result may be truncated — try regex extraction of "result" value
                m = re.search(r'"result"\s*:\s*"((?:[^"\\]|\\.)*)', text)
                if m:
                    try:
                        text = json.loads('"' + m.group(1) + '"')  # decode the string
                        continue
                    except (json.JSONDecodeError, TypeError):
                        pass
                break

    # Detect common bash errors that aren't wrapped in ❌
    bash_error_prefixes = [
        "bash:", "sh:", "/bin/sh:", "command not found",
        "No such file or directory", "Permission denied",
    ]
    for prefix in bash_error_prefixes:
        if prefix.lower() in text[:80].lower():
            return "❌ " + text

    return text


def _format_tool_input(tool_name: str, tool_input: dict) -> str:
    """Format tool input as a readable string."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        desc = tool_input.get("description", "")
        if desc:
            return f"{cmd}  [{desc}]"
        return cmd
    elif tool_name == "Agent":
        desc = tool_input.get("description", "")
        sa_type = tool_input.get("subagent_type", "")
        prompt = tool_input.get("prompt", "")
        return f"subagent_type={sa_type} description={desc}\n\n--- PROMPT ---\n{prompt}\n--- END PROMPT ---"
    elif tool_name.startswith("mcp__"):
        parts = []
        for k, v in tool_input.items():
            parts.append(f"{k}={v}")
        return " ".join(parts)
    elif tool_name in ("Read", "Edit"):
        fp = tool_input.get("file_path", "")
        if tool_name == "Edit":
            old = tool_input.get("old_string", "")
            new = tool_input.get("new_string", "")
            return f"{fp}  [-{old}→+{new}]"
        return fp
    elif tool_name == "Skill":
        return f"skill={tool_input.get('skill', '')} args={tool_input.get('args', '')}"
    elif tool_name in ("TaskCreate", "TaskUpdate"):
        parts = []
        for k, v in tool_input.items():
            parts.append(f"{k}={v}")
        return " ".join(parts)
    else:
        parts = []
        for k, v in tool_input.items():
            parts.append(f"{k}={v}")
        return " ".join(parts)


# ── Link tool calls to results ────────────────────────────────────────────

def link_tool_results(events: list[dict]) -> list[dict]:
    """Link tool_result events back to their tool_use events."""
    # Build index: tool_use_id → tool_use event index
    tool_use_idx = {}
    for i, evt in enumerate(events):
        if evt["type"] == "tool_use":
            tool_use_idx[evt["tool_use_id"]] = i

    # Merge tool results into tool_use events
    merged = []
    pending_results = {}  # tool_use_id → result text

    for evt in events:
        if evt["type"] == "tool_result":
            tid = evt["tool_use_id"]
            pending_results[tid] = evt["result"]
            if evt.get("is_error"):
                pending_results[tid] = "❌ ERROR: " + pending_results[tid]
            continue
        elif evt["type"] == "tool_use":
            # Check if we already have a result (from earlier processing)
            pass
        merged.append(evt)

    # Now add results to tool_use events
    result_merged = []
    for evt in merged:
        if evt["type"] == "tool_use":
            tid = evt.get("tool_use_id", "")
            if tid in pending_results:
                evt["result"] = pending_results[tid]
            else:
                evt["result"] = ""
        result_merged.append(evt)

    return result_merged


# ── Output formatting ─────────────────────────────────────────────────────

def format_trace(events: list[dict], title: str = "") -> str:
    """Format events as a Markdown trace table with Who column."""
    lines = []
    if title:
        lines.append(f"## {title}")
        lines.append("")

    lines.append("| # | Who | Action | Detail | Result |")
    lines.append("|---|-----|--------|--------|--------|")

    step = 0
    for evt in events:
        if evt["type"] == "tool_result":
            continue  # merged into tool_use

        step += 1
        who = evt["who"]

        if evt["type"] == "tool_use":
            action = f"**{evt['tool_name']}**"
            detail = evt.get("tool_input", "")
            result = evt.get("result", "")
            # Unwrap MCP result wrappers, detect errors
            is_err = result.startswith("❌")
            result = _unwrap_result(result.replace("❌ ERROR: ", "").replace("❌ ", ""), is_err) if result else ""
            # Escape pipes, preserve newlines as <br>
            detail = detail.replace("|", "\\|").replace("\n", "<br>")
            result = result.replace("|", "\\|").replace("\n", "<br>")
        elif evt["type"] == "user_message":
            action = "💬 user"
            detail = evt["text"].replace("|", "\\|").replace("\n", "<br>")
            result = ""
        elif evt["type"] == "agent_text":
            action = "🤖 agent"
            detail = evt["text"].replace("|", "\\|").replace("\n", "<br>")
            result = ""
        else:
            continue

        lines.append(f"| {step} | {who} | {action} | {detail} | {result} |")

    return "\n".join(lines)





# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Mine a Claude SDK session transcript")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--case-id", type=int, help="Case ID to look up in case_memory DB")
    group.add_argument("--hostname", help="Hostname to search for in transcripts")
    group.add_argument("--session-file", help="Direct path to a .jsonl transcript file")
    parser.add_argument("--db-url", default=os.environ.get("EVIDENCE_DB_URL", ""),
                        help="PostgreSQL connection string for case_memory DB")
    parser.add_argument("--transcript-dir", default="/mnt/transcripts",
                        help="Base directory for transcript search")

    args = parser.parse_args()

    if args.case_id:
        if not args.db_url:
            print("ERROR: --db-url or EVIDENCE_DB_URL required for --case-id", file=sys.stderr)
            sys.exit(1)
        sessions = lookup_sessions_by_case(args.case_id, args.db_url)
        if not sessions:
            print(f"No sessions found for case {args.case_id}", file=sys.stderr)
            sys.exit(1)

        # Process each session
        for sess in sessions:
            agent = sess.get("agent", "unknown")
            claude_id = sess.get("claude_session_id", "")
            gw_id = sess.get("gateway_session_id", "")

            # Map agent → mount path
            mount = agent
            if agent in ("triage-unknown", "triage"):
                mount = "triage"
            elif agent in ("repair", "repair-draft"):
                mount = "repair"
            elif agent == "recycler":
                mount = "recycler"

            # Try to find transcript file
            transcript_path = None
            for subpath in ["-app-workspace/", ""]:
                candidate = os.path.join(args.transcript_dir, mount, subpath, f"{claude_id}.jsonl")
                if os.path.isfile(candidate):
                    transcript_path = candidate
                    break

            if not transcript_path:
                print(f"⚠️ Transcript not found for {agent} session {claude_id}", file=sys.stderr)
                continue

            title = f"Agent: {agent} | Session: {gw_id} | Claude: {claude_id[:8]}..."
            events = parse_transcript(transcript_path)
            events = link_tool_results(events)

            print(format_trace(events, title))
            print()

    elif args.hostname:
        dirs = [os.path.join(args.transcript_dir, d) for d in ("triage", "repair", "recycler")]
        matches = find_transcript_by_hostname(args.hostname, dirs)
        if not matches:
            print(f"No transcripts found mentioning {args.hostname}", file=sys.stderr)
            sys.exit(1)

        for fpath in matches:
            title = f"File: {fpath}"
            events = parse_transcript(fpath)
            events = link_tool_results(events)

            print(format_trace(events, title))
            print()

    elif args.session_file:
        events = parse_transcript(args.session_file)
        events = link_tool_results(events)

        print(format_trace(events))


if __name__ == "__main__":
    main()
