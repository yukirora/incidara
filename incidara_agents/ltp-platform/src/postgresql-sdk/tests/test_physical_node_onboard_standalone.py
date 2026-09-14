#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
Standalone test script for PhysicalNodeOnboardClient.get_new_nodes()

This script can be run directly in a pod to verify the get_new_nodes() function.

Required Environment Variables:
    POSTGRES_CONNECTION_STR: PostgreSQL connection string
    POSTGRES_SCHEMA: Schema name (default: ltp_sdk)
    CLUSTER_ID: Cluster/endpoint identifier (optional)

Usage:
    python test_physical_node_onboard_standalone.py [--days 7]
"""

import os
import sys
import argparse
import logging
from datetime import datetime
from tabulate import tabulate

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk"))

from ltp_postgresql_sdk.features.physical_node_onboard.client import (
    PhysicalNodeOnboardClient,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def check_env_vars():
    """Check required environment variables."""
    required_vars = ["POSTGRES_CONNECTION_STR"]
    missing = [var for var in required_vars if not os.getenv(var)]

    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        return False

    logger.info("Environment variables:")
    logger.info(f"  POSTGRES_CONNECTION_STR: {'*' * 20} (masked)")
    logger.info(f"  POSTGRES_SCHEMA: {os.getenv('POSTGRES_SCHEMA', 'ltp_sdk')}")
    logger.info(f"  CLUSTER_ID: {os.getenv('CLUSTER_ID', 'not set')}")

    return True


def test_get_new_nodes(days=7):
    """
    Test the get_new_nodes() function.

    Args:
        days: Number of days to look back for recent onboards
    """
    logger.info(f"\n{'='*80}")
    logger.info(f"Testing PhysicalNodeOnboardClient.get_new_nodes(days={days})")
    logger.info(f"{'='*80}\n")

    try:
        # Create client
        logger.info("Creating PhysicalNodeOnboardClient...")
        client = PhysicalNodeOnboardClient(
            connection_str=os.getenv("POSTGRES_CONNECTION_STR"),
            schema=os.getenv("POSTGRES_SCHEMA", "ltp_sdk"),
        )
        logger.info("Client created successfully.\n")

        # Test get_new_nodes()
        logger.info(f"Calling get_new_nodes(days={days})...")
        results = client.get_new_nodes(days=days)
        logger.info(f"Query completed. Found {len(results)} new nodes.\n")

        if not results:
            logger.info("No new nodes found in the specified time range.")
            return True

        # Display results in table format
        logger.info(f"New Nodes (last {days} days):")
        logger.info(f"{'-'*80}\n")

        # Prepare table data
        headers = [
            "Hostname",
            "SKU",
            "Site/Rack",
            "Onboarded",
            "Current Status",
            "Status Time",
        ]

        table_data = []
        for node in results:
            onboard_time = node.get("onboard_timestamp")
            if isinstance(onboard_time, datetime):
                onboard_time = onboard_time.strftime("%Y-%m-%d %H:%M:%S")

            status_time = node.get("latest_status_timestamp")
            if isinstance(status_time, datetime):
                status_time = status_time.strftime("%Y-%m-%d %H:%M:%S")
            elif status_time is None:
                status_time = "N/A"

            table_data.append(
                [
                    node.get("hostname", "N/A"),
                    node.get("sku", "N/A"),
                    f"{node.get('site', 'N/A')}/{node.get('rack', 'N/A')}",
                    onboard_time,
                    node.get("current_status", "N/A"),
                    status_time,
                ]
            )

        print(tabulate(table_data, headers=headers, tablefmt="grid"))
        print()

        # Display detailed info for first few nodes
        logger.info("\nDetailed Information (first 3 nodes):")
        logger.info(f"{'-'*80}")
        for i, node in enumerate(results[:3], 1):
            logger.info(f"\nNode {i}:")
            for key, value in node.items():
                logger.info(f"  {key}: {value}")

        client.close()
        logger.info(f"\n{'='*80}")
        logger.info("Test completed successfully!")
        logger.info(f"{'='*80}\n")
        return True

    except Exception as e:
        logger.error(f"\nTest failed with error: {str(e)}")
        logger.exception("Full traceback:")
        return False


def test_raw_query():
    """Test raw SQL query execution."""
    logger.info(f"\n{'='*80}")
    logger.info("Testing raw SQL query execution")
    logger.info(f"{'='*80}\n")

    try:
        client = PhysicalNodeOnboardClient(
            connection_str=os.getenv("POSTGRES_CONNECTION_STR"),
            schema=os.getenv("POSTGRES_SCHEMA", "ltp_sdk"),
        )

        # Test simple query
        logger.info(
            "Executing: SELECT COUNT(*) FROM ltp_sdk.physical_node_onboard_records"
        )
        results = client.execute_query(
            "SELECT COUNT(*) as total FROM ltp_sdk.physical_node_onboard_records"
        )

        total = results[0]["total"] if results else 0
        logger.info(f"Total records in physical_node_onboard_records: {total}\n")

        # Test with recent records
        logger.info("Executing: SELECT recent onboard records (last 7 days)")
        results = client.execute_query(
            """
            SELECT hostname, sku, timestamp
            FROM ltp_sdk.physical_node_onboard_records
            WHERE timestamp >= NOW() - INTERVAL '7 days'
            ORDER BY timestamp DESC
            LIMIT 5
        """
        )

        if results:
            logger.info(f"Found {len(results)} recent onboard records:")
            for record in results:
                logger.info(
                    f"  {record['hostname']} - {record['sku']} - {record['timestamp']}"
                )
        else:
            logger.info("No recent onboard records found.")

        client.close()
        logger.info(f"\n{'='*80}")
        logger.info("Raw query test completed successfully!")
        logger.info(f"{'='*80}\n")
        return True

    except Exception as e:
        logger.error(f"\nRaw query test failed with error: {str(e)}")
        logger.exception("Full traceback:")
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test PhysicalNodeOnboardClient in pod environment"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to look back for recent onboards (default: 7)",
    )
    parser.add_argument(
        "--skip-raw-query", action="store_true", help="Skip raw query test"
    )

    args = parser.parse_args()

    # Check environment
    if not check_env_vars():
        sys.exit(1)

    # Run tests
    success = True

    # Test get_new_nodes()
    if not test_get_new_nodes(days=args.days):
        success = False

    # Test raw query
    if not args.skip_raw_query:
        if not test_raw_query():
            success = False

    # Exit
    if success:
        logger.info("\n✓ All tests passed!")
        sys.exit(0)
    else:
        logger.error("\n✗ Some tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
