"""Delegate nodes to another agent via incidara-console gateway API.

Provides the `delegate_to_agent` MCP tool for triage→repair delegation.
Uses CHAT_UI_URL, CHAT_UI_USER, CHAT_UI_PASSWORD env vars.

Includes auto-dedup: before creating a new task, checks if the target agent
already has an active (running / waiting_input / busy) task whose title
contains the hostname. If so, returns the existing task instead of creating
a duplicate.
"""
import json
import logging
import os
import re
import urllib.request
import urllib.parse
import http.cookiejar
import ssl

logger = logging.getLogger(__name__)

# Env vars — set in container .env / .claude.json
CHAT_UI_URL = os.environ.get("CHAT_UI_URL", "").rstrip("/")
CHAT_UI_USER = os.environ.get("CHAT_UI_USER", "")
CHAT_UI_PASSWORD = os.environ.get("CHAT_UI_PASSWORD", "")

# Hostname pattern — matches h200-XXXXXX, b300-XXXXXX, etc.
_HOSTNAME_RE = re.compile(r'(h200-\d+|b300-\d+|storage-\d+|cpu-\d+|ctrl-\d+|pxe-\d+|lg-cmc-[a-z0-9-]+-(?:h200|b300|storage|cpu|ctrl|pxe)-\d+)')


def _extract_hostname(text: str) -> str | None:
    """Extract a node hostname from a title or prompt string."""
    m = _HOSTNAME_RE.search(text)
    return m.group(1) if m else None


def _get_opener():
    """Build an opener that handles cookies. Works with both HTTP and HTTPS."""
    cj = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(cj)]
    # Add HTTPS handler with cert bypass if using HTTPS
    if CHAT_UI_URL.startswith("https"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers), cj


def _login(opener) -> bool:
    """Login to Chat UI. Returns True on success."""
    if not CHAT_UI_URL or not CHAT_UI_USER or not CHAT_UI_PASSWORD:
        raise RuntimeError(
            "Delegation requires CHAT_UI_URL, CHAT_UI_USER, CHAT_UI_PASSWORD env vars"
        )
    body = json.dumps({
        "email": CHAT_UI_USER,
        "password": CHAT_UI_PASSWORD,
    }).encode()
    req = urllib.request.Request(
        f"{CHAT_UI_URL}/api/auth/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = opener.open(req, timeout=15)
        result = json.loads(resp.read())
        logger.info("Chat UI login OK as %s", CHAT_UI_USER)
        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"Chat UI login failed: HTTP {e.code}: {body}")
    except Exception as e:
        raise RuntimeError(f"Chat UI login failed: {e}")


def _find_active_task_for_hostname(opener, agent_id: str, hostname: str) -> dict | None:
    """Query Chat UI for active tasks on the target agent that match the hostname.

    Checks tasks with status running, waiting_input, or busy whose title
    contains the hostname. Returns the first matching task dict, or None.
    """
    for status in ("running", "waiting_input", "busy", "interrupted"):
        url = (
            f"{CHAT_UI_URL}/api/tasks"
            f"?agent_id={urllib.parse.quote(agent_id)}"
            f"&status={urllib.parse.quote(status)}&limit=100"
        )
        req = urllib.request.Request(url, method="GET")
        try:
            resp = opener.open(req, timeout=15)
            data = json.loads(resp.read())
        except Exception as e:
            logger.warning("Failed to query tasks for dedup: %s", e)
            continue

        tasks = data if isinstance(data, list) else data.get("tasks", [])
        for task in tasks:
            task_title = task.get("title", "") or ""
            if hostname in task_title:
                logger.info(
                    "Found active %s task for %s: task_id=%s title=%s",
                    status, hostname, task.get("id"), task_title,
                )
                return task

    return None


def get_agent_active_tasks(agent_id: str) -> list[dict]:
    """Query Chat UI for all active tasks on the target agent.

    Returns a list of task dicts with id, title, status, session_id, etc.
    for tasks in running, waiting_input, busy, or interrupted state.
    """
    opener, cj = _get_opener()
    _login(opener)

    all_tasks = []
    for status in ("running", "waiting_input", "busy", "interrupted"):
        url = (
            f"{CHAT_UI_URL}/api/tasks"
            f"?agent_id={urllib.parse.quote(agent_id)}"
            f"&status={urllib.parse.quote(status)}&limit=200"
        )
        req = urllib.request.Request(url, method="GET")
        try:
            resp = opener.open(req, timeout=15)
            data = json.loads(resp.read())
        except Exception as e:
            logger.warning("Failed to query %s tasks for agent %s: %s", status, agent_id, e)
            continue

        tasks = data if isinstance(data, list) else data.get("tasks", [])
        all_tasks.extend(tasks)

    return all_tasks


def delegate_to_agent(agent_id: str, prompt: str, title: str = "", completion_mode: str = "manual", parent_task_id: int = 0) -> dict:
    """Delegate work to another agent via Chat UI.

    Auto-dedup: if a hostname is detected in the title/prompt and the target
    agent already has an active task (running / waiting_input / busy) whose
    title contains that hostname, returns the existing task instead of
    creating a duplicate.

    Flow:
      1. Login to Chat UI (session cookie)
      2. Extract hostname from title/prompt
      3. If hostname found, check for active task on target agent → dedup
      4. POST /api/agents/:agent_id/sessions with {prompt, title, completion_mode, parent_task_id}
         → creates session + task + sends prompt to gateway

    Args:
        agent_id: Target agent ID (e.g. "repair").
        prompt: The prompt/task description to send.
        title: Optional session title.
        completion_mode: "manual" (task stays waiting_input until user dismisses —
            default, recommended for hardware repair which requires user approval
            before destructive actions) or "auto" (task auto-completes when agent
            finishes — use only for read-only or non-destructive tasks like ticket
            drafting).

    Returns:
        dict with session and task info from Chat UI. If dedup triggered,
        includes "duplicate": True and the existing task info.
    """
    opener, cj = _get_opener()

    # Step 1: Login (get session cookie)
    _login(opener)

    # Step 2: Auto-dedup — check if target agent already has an active task for this hostname
    hostname = _extract_hostname(title or prompt)
    if hostname:
        existing = _find_active_task_for_hostname(opener, agent_id, hostname)
        if existing:
            logger.info(
                "Dedup: %s already has active task for %s (task_id=%s), skipping delegation",
                agent_id, hostname, existing.get("id"),
            )
            return {
                "duplicate": True,
                "existing_task": existing,
                "session": {"id": existing.get("session_id")},
                "task": existing,
                "message": (
                    f"Skipped: {agent_id} already has an active task "
                    f"(id={existing.get('id')}, status={existing.get('status')}) "
                    f"for {hostname}. Title: {existing.get('title')}"
                ),
            }

    # Step 3: Create session + task on target agent
    body = json.dumps({
        "prompt": prompt,
        "title": title or None,
        "completion_mode": completion_mode,
        "parent_task_id": parent_task_id or None,
    }).encode()
    req = urllib.request.Request(
        f"{CHAT_UI_URL}/api/agents/{agent_id}/sessions",
        data=body,
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        resp = opener.open(req, timeout=30)
        result = json.loads(resp.read())
        session_id = result.get("session", {}).get("id", "?")
        task_id = result.get("task", {}).get("id", "?")
        logger.info(
            "Delegated to %s: session=%s task=%s title=%s",
            agent_id, session_id, task_id, title,
        )
        return result
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"Delegation to {agent_id} failed: HTTP {e.code}: {resp_body}"
        )
    except Exception as e:
        raise RuntimeError(f"Delegation to {agent_id} failed: {e}")
