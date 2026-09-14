from unittest.mock import patch
from node_operations.reset import run_reset

def test_reset_calls_three_stages():
    scripts = []
    with patch("node_operations.reset.run_init_stage") as m:
        m.side_effect = lambda **kw: scripts.append(kw["script"])
        run_reset(hostname="n", ip="1.2.3.4", ssh_user="u", ssh_password="p",
                  reset_ssh_user="ru", reset_ssh_password="rp",
                  bmc_password="b", reset_bmc_password="rb", timeout=30)
    assert scripts == ["teardown.sh", "create_debug_user.sh", "clear_node.sh"]
