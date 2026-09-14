-- Get recent status history for a node
-- Usage: psql ... -v hostname='<hostname>' -f this_file.sql

SELECT hostname, timestamp, status
FROM "ltp_sdk"."node_status"
WHERE hostname = :'hostname'
ORDER BY timestamp DESC
LIMIT 10;
