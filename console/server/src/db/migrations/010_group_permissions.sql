-- custom_groups: add active + updated_at + unique name constraint
ALTER TABLE custom_groups
  ADD COLUMN active BOOLEAN NOT NULL DEFAULT true,
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE custom_groups
  ADD CONSTRAINT custom_groups_name_key UNIQUE (name);

-- agent_group_access: add access_level + updated_at
ALTER TABLE agent_group_access
  ADD COLUMN access_level VARCHAR(32) NOT NULL DEFAULT 'interactive',
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE agent_group_access
  ADD CONSTRAINT agent_group_access_level_check
  CHECK (access_level IN ('interactive', 'readonly', 'readonly_input', 'readonly_summary'));

-- group_dashboard_permissions: new table
CREATE TABLE group_dashboard_permissions (
  id              SERIAL PRIMARY KEY,
  group_id        VARCHAR(64) NOT NULL,
  dashboard       VARCHAR(64) NOT NULL,
  allowed         BOOLEAN NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT gdp_group_dashboard_key UNIQUE (group_id, dashboard)
);

ALTER TABLE group_dashboard_permissions
  ADD CONSTRAINT gdp_group_fkey
  FOREIGN KEY (group_id) REFERENCES custom_groups(id) ON DELETE CASCADE;
