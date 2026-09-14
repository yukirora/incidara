"""Switch SSH operations — interactive login for IB and Ruijie switches.

Switches require interactive SSH (password prompt → shell → paging disable → command → read).
One-shot SSH does NOT work — switches reject direct command execution.
"""

from __future__ import annotations

import os
import re
import shlex

import pexpect

_SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]


def _drain_until_quiet(child, idle_sec=1.0, max_wait=30):
    """Read from pexpect child until output stops for idle_sec."""
    import time
    buf = ""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            idx = child.expect([r".+", pexpect.TIMEOUT, pexpect.EOF],
                               timeout=idle_sec)
            if idx == 0:
                buf += child.after
            elif idx == 1:
                break
            else:
                break
        except Exception:
            break
    # Strip ANSI escape sequences
    buf = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', buf)
    buf = buf.replace('\r', '')
    return buf.strip()


def _detect_paging_cmd(ip: str) -> str:
    """Return vendor-specific paging disable command based on switch type.

    IB (Mellanox): terminal length 0
    Ruijie: no cli session paging enable
    We try both — the switch will just ignore the one it doesn't understand.
    """
    return "terminal length 0"


def run_switch_command(ip: str, command: str, timeout: int = 30) -> dict:
    """Execute a command on a network switch via interactive SSH.

    Flow: connect → password login → disable paging → run command → read output.

    Uses SWITCH_SSH_USER and SWITCH_SSH_PASSWORD from environment.

    Returns: {"exit_code": int, "stdout": str, "error": str|None}
    """
    user = os.environ.get("SWITCH_SSH_USER", "admin")
    password = os.environ.get("SWITCH_SSH_PASSWORD", "")
    if not password:
        return {"exit_code": -1, "stdout": "", "error": "SWITCH_SSH_PASSWORD not set"}

    cmd = ["ssh", *_SSH_OPTS, "-o", f"ConnectTimeout={timeout}",
           f"{user}@{ip}"]
    child = pexpect.spawn(
        " ".join(shlex.quote(x) for x in cmd),
        timeout=timeout, encoding="utf-8", codec_errors="replace"
    )
    try:
        # Wait for password prompt
        idx = child.expect(
            [r"[Pp]assword:", r"[Pp]ermission denied", pexpect.EOF, pexpect.TIMEOUT],
            timeout=timeout,
        )
        if idx == 1:
            return {"exit_code": 1, "stdout": "", "error": "Permission denied"}
        if idx == 2:
            return {"exit_code": 1, "stdout": "", "error": "Connection closed before login"}
        if idx == 3:
            return {"exit_code": -1, "stdout": "", "error": f"SSH timed out after {timeout}s"}

        child.sendline(password)

        # Wait for shell prompt to settle after login
        _drain_until_quiet(child, idle_sec=1.5, max_wait=timeout)

        # Disable paging — try both vendor commands
        child.sendline("terminal length 0")
        _drain_until_quiet(child, idle_sec=0.5, max_wait=5)
        child.sendline("no cli session paging enable")
        _drain_until_quiet(child, idle_sec=0.5, max_wait=5)

        # Send the actual command
        child.sendline(command)
        output = _drain_until_quiet(child, idle_sec=2.0, max_wait=timeout)

        # Exit cleanly
        try:
            child.sendline("exit")
        except Exception:
            pass

        return {"exit_code": 0, "stdout": output, "error": None}

    except pexpect.TIMEOUT:
        return {"exit_code": -1, "stdout": "", "error": f"Command timed out after {timeout}s"}
    except pexpect.EOF:
        output = child.before or ""
        return {"exit_code": 0, "stdout": output.strip(), "error": None}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "error": str(e)}
    finally:
        child.close(force=True)


def get_switch_inventory(switch_type: str | None = None) -> list[dict]:
    """Get switch inventory from evidence DB.

    Args:
        switch_type: Filter by type ('ib', 'ruijie', 'ufm'). None = all.
    """
    import psycopg2
    import psycopg2.extras

    db_url = os.environ.get("EVIDENCE_DB_URL", "")
    if not db_url:
        return []

    with psycopg2.connect(db_url) as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if switch_type:
            cur.execute("SELECT * FROM switch_inventory WHERE type = %s ORDER BY hostname",
                        (switch_type,))
        else:
            cur.execute("SELECT * FROM switch_inventory ORDER BY type, hostname")
        return [dict(r) for r in cur.fetchall()]
