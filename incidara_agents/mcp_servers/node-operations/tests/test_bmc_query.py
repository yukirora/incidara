from unittest.mock import MagicMock, patch

import pytest

from node_operations.bmc import bmc_sel_query


def _completed(stdout: str = ""):
    return MagicMock(returncode=0, stdout=stdout, stderr="")


def test_bmc_query_supports_sel_info():
    with patch("node_operations.bmc.subprocess.run", return_value=_completed("Entries          : 12\n")) as run:
        out = bmc_sel_query("10.0.0.1", "root", "pw", command="sel_info")

    assert "Entries" in out
    assert run.call_args[0][0][-2:] == ["sel", "info"]


def test_bmc_query_supports_sel_time_get():
    with patch("node_operations.bmc.subprocess.run", return_value=_completed("06/23/2026 03:21:35\n")) as run:
        out = bmc_sel_query("10.0.0.1", "root", "pw", command="sel_time_get")

    assert "06/23/2026" in out
    assert run.call_args[0][0][-3:] == ["sel", "time", "get"]


def test_bmc_query_supports_sel_get_record_id():
    with patch("node_operations.bmc.subprocess.run", return_value=_completed("SEL Record ID          : 0084\n")) as run:
        out = bmc_sel_query("10.0.0.1", "root", "pw", command="sel_get", record_id="84")

    assert "0084" in out
    assert run.call_args[0][0][-3:] == ["sel", "get", "84"]


def test_bmc_query_sel_get_requires_record_id():
    with pytest.raises(ValueError, match="record_id is required"):
        bmc_sel_query("10.0.0.1", "root", "pw", command="sel_get")


def test_bmc_query_filters_sel_window_before_returning():
    sel = "\n".join([
        "  80 | 03/05/26 | 15:54:22 UTC | Temperature HBM Temp |  | Deasserted",
        "  81 | 06/21/26 | 16:30:12 UTC | Unknown #0xff |  | Asserted",
        "  82 | 06/22/26 | 07:06:39 UTC | Power Supply PS1 Status | Presence detected () | Asserted",
        "  83 | 06/23/26 | 00:00:00 UTC | Session Audit #0xff |  | Asserted",
    ]) + "\n"

    with patch("node_operations.bmc.subprocess.run", return_value=_completed(sel)):
        out = bmc_sel_query(
            "10.0.0.1",
            "root",
            "pw",
            command="sel_elist",
            since="2026-06-21 00:00:00 UTC",
            until="2026-06-22 23:59:59 UTC",
        )

    assert "06/21/26" in out
    assert "06/22/26" in out
    assert "03/05/26" not in out
    assert "06/23/26" not in out


def test_bmc_query_filters_sel_with_grep():
    sel = "\n".join([
        "  81 | 06/21/26 | 16:30:12 UTC | Unknown #0xff |  | Asserted",
        "  82 | 06/22/26 | 07:06:39 UTC | Power Supply PS1 Status | Presence detected () | Asserted",
    ]) + "\n"

    with patch("node_operations.bmc.subprocess.run", return_value=_completed(sel)):
        out = bmc_sel_query("10.0.0.1", "root", "pw", command="sel_elist", grep="power|ps1")

    assert "Power Supply PS1" in out
    assert "Unknown #0xff" not in out
