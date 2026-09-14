-- Track delegation chain: which task created this task
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS parent_task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id) WHERE parent_task_id IS NOT NULL;
