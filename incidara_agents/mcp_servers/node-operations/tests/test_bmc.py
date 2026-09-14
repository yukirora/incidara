from unittest.mock import patch, MagicMock
from node_operations.bmc import get_sn_from_ipmi, check_fabricmanager

def test_get_sn_parses_serial():
    with patch("node_operations.bmc.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0, stdout=" Product Serial        : ABC123\n", stderr="")
        assert get_sn_from_ipmi("10.0.0.1") == "ABC123"

def test_check_fabricmanager_true():
    with patch("node_operations.bmc.run_remote_command_capture", return_value=(0, "Started NVIDIA fabric manager service.\n")):
        assert check_fabricmanager("10.0.0.1", "u", 30) is True
