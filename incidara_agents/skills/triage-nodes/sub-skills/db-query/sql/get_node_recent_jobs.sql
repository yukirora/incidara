-- Get recent jobs that ran on a specific node (via framework_events.sourceHost)
-- Usage: db_query.sh this_file.sql hostname='lg-cmc-...'
-- Returns: job name, state, exit code, completion time, GPU count, exit reason

SELECT DISTINCT
    f."userName" AS user_name,
    f."jobName" AS job_name,
    f.state AS job_state,
    f."subState" AS sub_state,
    f."appExitCode" AS exit_code,
    f."totalGpuNumber" AS gpu_count,
    f."completionTime" AS completion_time,
    COALESCE(js.exit_category, '') AS exit_category,
    COALESCE(LEFT(js.exit_reason, 300), '') AS exit_reason
FROM public.framework_events fe
JOIN public.frameworks f ON f.name = fe."frameworkName"
LEFT JOIN ltp_sdk.job_summary js ON js.job_name = f."jobName"
WHERE fe."sourceHost" = :'hostname'
  AND fe."lastTimestamp" >= (NOW() - INTERVAL '14 days')
ORDER BY f."completionTime" DESC NULLS FIRST
LIMIT 20;
