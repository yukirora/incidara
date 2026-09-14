-- Task-level usage tracking tables
-- Stores token usage and cost per task, with per-model breakdowns

-- Main usage table: one row per task
CREATE TABLE IF NOT EXISTS task_usage (
  task_id INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
  total_input_tokens BIGINT NOT NULL DEFAULT 0,
  total_output_tokens BIGINT NOT NULL DEFAULT 0,
  total_cache_creation_input_tokens BIGINT NOT NULL DEFAULT 0,
  total_cache_read_input_tokens BIGINT NOT NULL DEFAULT 0,
  total_tokens BIGINT NOT NULL DEFAULT 0,
  total_cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0,
  turns INTEGER NOT NULL DEFAULT 0,
  models TEXT[] NOT NULL DEFAULT '{}',
  calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Per-model usage within a task
CREATE TABLE IF NOT EXISTS task_model_usage (
  id SERIAL PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  input_tokens BIGINT NOT NULL DEFAULT 0,
  output_tokens BIGINT NOT NULL DEFAULT 0,
  cache_creation_input_tokens BIGINT NOT NULL DEFAULT 0,
  cache_read_input_tokens BIGINT NOT NULL DEFAULT 0,
  cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0,
  UNIQUE(task_id, model)
);

CREATE INDEX IF NOT EXISTS idx_task_model_usage_task_id ON task_model_usage(task_id);
CREATE INDEX IF NOT EXISTS idx_task_usage_calculated_at ON task_usage(calculated_at);
