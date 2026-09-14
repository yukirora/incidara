"""Integration tests for ssh.py against real nodes."""
import pytest
from node_operations.ssh import probe_ssh, run_remote_command, run_remote_command_capture


class TestSshProbe:
    """SSH probe tests against real nodes. Read-only, safe."""

    def test_probe_ssh_reachable_node(self, ssh_env, sample_node_ip):
        result = probe_ssh(sample_node_ip, ssh_env["user"], ssh_env["key"], ssh_env["timeout"])
        print(f"  probe_ssh({sample_node_ip}) = {result}")
        assert result is True

    def test_probe_ssh_unreachable_ip(self, ssh_env):
        result = probe_ssh("192.0.2.1", ssh_env["user"], ssh_env["key"], timeout=5)
        assert result is False

    def test_run_remote_command_hostname(self, ssh_env, sample_node_ip):
        rc = run_remote_command(sample_node_ip, ssh_env["user"], ssh_env["key"], "hostname", ssh_env["timeout"])
        print(f"  run_remote_command hostname rc={rc}")
        assert rc == 0

    def test_run_remote_command_capture_hostname(self, ssh_env, sample_node_ip):
        rc, output = run_remote_command_capture(
            sample_node_ip, ssh_env["user"], ssh_env["key"], "hostname", ssh_env["timeout"]
        )
        print(f"  hostname={output.strip()} rc={rc}")
        assert rc == 0
        assert len(output.strip()) > 0

    def test_run_remote_command_capture_uname(self, ssh_env, sample_node_ip):
        rc, output = run_remote_command_capture(
            sample_node_ip, ssh_env["user"], ssh_env["key"], "uname -a", ssh_env["timeout"]
        )
        print(f"  uname={output.strip()[:80]}")
        assert rc == 0
        assert "Linux" in output
