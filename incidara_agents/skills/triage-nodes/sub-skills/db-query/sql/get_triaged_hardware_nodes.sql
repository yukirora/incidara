-- Get all triaged_hardware nodes with alert context and existing reason
-- Usage: PGPASSWORD=<db-password> psql -h 127.0.0.1 -p 15432 -U root -d openpai -P pager=off -f this_file.sql

WITH latest_triaged_nodes AS (
    SELECT DISTINCT ON (hostname)
        hostname,
        timestamp AS triaged_timestamp,
        status
    FROM "ltp_sdk"."node_status"
    ORDER BY hostname, timestamp DESC
),
available_to_triaged_times AS (
    SELECT
        lt.hostname,
        lt.triaged_timestamp,
        lt.status,
        (
            SELECT MAX(timestamp)
            FROM "ltp_sdk"."node_status" ns
            WHERE ns.hostname = lt.hostname
                AND ns.status = 'validating'
                AND ns.timestamp < lt.triaged_timestamp
        ) AS available_timestamp
    FROM latest_triaged_nodes lt
    WHERE lt.status = 'triaged_hardware'
),
latest_actions AS (
    SELECT DISTINCT ON (hostname)
        hostname,
        reason,
        detail,
        action
    FROM "ltp_sdk"."node_actions"
    WHERE action LIKE '%triaged_hardware'
    ORDER BY hostname, timestamp DESC
)
SELECT
    att.hostname,
    att.triaged_timestamp,
    att.status,
    la.reason AS existing_reason,
    la.detail AS existing_detail,
    COUNT(ar.id) AS alert_count,
    string_agg(DISTINCT ar.alertname, ', ' ORDER BY ar.alertname) AS alert_names,
    string_agg(DISTINCT ar.summary, ' ||| ' ORDER BY ar.summary) AS alert_summaries
FROM available_to_triaged_times att
LEFT JOIN latest_actions la ON la.hostname = att.hostname
LEFT JOIN "ltp_sdk"."alert_records" ar
    ON att.available_timestamp IS NOT NULL
    AND ar.node_name = att.hostname
    AND ar.timestamp >= att.available_timestamp
    AND ar.timestamp <= att.triaged_timestamp
GROUP BY att.hostname, att.available_timestamp, att.triaged_timestamp, att.status,
         la.reason, la.detail
ORDER BY att.triaged_timestamp DESC;
