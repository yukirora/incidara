-- switch_monitor schema — switch inventory
-- Run against evidence DB (ltp_agent)
--
-- Consumed by:
--   - incidara_agents/mcp_servers/patrol-cron/patrol_cron/collectors/ssh.py  (target discovery)
--   - incidara_agents/mcp_servers/patrol-cron/patrol_cron/db.py              (hostname lookup)
--   - incidara_agents/mcp_servers/switch-operations/switch_operations/ssh.py (list switches)

BEGIN;

-- Static switch inventory — populated at cluster setup, updated when switches are added/replaced.
CREATE TABLE IF NOT EXISTS switch_inventory (
    hostname    TEXT PRIMARY KEY,
    ip          TEXT NOT NULL,
    type        TEXT NOT NULL,          -- e.g. 'spine', 'leaf', 'tor'
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

COMMIT;
