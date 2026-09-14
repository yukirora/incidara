-- Incidara Infrastructure PostgreSQL
-- This runs automatically on first container start via docker-entrypoint-initdb.d/

-- Agent Evidence DB schema
-- Used by triage and repair agents to persist investigation artifacts.
CREATE TABLE IF NOT EXISTS investigation_evidence (
    id           BIGSERIAL PRIMARY KEY,
    node_name    VARCHAR NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    collected_by VARCHAR NOT NULL,        -- auto-set from AGENT_NAME env var (triage, repair-draft, repair, ticket-replay)
    source       VARCHAR NOT NULL,        -- probe_ssh, nvidia_smi, dmesg, ib_stat, job_log, alert, nvlink, fabricmanager, job_list, job_detail, job_events, other
    category     VARCHAR,                 -- gpu, ib, nvlink, pcie, cpu, memory, platform, unknown
    summary      TEXT,                    -- 1-line interpretation of what was found
    content      TEXT NOT NULL,           -- raw output from tool/command
    metadata     JSONB DEFAULT '{}',      -- optional structured fields (e.g., {"gpu_index": 4, "ecc_count": 42})
    finding_id   INT                      -- optional back-reference to patrol_findings.finding_id
);

CREATE INDEX IF NOT EXISTS idx_evidence_node
    ON investigation_evidence(node_name, collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_evidence_source
    ON investigation_evidence(node_name, source);

ALTER TABLE investigation_evidence
    ADD COLUMN IF NOT EXISTS finding_id INT;  -- back-reference to patrol_findings.finding_id

-- ============================================================
-- Agent Intelligence Feedback Loop — Knowledge Store Tables
-- ============================================================

CREATE TABLE IF NOT EXISTS case_memory (
    id                    SERIAL PRIMARY KEY,
    hostname              TEXT NOT NULL,
    onboard_id            INT,
    rma_ticket_id         TEXT,
    our_classification    TEXT NOT NULL,
    our_reason            TEXT,
    our_evidence          JSONB DEFAULT '{}',
    our_investigation     JSONB DEFAULT '{}',
    vendor_verdict        TEXT NOT NULL,
    vendor_confidence     TEXT DEFAULT 'MEDIUM',
    vendor_answer_quality TEXT DEFAULT 'MODERATE',   -- DETAILED / MODERATE / MINIMAL / NONE
    vendor_repair_raw     TEXT,
    vendor_repair_type    TEXT,
    vendor_component      TEXT,
    rma_completed_at      TIMESTAMPTZ,
    collected_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    collected_by          TEXT NOT NULL,
    claude_session_id     JSONB DEFAULT '[]'          -- [{agent: "repair", session_id: "..."}, ...]
);

CREATE INDEX IF NOT EXISTS idx_case_memory_classif_verdict
    ON case_memory(our_classification, vendor_verdict);

CREATE INDEX IF NOT EXISTS idx_case_memory_component
    ON case_memory(vendor_component);

CREATE INDEX IF NOT EXISTS idx_case_memory_hostname
    ON case_memory(hostname, collected_at DESC);

-- Phase 2+3: analysis_problems (cross-session problem tracking)
-- ============================================================

CREATE TABLE IF NOT EXISTS analysis_problems (
    id                  SERIAL PRIMARY KEY,
    problem_id          INT NOT NULL,           -- stable problem key; first row uses same value as id
    title               TEXT,
    fault_type          TEXT,
    prompt              TEXT NOT NULL,
    case_ids            INT[] NOT NULL,
    status              TEXT DEFAULT 'open',    -- open / monitoring / resolved / unresolved / blocked
    diagnosis           JSONB DEFAULT '{}',     -- findings from case-diagnosis
    patch_summary       TEXT,                   -- what was changed in skill files
    patch_commit        TEXT,                   -- git commit hash or branch name
    monitor_expectation TEXT,                   -- what to watch in next feedback cycle
    pr_url              TEXT,                   -- URL of the created PR, when patch workflow files one
    patch_deployed      BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE once the patch has been deployed to production
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_problems_status
    ON analysis_problems(status);
CREATE INDEX IF NOT EXISTS idx_problems_fault_type
    ON analysis_problems(fault_type);

ALTER TABLE analysis_problems
    ADD COLUMN IF NOT EXISTS patch_deployed BOOLEAN NOT NULL DEFAULT FALSE;

-- Auto-set updated_at on every INSERT/UPDATE
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_updated_at ON analysis_problems;
CREATE TRIGGER set_updated_at
    BEFORE INSERT OR UPDATE ON analysis_problems
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Rejected Proposals — records skill changes that were deployed but didn't improve outcomes
-- Prevents the feedback agent from re-proposing the same approach in future cycles.
CREATE TABLE IF NOT EXISTS rejected_proposals (
    id              SERIAL PRIMARY KEY,
    problem_id      INT REFERENCES analysis_problems(problem_id),
    skill_file      TEXT NOT NULL,              -- e.g. "categorization-rules.md", "investigation-methodology.md"
    edit_type       TEXT NOT NULL,              -- add / delete / replace
    edit_summary    TEXT NOT NULL,              -- what the proposal changed (1-2 lines)
    edit_detail     TEXT,                       -- full diff or content of the change
    rationale       TEXT,                       -- why the agent thought this would help
    outcome         TEXT NOT NULL,              -- no_improvement / regression / reverted / superseded
    before_metric   JSONB DEFAULT '{}',         -- {"accuracy": 0.85, "cases": 42, "correct": 36, ...}
    after_metric    JSONB DEFAULT '{}',         -- {"accuracy": 0.82, "cases": 45, "correct": 37, ...}
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),  -- when the bad outcome was observed
    deployed_at     TIMESTAMPTZ,                -- when the change was originally deployed
    note            TEXT                        -- what went wrong, why it didn't help
);

CREATE INDEX IF NOT EXISTS idx_rejected_proposals_skill
    ON rejected_proposals(skill_file, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_rejected_proposals_problem
    ON rejected_proposals(problem_id);

-- ============================================================
-- RMA Finding Reconciliations — links case_memory RMA outcomes
-- back to patrol_findings for feedback-loop labelling.
-- ============================================================

CREATE TABLE IF NOT EXISTS rma_finding_reconciliations (
    case_id                 INT NOT NULL,
    finding_id              INT NOT NULL,
    rule_id                 TEXT NOT NULL,
    repair_outcome          TEXT NOT NULL CHECK (
                                repair_outcome IN (
                                    'REPAIR_CONFIRMED', 'NO_FAULT_FOUND',
                                    'MISCLASSIFIED', 'MAINTENANCE_FIX', 'CONFIG_TASK'
                                )
                            ),
    attribution             TEXT CHECK (
                                attribution IS NULL OR attribution IN (
                                    'detection', 'triage', 'inspect',
                                    'repair', 'automation', 'vendor_uncertain', 'unknown'
                                )
                            ),
    attribution_confidence  TEXT,
    fix_route               TEXT,
    feedback_label          TEXT DEFAULT 'unknown',     -- tp / fp / fn / tn / unknown
    expected_behavior       JSONB DEFAULT '{}',
    label_source            TEXT DEFAULT 'auto',        -- auto / human
    reconciled_at           TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (case_id, finding_id)
);

CREATE INDEX IF NOT EXISTS idx_rma_reconciliations_rule
    ON rma_finding_reconciliations(rule_id, reconciled_at DESC);
CREATE INDEX IF NOT EXISTS idx_rma_reconciliations_attribution
    ON rma_finding_reconciliations(attribution, attribution_confidence);

-- ============================================================
-- Agent Memory — per-agent learned outcomes for actions taken.
-- Managed by agent-feedback MCP (ensure_agent_memory_table).
-- ============================================================

CREATE TABLE IF NOT EXISTS agent_memory (
    id              BIGSERIAL PRIMARY KEY,
    target          TEXT NOT NULL,      -- hostname, job_name, rule_id, etc.
    domain          TEXT NOT NULL,      -- 'node', 'job', 'rule', ...
    action          TEXT NOT NULL,      -- what the agent did
    outcome         TEXT NOT NULL,      -- success / failure / partial
    reason          TEXT,               -- why this outcome occurred
    insight         TEXT,               -- generalised learning for future use
    detail          TEXT,               -- raw detail / evidence
    before_metric   JSONB DEFAULT '{}',
    after_metric    JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_target ON agent_memory(target);
CREATE INDEX IF NOT EXISTS idx_agent_memory_domain ON agent_memory(domain);
CREATE INDEX IF NOT EXISTS idx_agent_memory_insight ON agent_memory(insight) WHERE insight IS NOT NULL;

