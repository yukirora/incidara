-- Track who sent each user message in a session.
-- A row is inserted when POST /sessions/:id/messages is called.
-- The gateway_seq is filled in later when the SSE pump sees the matching message.user event.
CREATE TABLE IF NOT EXISTS message_senders (
  id SERIAL PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  sender_email VARCHAR(255) NOT NULL,
  gateway_seq INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fast lookup: find unmatched sends for a session (gateway_seq IS NULL)
CREATE INDEX idx_message_senders_unmatched ON message_senders (session_id, created_at) WHERE gateway_seq IS NULL;

-- Fast lookup: enrich events by session + seq
CREATE INDEX idx_message_senders_by_seq ON message_senders (session_id, gateway_seq);
