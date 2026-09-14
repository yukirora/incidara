from node_operations.types import SshCredentials, BmcCredentials, TicketConfig, NodeInfo


def test_ssh_credentials():
    c = SshCredentials(user="u", password="p")
    assert c.user == "u" and c.password == "p" 


def test_node_info_get_first_ip():
    n = NodeInfo(hostname="n", node_id="1", onboard_id=1, sn="s",
                 category="h200", ip=["10.0.0.1"], mgmt_ip=["10.0.1.1"], detail={})
    assert n.get_first_ip() == "10.0.0.1"


def test_node_info_get_first_ip_empty_raises():
    n = NodeInfo(hostname="n", node_id="1", onboard_id=1, sn="s",
                 category="h200", ip=[], mgmt_ip=[], detail={})
    try:
        n.get_first_ip()
        assert False
    except RuntimeError:
        pass
