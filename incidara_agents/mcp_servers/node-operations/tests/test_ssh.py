import pexpect
import subprocess
from unittest.mock import patch, MagicMock
from node_operations.ssh import probe_ssh, run_remote_command, run_remote_command_capture


def test_probe_ssh_key_auth_true():
    """Key/agent auth: subprocess returns 0 → reachable."""
    with patch("node_operations.ssh.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0)
        assert probe_ssh("10.0.0.1", "u", timeout=10) is True


def test_probe_ssh_key_auth_false():
    """Key/agent auth: subprocess returns 255 → unreachable."""
    with patch("node_operations.ssh.subprocess.run") as m:
        m.return_value = MagicMock(returncode=255)
        assert probe_ssh("10.0.0.1", "u", timeout=10) is False


def test_probe_ssh_key_auth_timeout():
    """Key/agent auth: subprocess timeout → unreachable."""
    with patch("node_operations.ssh.subprocess.run") as m:
        m.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=10)
        assert probe_ssh("10.0.0.1", "u", timeout=10) is False


def test_probe_ssh_password_auth_true():
    """Password auth: pexpect exits 0 → reachable."""
    with patch("node_operations.ssh.pexpect.spawn") as mock_spawn:
        child = MagicMock()
        # First expect returns index 1 (EOF in pattern list) → break loop
        child.expect.return_value = 1
        child.exitstatus = 0
        child.close = MagicMock()
        mock_spawn.return_value = child
        assert probe_ssh("10.0.0.1", "u", timeout=10, password="p") is True


def test_probe_ssh_password_auth_false():
    """Password auth: pexpect exits non-zero → unreachable."""
    with patch("node_operations.ssh.pexpect.spawn") as mock_spawn:
        child = MagicMock()
        child.expect.return_value = 1  # EOF
        child.exitstatus = 255
        child.close = MagicMock()
        mock_spawn.return_value = child
        assert probe_ssh("10.0.0.1", "u", timeout=10, password="p") is False


def test_run_remote_command_returns_rc():
    """run_remote_command returns the exit code (uses subprocess, not pexpect)."""
    with patch("node_operations.ssh.subprocess.run") as m:
        m.return_value = MagicMock(returncode=42)
        assert run_remote_command("10.0.0.1", "u", "echo hi", timeout=10) == 42


def test_run_remote_command_timeout():
    """run_remote_command returns -1 on timeout."""
    with patch("node_operations.ssh.subprocess.run") as m:
        m.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=10)
        assert run_remote_command("10.0.0.1", "u", "echo hi", timeout=10) == -1


def test_run_remote_command_capture():
    """run_remote_command_capture returns (rc, output) tuple."""
    with patch("node_operations.ssh.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0, stdout="hello\n")
        rc, out = run_remote_command_capture("10.0.0.1", "u", "echo hi", timeout=10)
        assert rc == 0
        assert out == "hello\n"
