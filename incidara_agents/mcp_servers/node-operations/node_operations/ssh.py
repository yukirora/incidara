import re
import shlex
import subprocess
import logging
import pexpect
import json
import os
from pathlib import Path

logger = logging.getLogger(__name__)
_SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]


# ── Mock SSH support ──────────────────────────────────────────────
# When NODE_OPS_SSH_MOCK_FILE is set, probe_ssh and run_ssh_command
# return mock data from the JSON file instead of making real SSH calls.
# This allows job-triage tests to seed realistic SSH responses.

_MOCK_CACHE: dict | None = None

def _load_mocks() -> dict | None:
    global _MOCK_CACHE
    path = os.environ.get("NODE_OPS_SSH_MOCK_FILE")
    if not path:
        return None
    if _MOCK_CACHE is not None and _MOCK_CACHE.get("__path__") == path:
        return _MOCK_CACHE
    try:
        with open(path) as f:
            data = json.load(f)
        data["__path__"] = path
        _MOCK_CACHE = data
        return data
    except Exception as e:
        logger.warning("Failed to load SSH mock file %s: %s", path, e)
        return None


def _mock_probe(ip: str) -> bool | None:
    mocks = _load_mocks()
    if mocks is None:
        return None
    for entry in mocks.get("probe_ssh", []):
        if entry.get("ip") == ip or entry.get("ip") == "*":
            return entry.get("reachable", True)
    return None  # no mock for this IP → fall through to real SSH


def _mock_run(ip: str, command: str) -> dict | None:
    mocks = _load_mocks()
    if mocks is None:
        return None
    for entry in mocks.get("run_ssh_command", []):
        if entry.get("ip") != ip and entry.get("ip") != "*":
            continue
        # Match by command substring or regex
        pattern = entry.get("command_match", "")
        if pattern == "*" or pattern in command:
            return {"exit_code": entry.get("exit_code", 0),
                    "stdout": entry.get("stdout", ""),
                    "error": entry.get("error")}
        import re as _re
        try:
            if _re.search(pattern, command):
                return {"exit_code": entry.get("exit_code", 0),
                        "stdout": entry.get("stdout", ""),
                        "error": entry.get("error")}
        except _re.error:
            pass
    return None  # no mock → fall through to real SSH


def _ssh_base_cmd(user: str, ip: str, timeout: int) -> list[str]:
    """Build base SSH command. No -i flag — rely on SSH agent (SSH_AUTH_SOCK)
    for key auth, pexpect password fallback when agent doesn't work."""
    cmd = ["ssh", *_SSH_OPTS, "-o", f"ConnectTimeout={timeout}"]
    cmd.append(f"{user}@{ip}")
    return cmd


def probe_ssh(ip: str, user: str, timeout: int = 300, password: str | None = None) -> bool:
    """Check if a node is SSH-reachable.

    By default uses key/agent auth (subprocess). If password is provided,
    falls back to pexpect-based password auth.
    """
    # Mock check
    mock_result = _mock_probe(ip)
    if mock_result is not None:
        logger.info("Mock probe_ssh(%s) → %s", ip, mock_result)
        return mock_result

    if not password:
        # Key/agent auth — fast, no pexpect
        cmd = _ssh_base_cmd(user, ip, timeout) + ["true"]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, check=False, timeout=timeout)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    # Password auth via pexpect
    cmd = _ssh_base_cmd(user, ip, timeout) + ["true"]
    child = pexpect.spawn(" ".join(shlex.quote(x) for x in cmd),
                          timeout=timeout, encoding="utf-8")
    try:
        while True:
            idx = child.expect([r"[Pp]assword:", pexpect.EOF, pexpect.TIMEOUT])
            if idx == 0:
                child.sendline(password)
            elif idx == 1:
                break
            else:
                return False
    except Exception:
        return False
    finally:
        child.close()
    return child.exitstatus == 0


def run_remote_command(ip: str, user: str, command: str, timeout: int = 30) -> int:
    cmd = _ssh_base_cmd(user, ip, timeout) + [command]
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        return -1


def run_remote_command_capture(ip: str, user: str, command: str, timeout: int = 30) -> tuple[int, str]:
    cmd = _ssh_base_cmd(user, ip, timeout) + [command]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout)
        return r.returncode, r.stdout
    except subprocess.TimeoutExpired:
        return -1, f"SSH command timed out after {timeout}s"


