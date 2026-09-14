-- Get recent alerts for a node (last 24h)
-- Usage: psql ... -v hostname='<hostname>' -f this_file.sql

SELECT ar.alertname, ar.severity, ar.timestamp, ar.summary
FROM "ltp_sdk"."alert_records" ar
WHERE ar.node_name = :'hostname'
    AND ar.timestamp >= NOW() - INTERVAL '24 hours'
ORDER BY ar.timestamp DESC;
