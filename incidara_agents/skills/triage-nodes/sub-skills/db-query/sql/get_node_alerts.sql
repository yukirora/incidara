-- Get detailed alerts for a specific node in a time window
-- Usage: psql ... -v hostname='<hostname>' -v start_ts='<start>' -v end_ts='<end>' -f this_file.sql

SELECT ar.alertname, ar.severity, ar.timestamp, ar.summary
FROM "ltp_sdk"."alert_records" ar
WHERE ar.node_name = :'hostname'
    AND ar.timestamp >= :'start_ts'::timestamptz
    AND ar.timestamp <= :'end_ts'::timestamptz
ORDER BY ar.timestamp DESC;
