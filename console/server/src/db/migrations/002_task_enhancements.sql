ALTER TABLE tasks ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completion_mode VARCHAR(16) NOT NULL DEFAULT 'auto';
-- completion_mode: 'auto' = task completes when session.completed fires (current behavior)
--                  'manual' = task stays open until user explicitly marks complete
