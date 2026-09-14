"""Integration test for the physical pipeline flow.

Tests the node-recycler's physical pipeline logic WITHOUT actually
resetting or configuring nodes. Validates that:
1. DB queries work for each pipeline stage
2. Status transitions are correct
3. The pipeline correctly identifies nodes to process

Run with: python -m pytest tests/integration/test_pipeline_integration.py -v -s
"""
import pytest
from node_operations.db import (
    create_clients,
    get_nodes_by_status,
    get_node_detail,
    get_node_history,
    get_ticket_id_for_node,
    is_ticket_completed,
    insert_status_transition,
)


class TestTicketCheckPipeline:
    """Test the ua -> ready_ua pipeline logic (read-only parts)."""

    def test_find_ua_nodes(self, db_clients):
        sc, _, _ = db_clients
        nodes = get_nodes_by_status(sc, "ua")
        print(f"  ua nodes: {len(nodes)}")
        for n in nodes[:5]:
            print(f"    {n.HostName}")

    def test_find_ticket_for_ua_node(self, db_clients):
        sc, _, pc = db_clients
        nodes = get_nodes_by_status(sc, "ua")
        if not nodes:
            pytest.skip("no ua nodes")
        hostname = nodes[0].HostName
        ticket_id, onboard_id = get_ticket_id_for_node(pc, hostname)
        print(f"  {hostname}: ticket_id={ticket_id} onboard_id={onboard_id}")


class TestReallocationPipeline:
    """Test the ready_ua -> allocated_ua pipeline logic (read-only parts)."""

    def test_find_ready_ua_nodes(self, db_clients):
        sc, _, _ = db_clients
        nodes = get_nodes_by_status(sc, "ready_ua")
        print(f"  ready_ua nodes: {len(nodes)}")
        for n in nodes[:5]:
            print(f"    {n.HostName}")

    def test_get_detail_for_ready_ua_node(self, db_clients):
        sc, _, pc = db_clients
        nodes = get_nodes_by_status(sc, "ready_ua")
        if not nodes:
            pytest.skip("no ready_ua nodes")
        hostname = nodes[0].HostName
        detail = get_node_detail(pc, hostname)
        assert detail is not None
        print(f"  {hostname}: category={detail.get('category')} ip={detail.get('ip')} sn={detail.get('sn')}")


class TestStatusTransitionRoundtrip:
    """Test writing and reading a status transition on a REAL node.

    WARNING: This test WRITES to the DB. It moves a node to triaged_unknown
    and back. Only run this on a designated test node.

    Set TEST_NODE_HOSTNAME and TEST_NODE_ID env vars to enable.
    """

    @pytest.fixture
    def test_node(self):
        import os
        hostname = os.environ.get("TEST_NODE_HOSTNAME")
        node_id = os.environ.get("TEST_NODE_ID")
        if not hostname or not node_id:
            pytest.skip("TEST_NODE_HOSTNAME and TEST_NODE_ID not set")
        return hostname, node_id

    def test_roundtrip_transition(self, db_clients, test_node):
        """Move test node: available -> triaged_unknown -> available."""
        sc, ac, _ = db_clients
        hostname, node_id = test_node

        # Step 1: Move to triaged_unknown
        insert_status_transition(
            status_client=sc, action_client=ac,
            hostname=hostname, node_id=node_id,
            from_status="available", to_status="triaged_unknown",
            reason="integration_test", detail='{"test": true}',
            category="platform",
        )
        print(f"  moved {hostname} to triaged_unknown")

        # Step 2: Verify it shows up
        nodes = get_nodes_by_status(sc, "triaged_unknown")
        found = any(n.HostName == hostname for n in nodes)
        print(f"  found in triaged_unknown: {found}")

        # Step 3: Move back to available
        insert_status_transition(
            status_client=sc, action_client=ac,
            hostname=hostname, node_id=node_id,
            from_status="triaged_unknown", to_status="available",
            reason="integration_test_cleanup", detail='{"test": true}',
            category="platform",
        )
        print(f"  moved {hostname} back to available")

        # Step 4: Verify it's back
        nodes = get_nodes_by_status(sc, "available")
        found = any(n.HostName == hostname for n in nodes)
        assert found, f"{hostname} not found in available after roundtrip"
        print(f"  roundtrip OK")
