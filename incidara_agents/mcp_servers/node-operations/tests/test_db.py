from node_operations.db import is_ticket_completed

def test_completed():
    assert is_ticket_completed("已完成") is True
    assert is_ticket_completed("已撤销") is True

def test_not_completed():
    assert is_ticket_completed("维修中") is False


def test_insert_status_transition_builds_correct_action_key():
    """Verify insert_status_transition computes the action key as 'from-to'."""
    import unittest.mock as um
    mock_status = um.MagicMock()
    mock_action = um.MagicMock()
    mock_physical = um.MagicMock()
    # Mock _resolve_current_status to return a known status
    with um.patch("node_operations.db_write._resolve_current_status", return_value="triaged_unknown"):
        from node_operations.db_write import insert_status_transition
        insert_status_transition(
            status_client=mock_status, action_client=mock_action,
            physical_node_client=mock_physical,
            hostname="node-01", node_id="n1",
            from_status="triaged_unknown", to_status="triaged_hardware",
            reason="GPU ECC", detail='{"fault_source": "gpu", "reason": "ECC errors detected"}', category="hardware",
        )
        mock_action.update_node_action.assert_called_once()
        call_args = mock_action.update_node_action.call_args
        assert call_args[0][1] == "triaged_unknown-triaged_hardware"
        mock_status.update_node_status.assert_called_once()
        assert mock_status.update_node_status.call_args[0][1] == "triaged_hardware"


def test_insert_rma_transition_fallback_uses_parameterized_transaction():
    """If SDK rejects transition, fallback should use parameterized transaction."""
    import unittest.mock as um
    from node_operations import db_write

    mock_status = um.MagicMock()
    mock_action = um.MagicMock()
    mock_physical = um.MagicMock()

    # force SDK path to fail
    mock_action.update_node_action.side_effect = RuntimeError("rejected")

    with um.patch("node_operations.db_write._execute_transaction") as mock_txn:
        db_write.insert_rma_transition(
            status_client=mock_status,
            action_client=mock_action,
            physical_node_client=mock_physical,
            hostname="node-02",
            onboard_id=456,
            ticket_id="TEST-FAIL-1776689773",
            description="test",
            detail="test",
        )

        # Fallback uses _execute_transaction with mixed items:
        # first is (sql, params) tuple, rest are plain SQL strings
        mock_txn.assert_called_once()
        queries = mock_txn.call_args[0][1]
        assert len(queries) == 3  # rma + action + status

        # First item is the parameterized RMA insert
        rma_item = queries[0]
        assert isinstance(rma_item, tuple)
        sql, params = rma_item
        assert isinstance(params, dict)
        # SQLAlchemy named params use :param syntax
        assert any(f":{k}" in sql for k in params), \
            f"SQL doesn't reference its params: {params} not in {sql[:80]}"

        # Remaining items are plain SQL strings (f-string interpolated)
        for q in queries[1:]:
            assert isinstance(q, str)
            assert "INSERT" in q


def test_insert_complete_rma_vendor_format_strings_safe():
    """Verify complete_rma handles vendor data with %(xxx)s format strings.

    This is the exact bug: vendor ticket text containing Python-style format
    strings like %(nvlink)s or %(yes)s would crash with:
      "A value is required for bind parameter 'nvlink'"
    when the JSON was interpolated into SQL as a string literal.
    Parameterized queries prevent this.
    """
    import unittest.mock as um
    import json
    from node_operations import db_write

    mock_status = um.MagicMock()
    mock_action = um.MagicMock()
    mock_physical = um.MagicMock()

    # Vendor payload with problematic format strings
    vendor_payload = {
        "data": {
            "repairStatus": "已完成",
            "description": "test %(nvlink)s benchmark failure",
            "processingInfos": "repaired %(yes)s component",
        }
    }

    with um.patch("node_operations.db_write._execute_transaction") as mock_txn:
        db_write.insert_complete_rma(
            status_client=mock_status,
            action_client=mock_action,
            physical_node_client=mock_physical,
            hostname="h200-000100",
            node_id="100",
            onboard_id=100,
            ticket_id="6a065b4da96362cb6f21b818",
            ticket_payload=vendor_payload,
            detail="vendor repair completed",
        )

        mock_txn.assert_called_once()
        queries = mock_txn.call_args[0][1]
        assert len(queries) == 3

        # Check the RMA SQL specifically — metainfo must be a bind param
        rma_sql, rma_params = queries[0]
        assert ":metainfo" in rma_sql
        metainfo = json.loads(rma_params["metainfo"])
        assert metainfo["version"] == 1
        # The vendor data with %(nvlink)s and %(yes)s is preserved exactly
        assert "%(nvlink)s" in metainfo["details"]["data"]["description"]
        assert "%(yes)s" in metainfo["details"]["data"]["processingInfos"]
        # No literal %(nvlink)s in the SQL string itself
        assert "%(nvlink)s" not in rma_sql
        assert "%(yes)s" not in rma_sql
