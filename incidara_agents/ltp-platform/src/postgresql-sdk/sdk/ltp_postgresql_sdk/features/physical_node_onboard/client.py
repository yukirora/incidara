# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Physical node onboard client for PostgreSQL operations."""

from typing import List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy import select, func, case, distinct, and_, or_, text
from ...base import PostgreSQLBaseClient
from ...models import PhysicalNodeOnboardRecord, NodeStatus
from ltp_storage.utils.time_util import convert_timestamp


class PhysicalNodeOnboardClient(PostgreSQLBaseClient):
    """
    Client for managing physical node onboard records in PostgreSQL.

    This client inherits execute_query() from PostgreSQLBaseClient for SQL execution.

    Example usage:
        client = PhysicalNodeOnboardClient()

        # Get new nodes
        new_nodes = client.get_new_nodes(days=7)

        # Execute raw SQL queries if needed
        results = client.execute_query(
            "SELECT * FROM ltp_sdk.physical_node_onboard_records WHERE hostname = 'node-01'"
        )
    """

    def get_new_nodes(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Get nodes that were onboarded recently but never had 'new' status.

        This identifies NEW_NODE as nodes in their initial state - onboarded
        but never had a 'new' status record in node_status table.

        Args:
            days: Number of days to look back for recent onboards (default: 7)

        Returns:
            List of dictionaries containing new node information with fields:
                - current_node_id: ID from physical_node_onboard_records
                - hostname: Node hostname
                - sn: Serial number
                - sku: SKU label
                - site: Site location
                - rack: Rack location
                - onboard_timestamp: When the node was onboarded
                - current_status: Most recent status from node_status
                - latest_status_timestamp: Timestamp of most recent status
                - node_type: Always 'NEW_NODE' for this query

        Example:
            client = PhysicalNodeOnboardClient()
            new_nodes = client.get_new_nodes(days=7)
            for node in new_nodes:
                print(f"New node: {node['hostname']} - SKU: {node['sku']}")
        """
        session = self.get_session()
        try:
            # Calculate cutoff time
            cutoff_time = datetime.utcnow() - timedelta(days=days)
            cutoff_time = convert_timestamp(cutoff_time, "datetime")
            # CTE 1: recent_onboards - get recent onboarding records
            recent_onboards = (
                select(
                    PhysicalNodeOnboardRecord.id,
                    PhysicalNodeOnboardRecord.hostname,
                    PhysicalNodeOnboardRecord.timestamp.label("onboard_timestamp"),
                    PhysicalNodeOnboardRecord.sn,
                    PhysicalNodeOnboardRecord.sku,
                    PhysicalNodeOnboardRecord.site,
                    PhysicalNodeOnboardRecord.rack,
                )
                .where(PhysicalNodeOnboardRecord.timestamp >= cutoff_time)
                .subquery()
            )

            # CTE 2: new_status_check - check if hostname has 'new' status
            new_status_check = (
                select(
                    NodeStatus.hostname,
                    func.max(case((NodeStatus.status == "new", 1), else_=0)).label(
                        "has_new_status"
                    ),
                )
                .group_by(NodeStatus.hostname)
                .subquery()
            )

            # CTE 3: latest_status - get most recent status for each hostname
            latest_status_subq = select(
                NodeStatus.hostname,
                NodeStatus.status,
                NodeStatus.timestamp.label("latest_status_timestamp"),
                func.row_number()
                .over(
                    partition_by=NodeStatus.hostname,
                    order_by=NodeStatus.timestamp.desc(),
                )
                .label("rn"),
            ).subquery()

            latest_status = (
                select(
                    latest_status_subq.c.hostname,
                    latest_status_subq.c.status,
                    latest_status_subq.c.latest_status_timestamp,
                )
                .where(latest_status_subq.c.rn == 1)
                .subquery()
            )

            # Final query
            query = (
                select(
                    recent_onboards.c.id.label("current_node_id"),
                    recent_onboards.c.hostname,
                    recent_onboards.c.sn,
                    recent_onboards.c.sku,
                    recent_onboards.c.site,
                    recent_onboards.c.rack,
                    recent_onboards.c.onboard_timestamp,
                    latest_status.c.status.label("current_status"),
                    latest_status.c.latest_status_timestamp,
                )
                .select_from(recent_onboards)
                .outerjoin(
                    new_status_check,
                    new_status_check.c.hostname == recent_onboards.c.hostname,
                )
                .outerjoin(
                    latest_status,
                    latest_status.c.hostname == recent_onboards.c.hostname,
                )
                .where(
                    or_(
                        new_status_check.c.has_new_status.is_(None),
                        new_status_check.c.has_new_status == 0,
                    )
                )
                .order_by(recent_onboards.c.onboard_timestamp.desc())
            )

            # Execute and convert to dictionaries
            result = session.execute(query)
            columns = result.keys()
            return [dict(zip(columns, row)) for row in result.fetchall()]

        finally:
            session.close()

    def get_node_id_by_hostname(self, hostname: str, timestamp: datetime = None) -> str:
        """
        Get the node ID for a hostname at a given timestamp.

        This retrieves the most recent physical node onboard record ID for the hostname
        that was onboarded at or before the given timestamp.

        Args:
            hostname: The hostname to look up
            timestamp: The timestamp to query (default: current time)
                      Returns the most recent node_id at or before this time

        Returns:
            str: The node ID (physical_node_onboard_records.id) as string,
                 or empty string if not found

        Example:
            client = PhysicalNodeOnboardClient()
            node_id = client.get_node_id_by_hostname('worker-01', datetime.utcnow())
            # Use this node_id when updating node_status or node_actions
        """
        session = self.get_session()
        try:
            if timestamp is None:
                timestamp = datetime.utcnow()
            timestamp = convert_timestamp(timestamp, "datetime")

            # Get the most recent onboard record for this hostname at or before timestamp
            query = (
                select(PhysicalNodeOnboardRecord.id)
                .where(
                    and_(
                        PhysicalNodeOnboardRecord.hostname == hostname,
                        PhysicalNodeOnboardRecord.timestamp <= timestamp,
                    )
                )
                .order_by(PhysicalNodeOnboardRecord.timestamp.desc())
                .limit(1)
            )

            result = session.execute(query).scalar_one_or_none()
            return str(result) if result else ""

        finally:
            session.close()

    def get_node_sku_by_hostname(self, hostname: str) -> str:
        """
        Get the SKU for a hostname from the most recent onboard record.

        Args:
            hostname: The hostname to look up

        Returns:
            str: The SKU label, or empty string if not found
        """
        session = self.get_session()
        try:
            query = (
                select(PhysicalNodeOnboardRecord.sku)
                .where(PhysicalNodeOnboardRecord.hostname == hostname)
                .order_by(PhysicalNodeOnboardRecord.timestamp.desc())
                .limit(1)
            )

            result = session.execute(query).scalar_one_or_none()
            return str(result) if result else ""

        finally:
            session.close()
