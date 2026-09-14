-- Get all triaged_unknown nodes with alert context
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
    WHERE lt.status = 'triaged_unknown'
)
SELECT
    att.hostname,
    att.triaged_timestamp,
    att.status,
    COUNT(ar.id) AS alert_count,
    string_agg(DISTINCT ar.alertname, ', ' ORDER BY ar.alertname) AS alert_names,
    string_agg(DISTINCT ar.summary, ' ||| ' ORDER BY ar.summary) AS alert_summaries
FROM available_to_triaged_times att
LEFT JOIN "ltp_sdk"."alert_records" ar
    ON att.available_timestamp IS NOT NULL
    AND ar.node_name = att.hostname
    AND ar.timestamp >= att.available_timestamp
    AND ar.timestamp <= att.triaged_timestamp
GROUP BY att.hostname, att.available_timestamp, att.triaged_timestamp, att.status
ORDER BY att.triaged_timestamp DESC;
