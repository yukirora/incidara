-- Track tool and LLM errors per task (even for tasks that complete successfully)
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS tool_error_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS llm_error_count INTEGER NOT NULL DEFAULT 0;
