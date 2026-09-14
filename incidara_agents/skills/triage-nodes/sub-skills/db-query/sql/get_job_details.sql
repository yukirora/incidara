-- Get job details by job name (supports partial match via LIKE)
-- Usage: db_query.sh this_file.sql jobname='%Z6OV1MaQ%'
-- Returns: job state, exit code, timestamps, GPU count, exit reason

SELECT
    f."userName" AS user_name,
    f."jobName" AS job_name,
    f.state AS job_state,
    f."subState" AS sub_state,
    f."appExitCode" AS exit_code,
    f."totalGpuNumber" AS gpu_count,
    f."submissionTime" AS submitted,
    f."launchTime" AS launched,
    f."completionTime" AS completed,
    f.retries,
    f."platformRetries" AS platform_retries,
    f."userRetries" AS user_retries,
    -- Task-level status from snapshot (framework state can be misleading)
    (snapshot::json->'status'->'attemptStatus'->'taskRoleStatuses'->0->'taskStatuses'->0->'retryPolicyStatus'->>'totalRetriedCount') AS task_retries,
    (snapshot::json->'status'->'attemptStatus'->'taskRoleStatuses'->0->'taskStatuses'->0->'attemptStatus'->'completionStatus'->>'code') AS task_exit_code,
    (snapshot::json->'status'->'attemptStatus'->'taskRoleStatuses'->0->'taskStatuses'->0->'attemptStatus'->'completionStatus'->'type'->>'name') AS task_completion_type,
    (snapshot::json->'status'->'attemptStatus'->'taskRoleStatuses'->0->'taskStatuses'->0->'attemptStatus'->'completionStatus'->>'phrase') AS task_completion_phrase,
    (snapshot::json->'status'->'attemptStatus'->'taskRoleStatuses'->0->'taskStatuses'->0->'attemptStatus'->>'containerNodeName') AS task_node,
    (snapshot::json->'status'->'attemptStatus'->'taskRoleStatuses'->0->'taskStatuses'->0->'attemptStatus'->>'containerLog') AS task_container_log,
    COALESCE(js.exit_category, '') AS exit_category,
    COALESCE(LEFT(js.exit_reason, 500), '') AS exit_reason
FROM public.frameworks f
LEFT JOIN ltp_sdk.job_summary js ON js.job_name = f."jobName"
WHERE f."jobName" LIKE :'jobname'
ORDER BY f."completionTime" DESC NULLS FIRST
LIMIT 5;
