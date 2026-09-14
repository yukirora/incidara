from dataclasses import dataclass
from typing import Any


@dataclass
class SshCredentials:
    user: str
    password: str
    timeout: int = 30


@dataclass
class ResetSshCredentials:
    user: str
    password: str


@dataclass
class BmcCredentials:
    password: str
    reset_password: str


@dataclass
class TicketConfig:
    base_url: str
    auth_znsl: str
    timeout: int = 15


@dataclass
class K8sHosts:
    master_user: str
    master_ip: str


@dataclass
class NodeInfo:
    hostname: str
    node_id: str
    onboard_id: int
    sn: str
    category: str
    ip: list[str]
    mgmt_ip: list[str]
    detail: dict[str, Any]
    ticket_id: str | None = None

    def get_first_ip(self) -> str:
        if not self.ip:
            raise RuntimeError(f"no IP for hostname={self.hostname}")
        return self.ip[0]

    def get_first_mgmt_ip(self) -> str:
        if not self.mgmt_ip:
            raise RuntimeError(f"no mgmt_ip for hostname={self.hostname}")
        return self.mgmt_ip[0]
