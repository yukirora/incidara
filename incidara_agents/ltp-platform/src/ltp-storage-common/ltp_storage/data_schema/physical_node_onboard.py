# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Physical node onboard record dataclasses."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional


@dataclass
class PhysicalNodeOnboardRecordData:
    """
    Physical node onboard record with node hardware and location details.

    Attributes:
        sn: Serial number of the onboarding node
        site: Site location of the node
        rack: Rack location of the node
        unit: Unit location within the rack
        category: Category of the node
        rank: Rank of the node in the nodes of the same category
        hostname: The hostname of the node
        cpu: Node CPU information as dict
        memory: Node memory information as dict
        gpu: Node GPU information as dict
        nic: Node NIC information as dict
        disk: Node disk information as dict
        sku: SKU label, generated from node components
        ip: Node IPs as dict
        mgmt_ip: Node management IPs as dict
        timestamp: Node onboarding timestamp
        metainfo: Node onboarding meta info as dict
        id: Primary key (optional, set after insertion)
    """

    sn: str
    site: str
    rack: str
    unit: int
    category: str
    rank: int
    hostname: str
    mgmt_ip: Dict[str, Any]
    timestamp: datetime
    cpu: Optional[Dict[str, Any]] = None
    memory: Optional[Dict[str, Any]] = None
    gpu: Optional[Dict[str, Any]] = None
    nic: Optional[Dict[str, Any]] = None
    disk: Optional[Dict[str, Any]] = None
    sku: Optional[str] = None
    ip: Optional[Dict[str, Any]] = None
    metainfo: Optional[Dict[str, Any]] = None
    id: Optional[int] = None

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "PhysicalNodeOnboardRecordData":
        """
        Create a PhysicalNodeOnboardRecordData instance from a dictionary.
        Handles both snake_case (PostgreSQL) and camelCase keys.

        Args:
            record: Dictionary containing the physical node onboard data

        Returns:
            PhysicalNodeOnboardRecordData: A new instance
        """
        from ..utils.time_util import convert_timestamp

        # Handle timestamp conversion
        timestamp_val = record.get("timestamp") or record.get("Timestamp")
        if timestamp_val:
            if isinstance(timestamp_val, str):
                timestamp = convert_timestamp(timestamp_val, "datetime")
            else:
                timestamp = timestamp_val
        else:
            timestamp = datetime.utcnow()

        return cls(
            id=record.get("id"),
            sn=record.get("sn", ""),
            site=record.get("site", ""),
            rack=record.get("rack", ""),
            unit=record.get("unit", 0),
            category=record.get("category", ""),
            rank=record.get("rank", 0),
            hostname=record.get("hostname", ""),
            cpu=record.get("cpu"),
            memory=record.get("memory"),
            gpu=record.get("gpu"),
            nic=record.get("nic"),
            disk=record.get("disk"),
            sku=record.get("sku"),
            ip=record.get("ip"),
            mgmt_ip=record.get("mgmt_ip", {}),
            timestamp=timestamp,
            metainfo=record.get("metainfo"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with snake_case keys."""
        return {
            "id": self.id,
            "sn": self.sn,
            "site": self.site,
            "rack": self.rack,
            "unit": self.unit,
            "category": self.category,
            "rank": self.rank,
            "hostname": self.hostname,
            "cpu": self.cpu,
            "memory": self.memory,
            "gpu": self.gpu,
            "nic": self.nic,
            "disk": self.disk,
            "sku": self.sku,
            "ip": self.ip,
            "mgmt_ip": self.mgmt_ip,
            "timestamp": self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else self.timestamp,
            "metainfo": self.metainfo,
        }
