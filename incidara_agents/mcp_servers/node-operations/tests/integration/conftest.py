"""
Integration test fixtures. These tests run inside the node-recycler pod
or any environment with access to the real DB and cluster nodes.

Required env vars:
  POSTGRES_CONNECTION_STR  - e.g. postgresql://root:<db-password>@<platform-db-host>:5432/openpai
  LTP_STORAGE_BACKEND_DEFAULT=postgresql
  POSTGRES_SCHEMA=ltp_sdk

Optional env vars (for SSH/BMC tests):
  SSH_USER       - default: operator
  SSH_TIMEOUT    - default: 30
"""
import os
import pytest


def _require_env(name):
    val = os.environ.get(name)
    if not val:
        pytest.skip(f"{name} not set")
    return val


@pytest.fixture
def db_clients():
    """Create real ltp_storage SDK clients."""
    _require_env("POSTGRES_CONNECTION_STR")
    from node_operations.db import create_clients
    sc, ac, pc = create_clients()
    return sc, ac, pc


@pytest.fixture
def ssh_env():
    """SSH credentials from env."""
    return {
        "user": os.environ.get("SSH_USER", "operator"),
        "timeout": int(os.environ.get("SSH_TIMEOUT", "30")),
    }


@pytest.fixture
def sample_available_node(db_clients):
    """Find one real available node to test against (read-only)."""
    sc, _, pc = db_clients
    from node_operations.db import get_nodes_by_status, get_node_detail
    nodes = get_nodes_by_status(sc, "available")
    if not nodes:
        pytest.skip("no available nodes in DB")
    hostname = nodes[0].HostName
    detail = get_node_detail(pc, hostname)
    if not detail:
        pytest.skip(f"no onboard record for {hostname}")
    return detail


@pytest.fixture
def sample_node_ip(sample_available_node):
    """Get the first IP of a real available node."""
    ip_list = sample_available_node.get("ip", [])
    if not ip_list:
        pytest.skip("node has no IP")
    return ip_list[0] if isinstance(ip_list, list) else ip_list
