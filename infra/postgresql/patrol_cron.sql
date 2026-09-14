-- patrol_cron schema — collectors, rules, state, findings
-- Run against evidence DB (ltp_agent)
--
-- Coexists with switch_monitor.sql tables (switch_inventory, switch_state).
-- The SSH collector reads from switch_inventory;
-- patrol_findings is a separate output table.

BEGIN;

CREATE TABLE IF NOT EXISTS collectors (
    name            TEXT PRIMARY KEY,
    description     TEXT,
    schedule_sec    INT NOT NULL CHECK (schedule_sec > 0),
    enabled         BOOLEAN DEFAULT TRUE,
    created_by      TEXT NOT NULL,

    target_type     TEXT NOT NULL CHECK (target_type IN ('node', 'switch', 'job', 'query_result')),
    target_filter   JSONB,               -- {hostname_pattern, sample, schedulable, from_inventory, ...}

    sources         JSONB NOT NULL,       -- [{type, name, config}, ...]
    -- source types: prometheus | ssh | job_logs | node_logs | db_query | api

    last_run        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE collectors
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

CREATE TABLE IF NOT EXISTS patrol_rules (
    rule_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    binds_to        TEXT NOT NULL REFERENCES collectors(name) ON DELETE CASCADE,
    analyze_code    TEXT NOT NULL,  -- def analyze(collected, state) -> (list[Finding], new_state)
    stage           TEXT NOT NULL DEFAULT 'log_only'
                    CHECK (stage IN ('log_only', 'create_task', 'submit_alert', 'auto_cordon')),
    enabled         BOOLEAN DEFAULT TRUE,
    created_by      TEXT NOT NULL,
    graduated_from  TEXT,
    graduated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rules_binds_to ON patrol_rules(binds_to) WHERE enabled;

CREATE TABLE IF NOT EXISTS rule_state (
    rule_id         TEXT NOT NULL REFERENCES patrol_rules(rule_id) ON DELETE CASCADE,
    state_data      JSONB NOT NULL DEFAULT '{}',
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (rule_id)
);

CREATE TABLE IF NOT EXISTS patrol_findings (
    finding_id      SERIAL PRIMARY KEY,
    rule_id         TEXT REFERENCES patrol_rules(rule_id) ON DELETE SET NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('critical', 'warning', 'info')),
    target_id       TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    action          TEXT NOT NULL,
    action_params   JSONB,
    evidence        JSONB,
    confidence      FLOAT DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    verdict         TEXT CHECK (verdict IS NULL OR verdict IN ('confirmed', 'rejected', 'rejected_nff')),
    repair_outcome  TEXT CHECK (
                        repair_outcome IS NULL OR repair_outcome IN (
                            'REPAIR_CONFIRMED',
                            'NO_FAULT_FOUND',
                            'MISCLASSIFIED',
                            'MAINTENANCE_FIX',
                            'CONFIG_TASK'
                        )
                    ),
    repair_outcome_at TIMESTAMPTZ,
    collector_snapshot_id TEXT,
    raw_evidence_hash TEXT,
    task_id         INT,              -- Chat UI task ID (external reference, no FK)
    active          BOOLEAN DEFAULT TRUE,
    deactivated_at  TIMESTAMPTZ,
    signal_key      TEXT,
    event_id        TEXT,
    last_seen_at    TIMESTAMPTZ DEFAULT NOW(),
    seen_count      INT DEFAULT 1,
    resolved        BOOLEAN DEFAULT FALSE,
    detected_at     TIMESTAMPTZ DEFAULT NOW(),
    -- provenance columns: link findings to the exact rule/collector version that produced them
    verdict_reason          TEXT,
    rule_version_at         TIMESTAMPTZ,   -- timestamp of patrol_rule_versions row used
    collector_sources_at    JSONB,         -- collector source config snapshot at detection time
    rule_code_hash          TEXT,          -- md5 of analyze_code → links to patrol_rule_versions
    collector_config_hash   TEXT           -- md5 of sources config → links to collector_versions
);

ALTER TABLE patrol_findings
    ADD COLUMN IF NOT EXISTS repair_outcome TEXT CHECK (
        repair_outcome IS NULL OR repair_outcome IN (
            'REPAIR_CONFIRMED',
            'NO_FAULT_FOUND',
            'MISCLASSIFIED',
            'MAINTENANCE_FIX',
            'CONFIG_TASK'
        )
    ),
    ADD COLUMN IF NOT EXISTS repair_outcome_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS collector_snapshot_id TEXT,
    ADD COLUMN IF NOT EXISTS raw_evidence_hash TEXT,
    ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS signal_key TEXT,
    ADD COLUMN IF NOT EXISTS event_id TEXT,
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS seen_count INT DEFAULT 1,
    ADD COLUMN IF NOT EXISTS verdict_reason TEXT,
    ADD COLUMN IF NOT EXISTS rule_version_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS collector_sources_at JSONB,
    ADD COLUMN IF NOT EXISTS rule_code_hash TEXT,
    ADD COLUMN IF NOT EXISTS collector_config_hash TEXT;

UPDATE patrol_findings
SET last_seen_at = detected_at
WHERE last_seen_at IS NULL;

UPDATE patrol_findings
SET seen_count = 1
WHERE seen_count IS NULL;

DO $$
DECLARE
    constraint_row RECORD;
BEGIN
    FOR constraint_row IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'patrol_findings'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%verdict%'
    LOOP
        EXECUTE format('ALTER TABLE patrol_findings DROP CONSTRAINT IF EXISTS %I', constraint_row.conname);
    END LOOP;

    ALTER TABLE patrol_findings
        ADD CONSTRAINT patrol_findings_verdict_check
        CHECK (verdict IS NULL OR verdict IN ('confirmed', 'rejected', 'rejected_nff'));
END $$;

CREATE INDEX IF NOT EXISTS idx_findings_rule_detected ON patrol_findings(rule_id, detected_at);
CREATE INDEX IF NOT EXISTS idx_findings_target_detected ON patrol_findings(target_id, detected_at);
CREATE INDEX IF NOT EXISTS idx_findings_open ON patrol_findings(resolved, detected_at) WHERE NOT resolved;
CREATE UNIQUE INDEX IF NOT EXISTS idx_findings_active_lifecycle_key
    ON patrol_findings(rule_id, target_id, signal_key, action)
    WHERE active AND signal_key IS NOT NULL;

-- Collector snapshots — stores full CollectionResult keyed by content hash.
-- One row per unique collector run output. Findings reference this by hash.
-- Replay cases use this to reconstruct frozen_input.
CREATE TABLE IF NOT EXISTS collector_snapshots (
    snapshot_hash       TEXT PRIMARY KEY,     -- sha256 of serialized CollectionResult
    collector_name      TEXT NOT NULL,
    frozen_input        JSONB NOT NULL,       -- full CollectionResult: {collector_name, targets: [{id, type, payload, meta}], errors, duration}
    target_count        INT DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rule_reconciliation_state (
    rule_id                 TEXT PRIMARY KEY REFERENCES patrol_rules(rule_id) ON DELETE CASCADE,
    reconciliation_dirty    BOOLEAN DEFAULT FALSE,
    nff_baseline_at         TIMESTAMPTZ,
    reconcile_attempts      INT DEFAULT 0,
    last_delegated_at       TIMESTAMPTZ,
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rule_replay_cases (
    replay_case_id              BIGSERIAL PRIMARY KEY,
    case_id                     INT,
    finding_id                  INT REFERENCES patrol_findings(finding_id) ON DELETE SET NULL,
    rule_id                     TEXT NOT NULL REFERENCES patrol_rules(rule_id) ON DELETE CASCADE,
    source                      TEXT NOT NULL CHECK (
                                    source IN (
                                        'rma_bad_feedback',
                                        'confirmed_counterexample',
                                        'manual_counterexample'
                                    )
                                ),
    repair_outcome              TEXT CHECK (
                                    repair_outcome IS NULL OR repair_outcome IN (
                                        'REPAIR_CONFIRMED',
                                        'NO_FAULT_FOUND',
                                        'MISCLASSIFIED',
                                        'MAINTENANCE_FIX',
                                        'CONFIG_TASK'
                                    )
                                ),
    attribution                 TEXT CHECK (
                                    attribution IS NULL OR attribution IN (
                                        'detection',
                                        'triage',
                                        'inspect',
                                        'repair',
                                        'automation',
                                        'vendor_uncertain',
                                        'unknown'
                                    )
                                ),
    rule_version_at_detection   TEXT,
    collector_snapshot_id       TEXT,
    raw_evidence_hash           TEXT,
    frozen_input                JSONB NOT NULL,
    expected_behavior           JSONB NOT NULL,
    created_by                  TEXT NOT NULL,
    created_at                  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rule_replay_cases_rule ON rule_replay_cases(rule_id, source);
CREATE INDEX IF NOT EXISTS idx_rule_replay_cases_finding ON rule_replay_cases(finding_id);

CREATE TABLE IF NOT EXISTS detection_lifecycle_state (
    rule_id             TEXT NOT NULL,
    target_id           TEXT NOT NULL,
    signal_key          TEXT NOT NULL,
    action              TEXT NOT NULL,
    bad_count           INT NOT NULL DEFAULT 0,
    healthy_count       INT NOT NULL DEFAULT 0,
    active_finding_id   INT REFERENCES patrol_findings(finding_id) ON DELETE SET NULL,
    last_status         TEXT CHECK (last_status IS NULL OR last_status IN ('bad', 'healthy', 'unknown')),
    first_seen_at       TIMESTAMPTZ,
    last_seen_at        TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    expires_at          TIMESTAMPTZ,
    PRIMARY KEY (rule_id, target_id, signal_key, action)
);

CREATE INDEX IF NOT EXISTS idx_detection_lifecycle_expires
    ON detection_lifecycle_state(expires_at)
    WHERE active_finding_id IS NULL;

-- Version history for rule analyze_code — append-only, keyed by content hash.
-- Findings link back via rule_code_hash to reproduce exact rule logic at detection time.
CREATE TABLE IF NOT EXISTS patrol_rule_versions (
    code_hash       TEXT NOT NULL,
    rule_id         TEXT NOT NULL,
    analyze_code    TEXT NOT NULL,
    stage           TEXT,
    saved_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    saved_by        TEXT,
    PRIMARY KEY (code_hash, rule_id)
);

CREATE INDEX IF NOT EXISTS idx_patrol_rule_versions_rule ON patrol_rule_versions(rule_id, saved_at DESC);

-- Version history for collector sources config — append-only, keyed by content hash.
-- Findings link back via collector_config_hash to reproduce exact collector config at detection time.
CREATE TABLE IF NOT EXISTS collector_versions (
    config_hash     TEXT NOT NULL,
    name            TEXT NOT NULL,
    sources         JSONB,
    target_filter   JSONB,
    schedule_sec    INT,
    enabled         BOOLEAN,
    saved_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    saved_by        TEXT,
    PRIMARY KEY (config_hash, name)
);

CREATE INDEX IF NOT EXISTS idx_collector_versions_name ON collector_versions(name, saved_at DESC);

COMMIT;
