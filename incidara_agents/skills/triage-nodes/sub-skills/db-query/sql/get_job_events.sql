-- Get events for a specific job (by framework name/hash)
-- Usage: db_query.sh this_file.sql frameworkname='32a93e1460546acef6a678b5244d2257'
-- Returns: timestamp, event type, reason, message, source host

SELECT
    "lastTimestamp" AS timestamp,
    type,
    reason,
    LEFT(message, 300) AS message,
    "sourceHost" AS source_host
FROM public.framework_events
WHERE "frameworkName" = :'frameworkname'
ORDER BY "lastTimestamp" DESC
LIMIT 20;
