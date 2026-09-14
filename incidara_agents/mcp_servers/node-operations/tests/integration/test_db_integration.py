"""Integration tests for db.py against real PostgreSQL."""
import pytest
from node_operations.db import (
    create_clients,
    get_nodes_by_status,
    get_node_detail,
    get_node_history,
    get_latest_action_by_state,
    get_ticket_id_for_node,
    is_ticket_completed,
)


class TestDbReads:
    """Read-only DB tests. Safe to run anytime."""

    def test_create_clients(self, db_clients):
        sc, ac, pc = db_clients
        assert sc is not None
        assert ac is not None
        assert pc is not None

    def test_get_nodes_by_status_available(self, db_clients):
        sc, _, _ = db_clients
        nodes = get_nodes_by_status(sc, "available")
        assert isinstance(nodes, list)
        print(f"Found {len(nodes)} available nodes")
        if nodes:
            n = nodes[0]
            print(f"  first: {n.HostName}")
            assert hasattr(n, "HostName")

    def test_get_nodes_by_status_all_statuses(self, db_clients):
        sc, _, _ = db_clients
        for status in ["available", "triaged_unknown", "triaged_hardware",
                        "triaged_platform", "ua", "ready_ua", "allocated_ua",
                        "deallocated_ua", "cordoned", "validating"]:
            nodes = get_nodes_by_status(sc, status)
            count = len(nodes) if isinstance(nodes, list) else 0
            print(f"  {status}: {count} nodes")

    def test_get_node_detail(self, db_clients, sample_available_node):
        detail = sample_available_node
        assert detail is not None
        assert "hostname" in detail
        assert "ip" in detail
        assert "sn" in detail
        print(f"  hostname={detail['hostname']} sn={detail['sn']} category={detail.get('category')}")
        print(f"  ip={detail['ip']} mgmt_ip={detail.get('mgmt_ip')}")

    def test_get_node_history(self, db_clients, sample_available_node):
        _, ac, _ = db_clients
        hostname = sample_available_node["hostname"]
        history = get_node_history(ac, hostname)
        assert isinstance(history, list)
        print(f"  {hostname} has {len(history)} action records")
        if history:
            latest = history[-1] if isinstance(history[-1], dict) else history[-1]
            print(f"  latest action: {latest}")

    def test_get_node_detail_nonexistent(self, db_clients):
        _, _, pc = db_clients
        detail = get_node_detail(pc, "nonexistent-node-12345")
        assert detail is None

    def test_is_ticket_completed(self):
        assert is_ticket_completed("已完成") is True
        assert is_ticket_completed("已撤销") is True
        assert is_ticket_completed("已撤回") is True
        assert is_ticket_completed("已拒绝") is True
        assert is_ticket_completed("维修中") is False
        assert is_ticket_completed("") is False
