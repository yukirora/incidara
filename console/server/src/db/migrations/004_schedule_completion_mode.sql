-- Add completion_mode to schedules so users can choose manual vs auto for scheduled tasks
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS completion_mode VARCHAR(16) NOT NULL DEFAULT 'auto';
-- completion_mode: 'auto' = task auto-completes when session.completed fires
--                  'manual' = task requires user to mark complete
