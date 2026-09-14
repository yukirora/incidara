-- job_detection schema — parallel namespace for job-level patrol detection
-- Run against evidence DB (ltp_agent)
--
-- Mirrors the node-detection patrol_* tables but scoped to job targets.
-- The patrol-cron engine selects the right table namespace via _t() helper
-- based on the collector's target_type.
--
-- Consumed by:
--   - incidara_agents/mcp_servers/patrol-cron/patrol_cron/mcp_tools.py  (_t() table routing)
--   - incidara_agents/mcp_servers/patrol-cron/patrol_cron/db.py          (rate/dirty queries)

BEGIN;

CREATE TABLE IF NOT EXISTS job_detection_collectors (
    name            TEXT PRIMARY KEY,
    schedule_sec    INT NOT NULL DEFAULT 300,
    target_type     TEXT NOT NULL DEFAULT 'job',
    target_filter   JSONB DEFAULT '{}',
    created_by      TEXT DEFAULT 'agent',
    description     TEXT,
    enabled         BOOLEAN DEFAULT TRUE,
    sources         JSONB NOT NULL DEFAULT '[]',
    last_run        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS job_detection_patrol_rules (
    rule_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    binds_to        TEXT NOT NULL,
    analyze_code    TEXT NOT NULL,
    stage           TEXT NOT NULL DEFAULT 'log_only',
    enabled         BOOLEAN DEFAULT TRUE,
    created_by      TEXT DEFAULT 'agent',
    description     TEXT,
    graduated_from  TEXT,
    graduated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS job_detection_rule_state (
    rule_id     TEXT PRIMARY KEY,
    state_data  JSONB DEFAULT '{}',
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS job_detection_patrol_findings (
    finding_id              BIGSERIAL PRIMARY KEY,
    rule_id                 TEXT,
    target_id               TEXT NOT NULL,
    target_type             TEXT NOT NULL DEFAULT 'job',
    severity                TEXT NOT NULL DEFAULT 'warning',
    action                  TEXT NOT NULL DEFAULT 'alert',
    action_params           JSONB DEFAULT '{}',
    evidence                JSONB DEFAULT '{}',
    confidence              REAL DEFAULT 0.5,
    verdict                 TEXT,
    resolved                BOOLEAN DEFAULT FALSE,
    detected_at             TIMESTAMPTZ DEFAULT NOW(),
    collector_snapshot_id   TEXT,
    raw_evidence_hash       TEXT
);

CREATE INDEX IF NOT EXISTS idx_jd_findings_rule_detected
    ON job_detection_patrol_findings(rule_id, detected_at);
CREATE INDEX IF NOT EXISTS idx_jd_findings_target_detected
    ON job_detection_patrol_findings(target_id, detected_at);
CREATE INDEX IF NOT EXISTS idx_jd_findings_open
    ON job_detection_patrol_findings(resolved, detected_at) WHERE NOT resolved;

-- Collector snapshots for job detection — same structure as node-detection collector_snapshots.
CREATE TABLE IF NOT EXISTS job_detection_collector_snapshots (
    snapshot_hash   TEXT PRIMARY KEY,
    collector_name  TEXT NOT NULL,
    frozen_input    JSONB NOT NULL,
    target_count    INT DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMIT;
