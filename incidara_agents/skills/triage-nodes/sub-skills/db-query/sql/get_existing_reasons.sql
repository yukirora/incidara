-- Get all existing triage reasons from node_actions for reference
-- Usage: psql ... -f this_file.sql

SELECT 'triaged_hardware' AS status, reason, COUNT(*) AS usage_count
FROM ltp_sdk.node_actions
WHERE action LIKE '%triaged_hardware'
    AND reason IS NOT NULL AND reason != ''
GROUP BY reason
UNION ALL
SELECT 'triaged_platform' AS status, reason, COUNT(*) AS usage_count
FROM ltp_sdk.node_actions
WHERE action LIKE '%triaged_platform'
    AND reason IS NOT NULL AND reason != ''
GROUP BY reason
ORDER BY status, usage_count DESC;
