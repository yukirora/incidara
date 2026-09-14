CREATE TABLE IF NOT EXISTS agent_group_access (
  id SERIAL PRIMARY KEY,
  agent_id VARCHAR(64) NOT NULL,
  group_id VARCHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(agent_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_aga_agent ON agent_group_access(agent_id);
