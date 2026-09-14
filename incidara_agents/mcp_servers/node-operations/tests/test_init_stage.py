import pathlib
from unittest.mock import patch
from node_operations.init_stage import run_init_stage


def test_calls_scp_ssh_cleanup():
    calls = []
    with patch("node_operations.init_stage.scp_upload") as mock_scp, \
         patch("node_operations.init_stage.ssh_with_password") as mock_ssh, \
         patch("node_operations.init_stage.get_bundle_path", return_value=pathlib.Path("/fake/init_bundle")):
        def fake_ssh(ip, user, password, command, timeout):
            calls.append(command)
            return (0, "")
        mock_ssh.side_effect = fake_ssh
        run_init_stage(hostname="n", ip="1.2.3.4", ssh_user="u", ssh_password="p",
                       bmc_password="b", timeout=30,
                       bundle="init_bundle", script="teardown.sh")
        mock_scp.assert_called_once()
        assert any("teardown.sh" in str(c) for c in calls), f"teardown.sh not found in calls: {calls}"
        assert any("rm -r" in str(c) for c in calls), f"rm -r not found in calls: {calls}"


def test_raises_on_script_failure():
    with patch("node_operations.init_stage.scp_upload"), \
         patch("node_operations.init_stage.ssh_with_password", return_value=(1, "err")), \
         patch("node_operations.init_stage.get_bundle_path", return_value=pathlib.Path("/fake/bundle")):
        try:
            run_init_stage(hostname="n", ip="1.2.3.4", ssh_user="u", ssh_password="p",
                           bmc_password="b", timeout=30,
                           bundle="init_bundle", script="teardown.sh")
            assert False
        except RuntimeError:
            pass
