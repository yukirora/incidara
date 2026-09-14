import requests
from node_operations.types import TicketConfig


def build_ticket_description(*, sn: str, mgmt_ip: str, ip: str, hostname: str,
                              summary: str, reproducer: str) -> str:
    return (f"【序列号】\n{sn}\n【BMC IP】\n{mgmt_ip}\n【SSH IP】\n{ip}\n"
            f"【hostname】\n{hostname}\n【问题描述】\n{summary}\n【复现方式】\n{reproducer}\n")


def submit_ticket(*, sn: str, description: str, config: TicketConfig) -> tuple[str, dict]:
    resp = requests.post(
        config.base_url.rstrip("/") + "/openapi/v1/order",
        headers={"Authorization-Znsl": config.auth_znsl, "Content-Type": "application/json"},
        json={"description": description, "equipment_info": {"equipment_sn": sn, "asset_type": "MACHINE"}},
        timeout=config.timeout,
    )
    payload = resp.json()
    if resp.status_code >= 400 or payload.get("code") not in (0, "0", None):
        raise RuntimeError(f"ticket submit failed: status={resp.status_code} payload={payload}")
    tid = payload.get("data", {}).get("orderID") or payload.get("data", {}).get("orderId")
    if not tid:
        raise RuntimeError(f"ticket id missing: {payload}")
    return str(tid), payload


def get_ticket_status(ticket_id: str, config: TicketConfig) -> dict:
    resp = requests.get(
        config.base_url.rstrip("/") + "/openapi/v1/repair/order",
        headers={"Authorization-Znsl": config.auth_znsl},
        params={"id": ticket_id}, timeout=config.timeout,
    )
    payload = resp.json()
    if resp.status_code >= 400 or payload.get("code") not in (0, "0", None):
        raise RuntimeError(f"ticket get failed: status={resp.status_code}")
    return payload
