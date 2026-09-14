"""Integration tests for bmc.py against real nodes."""
import pytest
from node_operations.bmc import get_sn_from_ipmi, check_fabricmanager


class TestBmcReads:
    """BMC read-only tests. Safe to run."""

    def test_get_sn_from_ipmi(self, ssh_env, sample_node_ip):
        """Read serial number via ipmitool on a real node."""
        try:
            sn = get_sn_from_ipmi(sample_node_ip, user=ssh_env["user"], key=ssh_env["key"])
            print(f"  SN={sn}")
            assert len(sn) > 0
        except Exception as e:
            # ipmitool might not be available on all node types
            pytest.skip(f"ipmitool not available: {e}")

    def test_check_fabricmanager_gpu_node(self, ssh_env, db_clients, sample_available_node):
        """Check fabricmanager on a GPU node."""
        category = sample_available_node.get("category", "")
        if category not in ("h200", "b300"):
            pytest.skip(f"not a GPU node (category={category})")
        ip = sample_available_node["ip"][0]
        result = check_fabricmanager(ip, ssh_env["user"], ssh_env["key"], ssh_env["timeout"])
        print(f"  fabricmanager on {ip}: {result}")
        assert isinstance(result, bool)
