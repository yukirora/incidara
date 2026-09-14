-- Model pricing table (replaces hardcoded pricing.ts)
-- Prices in USD per 1M tokens
-- Source: http://35.220.164.252:3888/pricing

CREATE TABLE IF NOT EXISTS model_pricing (
  id SERIAL PRIMARY KEY,
  model TEXT NOT NULL UNIQUE,
  input_per_m NUMERIC(10,4) NOT NULL,
  output_per_m NUMERIC(10,4) NOT NULL,
  cache_read_per_m NUMERIC(10,4) NOT NULL,
  cache_creation_per_m NUMERIC(10,4) NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed with official Anthropic pricing (https://www.anthropic.com/pricing)
INSERT INTO model_pricing (model, input_per_m, output_per_m, cache_read_per_m, cache_creation_per_m) VALUES
  -- Sonnet 4 family: input $3, output $15, cache_read $0.30, cache_write $3.75
  ('claude-sonnet-4-6', 3.00, 15.00, 0.30, 3.75),
  ('claude-sonnet-4-20250514', 3.00, 15.00, 0.30, 3.75),
  ('claude-sonnet-4-5-20250929', 3.00, 15.00, 0.30, 3.75),
  -- Opus 4.5+/4.6/4.7 family: input $5, output $25, cache_read $0.50, cache_write $6.25
  ('claude-opus-4-6', 5.00, 25.00, 0.50, 6.25),
  ('claude-opus-4-7', 5.00, 25.00, 0.50, 6.25),
  ('claude-opus-4-5-20251101', 5.00, 25.00, 0.50, 6.25),
  -- Opus 4/4.1 family: input $15, output $75, cache_read $1.50, cache_write $18.75
  ('claude-opus-4-20250514', 15.00, 75.00, 1.50, 18.75),
  ('claude-opus-4-1-20250805', 15.00, 75.00, 1.50, 18.75),
  -- Haiku 4.5: input $1, output $5, cache_read $0.10, cache_write $1.25
  ('claude-haiku-4-5-20251001', 1.00, 5.00, 0.10, 1.25)
ON CONFLICT (model) DO UPDATE SET
  input_per_m = EXCLUDED.input_per_m,
  output_per_m = EXCLUDED.output_per_m,
  cache_read_per_m = EXCLUDED.cache_read_per_m,
  cache_creation_per_m = EXCLUDED.cache_creation_per_m,
  updated_at = NOW();
