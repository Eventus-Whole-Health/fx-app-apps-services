-- ============================================================================
-- Phase 3: Backfill bogus 'error'/'failed' exec-log rows for 202-async services
-- ============================================================================
-- Part of the scheduler async-misclassification fix (GitHub Issue #9).
--
-- Root cause (confirmed via apps_master_services_log):
--   timer_function.py recorded successful 202-async runs as
--   status='error'/'failed' with log_id=NULL after a post-dispatch SQL
--   write raised. job_manager.py filters log_id IS NOT NULL, so these
--   rows can never self-heal. The code fix prevents new ones; this
--   migration reclassifies the historical backlog.
--
-- Matching rule: exec row (status IN ('error','failed'), log_id IS NULL,
-- schedule is 202-async) is paired with a master_services_log row for the
-- same service (hyphen->underscore normalized), status='success', whose
-- started_at is on the same UTC calendar day as the exec triggered_at.
--
-- SCOPE: 202-async schedules only (apps_central_scheduling.last_response_code
-- = 202). Idempotent — only touches rows still status IN ('error','failed')
-- with log_id IS NULL that have a matching success master row.
-- ============================================================================

-- --------------------------------------------------------------------------
-- Step 0: Pre-flight count (informational)
-- --------------------------------------------------------------------------
SELECT COUNT(*) AS false_rows_before
FROM jgilpatrick.apps_scheduler_execution_log el
JOIN jgilpatrick.apps_central_scheduling cs ON el.schedule_id = cs.id
WHERE el.status IN ('error', 'failed')
  AND el.log_id IS NULL
  AND cs.last_response_code = 202;
GO

-- --------------------------------------------------------------------------
-- Step 1: Reclassify matched rows to 'success'
-- --------------------------------------------------------------------------
WITH matched AS (
    SELECT
        el.execution_id,
        msl.log_id        AS true_log_id,
        msl.started_at    AS m_start,
        msl.ended_at      AS m_end,
        ROW_NUMBER() OVER (
            PARTITION BY el.execution_id
            ORDER BY ABS(DATEDIFF(SECOND, el.triggered_at, msl.started_at))
        ) AS rn
    FROM jgilpatrick.apps_scheduler_execution_log el
    JOIN jgilpatrick.apps_central_scheduling cs
        ON el.schedule_id = cs.id
    JOIN jgilpatrick.apps_master_services_log msl
        ON REPLACE(msl.service_name, '_', '-') = el.service_name
       AND msl.status = 'success'
       AND CAST(msl.started_at AS date) = CAST(el.triggered_at AS date)
    WHERE el.status IN ('error', 'failed')
      AND el.log_id IS NULL
      AND cs.last_response_code = 202
)
UPDATE el
SET el.status            = 'success',
    el.http_status_code  = 200,
    el.log_id            = m.true_log_id,
    el.duration_ms       = CASE
                              WHEN m.m_end IS NOT NULL
                              THEN DATEDIFF(SECOND, m.m_start, m.m_end) * 1000
                              ELSE el.duration_ms
                           END,
    el.completed_at      = CASE
                              WHEN m.m_end IS NOT NULL
                              THEN m.m_end
                              ELSE el.completed_at
                           END,
    el.error_message     = NULL,
    el.response_detail   = 'Backfilled by migration 003 (#9): '
                         + 'master log terminal success'
FROM jgilpatrick.apps_scheduler_execution_log el
JOIN matched m ON m.execution_id = el.execution_id AND m.rn = 1
WHERE el.status IN ('error', 'failed')
  AND el.log_id IS NULL;
GO

-- --------------------------------------------------------------------------
-- Step 2: Post-flight count (remaining = genuinely unmatched only)
-- --------------------------------------------------------------------------
SELECT COUNT(*) AS false_rows_after
FROM jgilpatrick.apps_scheduler_execution_log el
JOIN jgilpatrick.apps_central_scheduling cs ON el.schedule_id = cs.id
WHERE el.status IN ('error', 'failed')
  AND el.log_id IS NULL
  AND cs.last_response_code = 202;
GO
