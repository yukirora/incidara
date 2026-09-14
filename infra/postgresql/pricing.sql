-- LTP TCO — pricing table schema and seed data.
--
-- Consumed by:
--   - incidara_agents/tools/postgresql_query_tool.py  (SELECT at runtime)
--   - ~/.claude/skills/ltp_metrics/scripts/tco.py (SELECT via psycopg2)
--
-- Originally created ad-hoc in pgAdmin4 around 2026-03-11.
-- This file reproduces that state so the schema+seed can be re-applied
-- to a fresh database.
--
-- Apply with:
--   psql "$POSTGRES_URL" -f pricing.sql
--
-- Notes:
--   * gpu_type is NULL for CPU-only jobs, so it cannot be PRIMARY KEY
--     (PK forbids NULL). UNIQUE is used instead; PostgreSQL treats
--     multiple NULLs as distinct, which is fine because there is only
--     ever one CPU row.
--   * All NUMERIC columns use unbounded precision — match the pgAdmin4
--     definition if it differs.

BEGIN;

CREATE TABLE IF NOT EXISTS pricing (
    gpu_type                        VARCHAR(50) UNIQUE,
    total_hardware_cost_per_gpu_usd NUMERIC,
    useful_life_years               NUMERIC,
    electricity_rate_per_kwh_usd    NUMERIC,
    pue                             NUMERIC,
    labor_cost_per_gpu_hour_usd     NUMERIC,
    maintenance_rate_annual         NUMERIC
);

COMMENT ON TABLE  pricing IS
    'Per-GPU-type TCO pricing factors. One row per GPU SKU; gpu_type NULL = CPU-only.';
COMMENT ON COLUMN pricing.gpu_type                        IS 'GPU SKU (e.g. "B300", "H200"); NULL for CPU-only jobs.';
COMMENT ON COLUMN pricing.total_hardware_cost_per_gpu_usd IS 'USD/GPU: card + chassis + networking + cooling + power infra, amortized per GPU.';
COMMENT ON COLUMN pricing.useful_life_years               IS 'Depreciation period in years (typical 3-5).';
COMMENT ON COLUMN pricing.electricity_rate_per_kwh_usd    IS 'Electricity cost in USD/kWh.';
COMMENT ON COLUMN pricing.pue                             IS 'Power Usage Effectiveness: facility power / IT power (typical 1.2-1.4).';
COMMENT ON COLUMN pricing.labor_cost_per_gpu_hour_usd     IS 'Ops headcount cost amortized per GPU-hour (USD).';
COMMENT ON COLUMN pricing.maintenance_rate_annual         IS 'Annual vendor support/warranty cost as fraction of hardware cost (e.g. 0.12 = 12%).';

-- Seed data. Matches _FAKE_PRICING in postgresql_query_tool.py.
INSERT INTO pricing
    (gpu_type, total_hardware_cost_per_gpu_usd, useful_life_years,
     electricity_rate_per_kwh_usd, pue,
     labor_cost_per_gpu_hour_usd, maintenance_rate_annual)
VALUES
    ('B300', 40000.00, 4.0, 0.0800, 1.30, 0.5000, 0.1200),
    ('H200', 25000.00, 4.0, 0.0800, 1.30, 0.5000, 0.1200),
    (NULL,   NULL,     NULL, 0.0800, 1.30, 0.5000, NULL);

COMMIT;
