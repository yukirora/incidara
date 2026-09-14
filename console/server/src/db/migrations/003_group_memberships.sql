CREATE TABLE IF NOT EXISTS group_memberships (
  id SERIAL PRIMARY KEY,
  group_id VARCHAR(64) NOT NULL,
  user_email VARCHAR(255) NOT NULL REFERENCES users(email) ON DELETE CASCADE,
  added_by VARCHAR(255),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(group_id, user_email)
);
CREATE INDEX IF NOT EXISTS idx_gm_group ON group_memberships(group_id);
CREATE INDEX IF NOT EXISTS idx_gm_email ON group_memberships(user_email);
