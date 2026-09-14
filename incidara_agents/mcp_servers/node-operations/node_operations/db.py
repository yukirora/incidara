"""DB operations — re-exports from db_read and db_write.

Split into two modules for access control:
  db_read.py  — safe for all roles (readonly, diagnosis, ops)
  db_write.py — restricted by role (diagnosis: status transitions only, ops: all writes)

Write functions use raw SQL fallback when the SDK rejects transitions
involving physical pipeline statuses (ready_ua, etc.).
"""

# Reads (all roles)
from node_operations.db_read import (
    create_clients,
    is_ticket_completed,
    get_nodes_by_status,
    get_nodes_by_status_with_alerts,
    get_node_history,
    get_node_history_with_alerts,
    get_latest_action_by_state,
    get_node_detail,
    get_node_detail_by_onboard_id,
    get_ticket_id_for_node,
    get_node_alerts,
    get_node_recent_jobs,
    get_job_details,
    get_job_events,
    get_validation_job,
    get_existing_reasons,
)

# Alert Manager API (triage + validation)
from node_operations.alert_manager import (
    submit_validation,
    submit_triage_alert,
)

# Delegation (triage → repair via Chat UI)
from node_operations.delegation import (
    delegate_to_agent,
)

# Writes (role-restricted)
from node_operations.db_write import (
    insert_status_transition,
    insert_rma_transition,
    insert_complete_rma,
    insert_allocated_ua,
    insert_triaged_unknown,
    clone_onboard_record,
)