def run_ssh_command(ip: str, user: str, command: str,
                    timeout: int = 60, sudo: bool = False) -> dict:
    """Execute an SSH command on a remote node and return structured result."""
    # Mock check
    mock_result = _mock_run(ip, command)
    if mock_result is not None:
        logger.info("Mock run_ssh_command(%s, %s) → mock data", ip, command[:80])
        return mock_result

    if sudo:
        command = f"sudo {command}"
    cmd = _ssh_base_cmd(user, ip, timeout) + [command]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, check=False, timeout=timeout + 10)
        error = r.stderr.strip() if r.stderr and r.stderr.strip() else None
        return {"exit_code": r.returncode, "stdout": r.stdout, "error": error}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "error": f"SSH command timed out after {timeout}s"}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "error": str(e)}


def run_kubectl_via_ssh(host_ip: str, user: str,
                        command: str, timeout: int = 60) -> dict:
    """Execute a kubectl command on a remote LTP host via SSH."""
    cmd_stripped = command.strip()
    if not cmd_stripped:
        return {"exit_code": 1, "stdout": "", "error": "Empty kubectl command"}
    if cmd_stripped.startswith("kubectl "):
        cmd_stripped = cmd_stripped[len("kubectl "):]
    full_cmd = f"kubectl {cmd_stripped}"
    cmd = _ssh_base_cmd(user, host_ip, timeout) + [full_cmd]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, check=False, timeout=timeout + 10)
        error = r.stderr.strip() if r.stderr and r.stderr.strip() else None
        return {"exit_code": r.returncode, "stdout": r.stdout, "error": error}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "error": f"kubectl command timed out after {timeout}s"}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "error": str(e)}


def scp_upload(ip: str, user: str, password: str | None,
               local_path: str, remote_path: str, timeout: int) -> None:
    """SCP upload. Uses pexpect to handle password prompts if agent auth fails."""
    scp_cmd = ["scp", *_SSH_OPTS, "-o", f"ConnectTimeout={timeout}", "-r"]
    scp_cmd += [local_path, f"{user}@{ip}:{remote_path}"]
    child = pexpect.spawn(" ".join(shlex.quote(x) for x in scp_cmd), timeout=timeout, encoding="utf-8")
    try:
        while True:
            idx = child.expect([r"[Pp]assword:", pexpect.EOF, pexpect.TIMEOUT])
            if idx == 0:
                if not password:
                    raise RuntimeError("SCP asks for password but none provided")
                child.sendline(password)
            elif idx == 1:
                break
            else:
                raise RuntimeError("SCP timed out")
    finally:
        child.close()
    if child.exitstatus != 0:
        raise RuntimeError(f"scp to {ip}:{remote_path} failed (exit {child.exitstatus})")


def ssh_with_password(ip: str, user: str, password: str | None,
                      command: str, timeout: int) -> tuple[int, str]:
    """SSH and execute command. Uses pexpect to handle agent auth, password prompts,
    and sudo password prompts."""
    ssh_cmd = ["ssh", *_SSH_OPTS, "-o", f"ConnectTimeout={timeout}"]
    ssh_cmd += [f"{user}@{ip}", command]
    child = pexpect.spawn(" ".join(shlex.quote(x) for x in ssh_cmd), timeout=timeout, encoding="utf-8")
    parts = []
    try:
        while True:
            idx = child.expect([
                rf"\[sudo\] password for {re.escape(user)}:", r"[Pp]assword:",
                pexpect.EOF, pexpect.TIMEOUT,
            ])
            if idx in (0, 1):
                if not password:
                    raise RuntimeError("password required")
                child.sendline(password)
            elif idx == 2:
                parts.append(child.before or "")
                break
            else:
                raise RuntimeError("SSH timed out")
    finally:
        child.close()
    rc = child.exitstatus if child.exitstatus is not None else child.signalstatus
    return rc, "".join(parts)


def scp_download(ip: str, user: str, password: str | None,
                 remote_path: str, local_path: str, timeout: int) -> None:
    """SCP download (remote → local). Uses pexpect to handle password prompts."""
    scp_cmd = ["scp", *_SSH_OPTS, "-o", f"ConnectTimeout={timeout}", "-r"]
    scp_cmd += [f"{user}@{ip}:{remote_path}", local_path]
    child = pexpect.spawn(" ".join(shlex.quote(x) for x in scp_cmd), timeout=timeout, encoding="utf-8")
    try:
        while True:
            idx = child.expect([r"[Pp]assword:", pexpect.EOF, pexpect.TIMEOUT])
            if idx == 0:
                if not password:
                    raise RuntimeError("SCP asks for password but none provided")
                child.sendline(password)
            elif idx == 1:
                break
            else:
                raise RuntimeError("SCP timed out")
    finally:
        child.close()
    if child.exitstatus != 0:
        raise RuntimeError(f"scp from {ip}:{remote_path} failed (exit {child.exitstatus})")
