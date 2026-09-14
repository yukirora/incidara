#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
Simple standalone test script for PhysicalNodeOnboardClient.get_new_nodes()

This is a minimal version without external dependencies (no tabulate).
Can be run directly in a pod.

Required Environment Variables:
    POSTGRES_CONNECTION_STR: PostgreSQL connection string
    POSTGRES_SCHEMA: Schema name (default: ltp_sdk)
    CLUSTER_ID: Cluster/endpoint identifier (optional)

Usage:
    python test_physical_node_onboard_simple.py
"""

import os
import sys
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk"))

from ltp_postgresql_sdk.features.physical_node_onboard.client import (
    PhysicalNodeOnboardClient,
)


def print_separator(char="=", length=80):
    """Print a separator line."""
    print(char * length)


def print_header(text):
    """Print a formatted header."""
    print_separator()
    print(f" {text}")
    print_separator()


def format_timestamp(ts):
    """Format timestamp for display."""
    if ts is None:
        return "N/A"
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return str(ts)


def test_get_new_nodes(days=7):
    """Test the get_new_nodes() function."""
    print_header(f"Testing get_new_nodes(days={days})")

    # Check environment
    if not os.getenv("POSTGRES_CONNECTION_STR"):
        print("ERROR: POSTGRES_CONNECTION_STR environment variable not set!")
        return False

    print(f"\nEnvironment:")
    print(f"  POSTGRES_SCHEMA: {os.getenv('POSTGRES_SCHEMA', 'ltp_sdk')}")
    print(f"  CLUSTER_ID: {os.getenv('CLUSTER_ID', 'not set')}")
    print()

    try:
        # Create client
        print("Creating client...")
        client = PhysicalNodeOnboardClient(
            connection_str=os.getenv("POSTGRES_CONNECTION_STR"),
            schema=os.getenv("POSTGRES_SCHEMA", "ltp_sdk"),
        )
        print("✓ Client created\n")

        # Call get_new_nodes()
        print(f"Querying new nodes (last {days} days)...")
        results = client.get_new_nodes(days=days)
        print(f"✓ Query completed\n")

        # Display results
        print(f"Found {len(results)} new nodes\n")

        if not results:
            print("No new nodes found in the specified time range.")
            client.close()
            return True

        # Print each node
        print_separator("-")
        for i, node in enumerate(results, 1):
            print(f"\nNode {i}:")
            print(f"  Hostname:        {node.get('hostname', 'N/A')}")
            print(f"  Serial Number:   {node.get('sn', 'N/A')}")
            print(f"  SKU:             {node.get('sku', 'N/A')}")
            print(f"  Site:            {node.get('site', 'N/A')}")
            print(f"  Rack:            {node.get('rack', 'N/A')}")
            print(
                f"  Onboarded:       {format_timestamp(node.get('onboard_timestamp'))}"
            )
            print(f"  Current Status:  {node.get('current_status', 'N/A')}")
            print(
                f"  Status Time:     {format_timestamp(node.get('latest_status_timestamp'))}"
            )
            print(f"  Node Type:       {node.get('node_type', 'N/A')}")

        print_separator("-")

        # Summary statistics
        print(f"\nSummary:")
        skus = {}
        statuses = {}
        for node in results:
            sku = node.get("sku", "Unknown")
            skus[sku] = skus.get(sku, 0) + 1

            status = node.get("current_status", "Unknown")
            statuses[status] = statuses.get(status, 0) + 1

        print(f"\n  By SKU:")
        for sku, count in sorted(skus.items()):
            print(f"    {sku}: {count}")

        print(f"\n  By Current Status:")
        for status, count in sorted(statuses.items()):
            print(f"    {status}: {count}")

        client.close()
        print()
        print_header("✓ Test completed successfully!")
        return True

    except Exception as e:
        print(f"\n✗ ERROR: {str(e)}")
        import traceback

        traceback.print_exc()
        return False


def test_database_connection():
    """Test basic database connection."""
    print_header("Testing Database Connection")

    try:
        client = PhysicalNodeOnboardClient(
            connection_str=os.getenv("POSTGRES_CONNECTION_STR"),
            schema=os.getenv("POSTGRES_SCHEMA", "ltp_sdk"),
        )

        # Count total records
        results = client.execute_query(
            "SELECT COUNT(*) as total FROM ltp_sdk.physical_node_onboard_records"
        )
        total = results[0]["total"] if results else 0
        print(f"\nTotal onboard records: {total}")

        # Count recent records (last 30 days)
        results = client.execute_query(
            """
            SELECT COUNT(*) as total
            FROM ltp_sdk.physical_node_onboard_records
            WHERE timestamp >= NOW() - INTERVAL '30 days'
        """
        )
        recent = results[0]["total"] if results else 0
        print(f"Recent onboard records (last 30 days): {recent}")

        # Count nodes in node_status
        results = client.execute_query(
            "SELECT COUNT(DISTINCT hostname) as total FROM ltp_sdk.node_status"
        )
        status_count = results[0]["total"] if results else 0
        print(f"Unique hostnames in node_status: {status_count}")

        client.close()
        print()
        print_header("✓ Database connection test passed!")
        return True

    except Exception as e:
        print(f"\n✗ ERROR: {str(e)}")
        import traceback

        traceback.print_exc()
        return False


def test_get_node_id():
    """Test get_node_id_by_hostname and get_latest_node_id_by_hostname functions."""
    print_header("Testing get_node_id_by_hostname()")

    try:
        client = PhysicalNodeOnboardClient(
            connection_str=os.getenv("POSTGRES_CONNECTION_STR"),
            schema=os.getenv("POSTGRES_SCHEMA", "ltp_sdk"),
        )

        # Get a sample hostname from the database
        results = client.execute_query(
            """
            SELECT hostname, id, timestamp
            FROM ltp_sdk.physical_node_onboard_records
            ORDER BY timestamp DESC
            LIMIT 3
        """
        )

        if not results:
            print("\nNo onboard records found in database. Skipping test.")
            client.close()
            return True

        print(f"\nTesting with {len(results)} sample hostnames:\n")

        for i, record in enumerate(results, 1):
            hostname = record["hostname"]
            expected_id = str(record["id"])
            timestamp = record["timestamp"]

            print(f"Test {i}: hostname='{hostname}'")
            print(f"  Expected ID: {expected_id}")
            print(f"  Timestamp:   {format_timestamp(timestamp)}")

            # Test get_node_id_by_hostname with timestamp
            node_id_with_ts = client.get_node_id_by_hostname(hostname, timestamp)
            print(f"  get_node_id_by_hostname(timestamp): {node_id_with_ts}")

            # Test with current time (should return latest)
            node_id_current = client.get_node_id_by_hostname(
                hostname, datetime.utcnow()
            )
            print(f"  get_node_id_by_hostname(now):       {node_id_current}")

            if not node_id_with_ts:
                print(f"  ✗ FAILED: No node_id returned")
                return False

            print(f"  ✓ Success")
            print()

        client.close()
        print()
        print_header("✓ get_node_id tests passed!")
        return True

    except Exception as e:
        print(f"\n✗ ERROR: {str(e)}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = True

    # Test connection first
    if not test_database_connection():
        success = False

    print()

    # Test get_node_id functions
    if not test_get_node_id():
        success = False

    print()

    # Test get_new_nodes
    if not test_get_new_nodes(days=7):
        success = False

    # Exit with appropriate code
    sys.exit(0 if success else 1)
