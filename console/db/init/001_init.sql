-- Initial schema for incidara-console.
-- Runs on first boot via docker-entrypoint-initdb.d (empty data/ volume only).
-- Re-running after schema changes requires either adding a new migration file
-- applied by the server or manual psql execution; initdb scripts are a
-- one-time bootstrap.

CREATE TABLE IF NOT EXISTS _migrations (
  filename VARCHAR(255) PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  email VARCHAR(255) UNIQUE NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  name VARCHAR(255),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
  id SERIAL PRIMARY KEY,
  gateway_session_id VARCHAR(64) UNIQUE NOT NULL,
  agent_id VARCHAR(64) NOT NULL,
  owner_email VARCHAR(255) NOT NULL REFERENCES users(email),
  title TEXT,
  shared_with_users TEXT[] NOT NULL DEFAULT '{}',
  shared_with_groups TEXT[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_email);
CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_gw_id ON sessions(gateway_session_id);

-- Schedules fire tasks on a trigger (once / interval / cron).
-- Each firing creates a Task row (which runs or queues per normal rules).
CREATE TABLE IF NOT EXISTS schedules (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255),
  agent_id VARCHAR(64) NOT NULL,
  owner_email VARCHAR(255) NOT NULL REFERENCES users(email),
  prompt TEXT NOT NULL,

  -- Trigger
  trigger_type VARCHAR(16) NOT NULL,          -- 'once' | 'interval' | 'cron'
  run_at TIMESTAMPTZ,                          -- for 'once'
  interval_seconds INTEGER,                    -- for 'interval'
  cron_expr VARCHAR(64),                       -- for 'cron'
  timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',

  -- Session strategy
  session_mode VARCHAR(16) NOT NULL DEFAULT 'new',   -- 'new' | 'reuse'
  reuse_session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,

  -- State
  enabled BOOLEAN NOT NULL DEFAULT true,
  last_fired_at TIMESTAMPTZ,
  next_fire_at TIMESTAMPTZ,                    -- null when permanently done

  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_schedules_next_fire ON schedules(next_fire_at) WHERE enabled;
CREATE INDEX IF NOT EXISTS idx_schedules_owner ON schedules(owner_email);
CREATE INDEX IF NOT EXISTS idx_schedules_agent ON schedules(agent_id);

-- Tasks are units of work within a session. A task spans from an initial
-- prompt to a terminal event (completed/error/interrupted). User messages
-- sent while a task is waiting_input are guidance within that same task,
-- not new tasks.
--
-- Tasks can be `pending` if submitted while the session has an active task;
-- the queue promoter moves them to `running` when the prior task terminates.
CREATE TABLE IF NOT EXISTS tasks (
  id SERIAL PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  prompt TEXT NOT NULL,
  submitter_email VARCHAR(255) NOT NULL REFERENCES users(email),
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
      -- pending | running | waiting_input | completed | error | interrupted | cancelled
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),   -- when user submitted
  started_at TIMESTAMPTZ,                           -- when promoted from pending to running
  ended_at TIMESTAMPTZ,                             -- when reached terminal
  output_preview TEXT,
      -- last message.agent content in this task (200 chars max)
      -- fallback to partial_content on interrupt, error message on error
  gateway_seq_start INTEGER,
      -- seq of the message.user event starting this task (for deep-linking)
  from_schedule_id INTEGER REFERENCES schedules(id) ON DELETE SET NULL
      -- non-null if this task was created by a schedule firing
);

CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_submitter ON tasks(submitter_email);
CREATE INDEX IF NOT EXISTS idx_tasks_created_desc ON tasks(created_at DESC);
-- supports the queue promotion query (find oldest pending for a session)
CREATE INDEX IF NOT EXISTS idx_tasks_session_status_id ON tasks(session_id, status, id);
CREATE INDEX IF NOT EXISTS idx_tasks_from_schedule ON tasks(from_schedule_id) WHERE from_schedule_id IS NOT NULL;

-- Record this bootstrap so the server's migration runner skips it.
INSERT INTO _migrations (filename) VALUES ('001_init.sql')
ON CONFLICT (filename) DO NOTHING;
