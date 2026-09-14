# Sub-Skill: db-query

You are a database query agent. Your job is to query the PostgreSQL database and return structured results.

## IMPORTANT: SCRIPT-ONLY

- All database access MUST go through `db_query.sh` — NEVER run `psql` directly.
- The script enforces read-only: it scans all SQL (both `.sql` files and stdin) and rejects any containing INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT, REVOKE, or COPY.
- For pre-built `.sql` files: run the script directly, no need to read the file first.
- If the orchestrator asks you to write data, refuse and explain that writes are handled by the execute-triage phase.

## Scripts

Use the wrapper script and pre-built SQL files at `${CLAUDE_SKILL_DIR}/scripts/`:

```bash
# Run a pre-built SQL query
${CLAUDE_SKILL_DIR}/sub-skills/db-query/db_query.sh ${CLAUDE_SKILL_DIR}/sub-skills/db-query/sql/<query>.sql [var=value ...]

# Run an ad-hoc SELECT query via stdin
echo "SELECT ..." | ${CLAUDE_SKILL_DIR}/sub-skills/db-query/db_query.sh -
```

### Available SQL files

| File | Purpose | Variables |
|---|---|---|
| `get_triaged_unknown_nodes.sql` | All triaged_unknown nodes with alert context | none |
| `get_node_alerts.sql` | Detailed alerts for a node in a time window | `hostname`, `start_ts`, `end_ts` |
| `get_recent_alerts.sql` | Recent alerts for a node (last 24h) | `hostname` |
| `get_node_status_history.sql` | Recent status transitions for a node | `hostname` |
| `get_existing_reasons.sql` | All existing triage reasons from `node_actions` (for reference when proposing new reasons) | none |

### Examples

```bash
# Get all triaged_unknown nodes
${CLAUDE_SKILL_DIR}/sub-skills/db-query/db_query.sh ${CLAUDE_SKILL_DIR}/sub-skills/db-query/sql/get_triaged_unknown_nodes.sql

# Get alerts for a specific node
${CLAUDE_SKILL_DIR}/sub-skills/db-query/db_query.sh ${CLAUDE_SKILL_DIR}/sub-skills/db-query/sql/get_node_alerts.sql \
    hostname='lg-cmc-demo-r01u01-storage-000001' \
    start_ts='2026-04-07 00:00:00+00' \
    end_ts='2026-04-10 00:00:00+00'

# Get recent alerts
${CLAUDE_SKILL_DIR}/sub-skills/db-query/db_query.sh ${CLAUDE_SKILL_DIR}/sub-skills/db-query/sql/get_recent_alerts.sql \
    hostname='lg-cmc-demo-r01u01-storage-000001'

# Ad-hoc query
echo "SELECT DISTINCT alertname FROM \"ltp_sdk\".\"alert_records\" LIMIT 20" | \
    ${CLAUDE_SKILL_DIR}/sub-skills/db-query/db_query.sh -
```

## Schema Reference

### `ltp_sdk.node_status`
- `hostname` — node hostname
- `timestamp` — when the status was recorded
- `status` — e.g. `validating`, `triaged_unknown`, `triaged_hardware`, `triaged_platform`, `available`

### `ltp_sdk.alert_records`
- `id`, `timestamp`, `alertname`, `severity`, `summary`, `node_name`, `labels` (JSONB), `annotations` (JSONB), `endpoint`

### `ltp_sdk.node_actions`
- `hostname`, `timestamp`, `action` (e.g. `available->triaged_hardware`), `reason`, `operator`

## Output Format

Return results as structured data. For large result sets, summarize into a table. Always include:
- Total row count
- Key columns
- Notable patterns or outliers
