"""Cron sync daemon — watches collectors table and keeps crontab in sync.

Each enabled collector gets a crontab entry that runs run_collector_job.py
with flock to prevent overlapping runs. When the agent creates/modifies/disables
a collector via MCP tools, this daemon detects the change and updates crontab.

Usage:
    python -m patrol_cron.cron_sync          # run once
    python -m patrol_cron.cron_sync --watch  # poll every SYNC_INTERVAL

Env vars:
    EVIDENCE_DB_URL        - PostgreSQL connection string
    PATROL_SYNC_INTERVAL   - How often to poll DB for changes (default 30s)
    PATROL_LOCK_DIR        - Directory for flock files (default /tmp/patrol_cron)
    PATROL_LOG_DIR         - Directory for collector logs (default /tmp/patrol_cron/logs)
    PATROL_PYTHON          - Python interpreter path (default python3)
"""

from __future__ import annotations

import logging
import math
import os
import signal
import subprocess
import sys
import time

from patrol_cron import db

logger = logging.getLogger("patrol_cron.sync")

SYNC_INTERVAL = int(os.environ.get("PATROL_SYNC_INTERVAL", "30"))
LOCK_DIR = os.environ.get("PATROL_LOCK_DIR", "/tmp/patrol_cron")
LOG_DIR = os.environ.get("PATROL_LOG_DIR", "/tmp/patrol_cron/logs")
PYTHON = os.environ.get("PATROL_PYTHON", sys.executable)  # full path, safe for cron

# Path to run_collector_job.py — resolved relative to this file
_JOB_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_collector_job.py")

# Marker comments so we only touch our own crontab entries
_CRON_HEADER = "# --- patrol_cron managed (do not edit) ---"
_CRON_FOOTER = "# --- end patrol_cron ---"


def _secs_to_cron_schedule(secs: int) -> str:
    """Convert seconds interval to a cron schedule expression.

    Cron minimum granularity is 1 minute. Sub-minute rounds up to every minute.
    """
    minutes = max(1, secs // 60)

    if minutes == 1:
        return "* * * * *"
    elif minutes < 60:
        return f"*/{minutes} * * * *"
    elif minutes == 60:
        return "0 * * * *"
    elif minutes < 1440:
        hours = minutes // 60
        return f"0 */{hours} * * *"
    else:
        return "0 0 * * *"  # daily


def _collector_to_cron_line(coll: dict) -> str | None:
    """Build a crontab line for one collector. Returns None if disabled."""
    if not coll.get("enabled", True):
        return None

    name = coll["name"]
    schedule = _secs_to_cron_schedule(coll["schedule_sec"])
    lock_file = os.path.join(LOCK_DIR, f"{name}.lock")
    log_file = os.path.join(LOG_DIR, f"{name}.log")

    # flock -n: non-blocking, skip if already running
    return (
        f"{schedule} flock -n {lock_file} "
        f"{PYTHON} {_JOB_SCRIPT} --name {name} "
        f">> {log_file} 2>&1"
    )


def build_crontab(collectors: list[dict]) -> str:
    """Build the full patrol_cron crontab block from collector configs."""
    # Cron doesn't inherit container env — forward all env vars
    pythonpath = os.environ.get("PYTHONPATH", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    skip_keys = {"HOME", "HOSTNAME", "TERM", "SHLVL", "_", "PWD", "OLDPWD"}
    lines = [
        _CRON_HEADER,
        f"PYTHONPATH={pythonpath}",
        f"PATH=/usr/local/bin:/usr/bin:/bin",
    ]
    for key, val in sorted(os.environ.items()):
        if key in skip_keys or key.startswith("FASTMCP_"):
            continue
        # Skip empty values — cron treats next line as schedule
        if not val:
            continue
        # Cron env lines can't have newlines or special chars in values
        if "\n" not in val:
            lines.append(f"{key}={val}")
    for coll in collectors:
        line = _collector_to_cron_line(coll)
        if line:
            lines.append(line)
    # Daily log rotation at midnight: rename current → dated, delete >7 days
    rotate_cmd = (
        f"cd {LOG_DIR} && "
        "for f in *.log; do "
        f"  [ -f \"$f\" ] && mv \"$f\" \"$f-$(date +\\%Y-\\%m-\\%d)\"; "
        "done && "
        "find . -name '*.log-2*' -mtime +7 -delete"
    )
    lines.append(f"0 0 * * * {rotate_cmd}")
    lines.append(_CRON_FOOTER)
    return "\n".join(lines) + "\n"


def _read_crontab() -> str:
    """Read current user crontab."""
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=10,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def _write_crontab(new_content: str):
    """Write the full crontab content."""
    proc = subprocess.run(
        ["crontab", "-"], input=new_content, capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"crontab write failed: {proc.stderr}")


def _replace_patrol_block(existing: str, new_block: str) -> str:
    """Replace just the patrol_cron block in the crontab, preserving other entries."""
    lines = existing.split("\n")
    before = []
    after = []
    in_block = False
    past_block = False

    for line in lines:
        if line.strip() == _CRON_HEADER.strip():
            in_block = True
            continue
        if line.strip() == _CRON_FOOTER.strip():
            in_block = False
            past_block = True
            continue
        if in_block:
            continue
        if past_block:
            after.append(line)
        else:
            before.append(line)

    parts = []
    before_text = "\n".join(before).rstrip()
    if before_text:
        parts.append(before_text)
    parts.append(new_block.rstrip())
    after_text = "\n".join(after).rstrip()
    if after_text:
        parts.append(after_text)

    return "\n".join(parts) + "\n"


def sync_crontab() -> bool:
    """Sync DB collectors → crontab. Returns True if crontab was changed."""
    collectors = db.get_enabled_collectors()
    new_block = build_crontab(collectors)

    existing = _read_crontab()

    # Extract current patrol block for comparison
    current_block = ""
    in_block = False
    for line in existing.split("\n"):
        if line.strip() == _CRON_HEADER.strip():
            in_block = True
        if in_block:
            current_block += line + "\n"
        if line.strip() == _CRON_FOOTER.strip():
            in_block = False

    if current_block.strip() == new_block.strip():
        logger.debug("Crontab unchanged, skipping write")
        return False

    # Ensure lock/log dirs exist
    os.makedirs(LOCK_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    full_crontab = _replace_patrol_block(existing, new_block)
    _write_crontab(full_crontab)
    logger.info(f"Crontab updated: {len(collectors)} collectors")
    return True


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    watch = "--watch" in sys.argv

    if not watch:
        sync_crontab()
        return

    _shutdown = False

    def _handle_signal(signum, frame):
        nonlocal _shutdown
        _shutdown = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info(f"patrol_cron sync daemon starting (interval={SYNC_INTERVAL}s)")

    while not _shutdown:
        try:
            changed = sync_crontab()
            if changed:
                logger.info("Crontab synced")
        except Exception as e:
            logger.error(f"Sync error: {e}", exc_info=True)

        for _ in range(SYNC_INTERVAL):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("patrol_cron sync daemon stopped")


if __name__ == "__main__":
    main()
