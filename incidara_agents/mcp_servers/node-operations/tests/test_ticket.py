from unittest.mock import patch, MagicMock
from node_operations.ticket import submit_ticket, build_ticket_description, get_ticket_status
from node_operations.types import TicketConfig

def test_submit_ticket_returns_id():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"code": 0, "data": {"orderID": "T-1"}}
    with patch("node_operations.ticket.requests.post", return_value=resp):
        tid, _ = submit_ticket(sn="SN", description="d",
                               config=TicketConfig(base_url="https://api", auth_znsl="t", timeout=15))
        assert tid == "T-1"

def test_build_description():
    d = build_ticket_description(sn="SN", mgmt_ip="1.1.1.1", ip="2.2.2.2",
                                  hostname="node-01", summary="broken", reproducer="check")
    assert "SN" in d and "node-01" in d and "broken" in d

def test_get_ticket_status():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"code": 0, "data": {"status": "维修中"}}
    with patch("node_operations.ticket.requests.get", return_value=resp):
        result = get_ticket_status("T-1", TicketConfig(base_url="https://api", auth_znsl="t", timeout=15))
        assert result["data"]["status"] == "维修中"
