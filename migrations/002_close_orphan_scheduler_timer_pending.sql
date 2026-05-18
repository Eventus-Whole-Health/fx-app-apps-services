-- ============================================================================
-- Phase 2: Close orphan scheduler_timer 'pending' rows
-- ============================================================================
-- Part of the scheduler logging-resilience fix (GitHub Issues #7, #8).
--
-- Root cause (confirmed via Seq + apps_master_services_log):
--   scheduler_timer uses a two-step log_start (INSERT row as 'pending', then
--   SELECT log_id back by invocation_id). When the SQL Executor API read-
--   times-out on the query-back, the INSERT has already landed but log_id is
--   never obtained, so the row is stranded at status='pending' forever with
--   error_message = NULL. ~110 such rows had accumulated (1/day at ~04:35 UTC
--   plus an acute 2026-04-28/29 burst).
--
-- The code fix (timer_function.py) closes future orphans by invocation_id in
-- the logging except block. This migration closes the historical backlog.
--
-- SCOPE: scheduler_timer ONLY. The systemic equivalent in ServiceLogger
-- (e.g. ~4,359 process-patient rows from other function apps) is explicitly
-- OUT OF SCOPE here and tracked separately.
--
-- Safe to re-run (idempotent — only touches rows still 'pending').
-- ============================================================================

-- --------------------------------------------------------------------------
-- Step 0: Pre-flight count (informational)
-- --------------------------------------------------------------------------
SELECT COUNT(*) AS orphan_rows_before
FROM jgilpatrick.apps_master_services_log
WHERE service_name = 'scheduler_timer'
  AND status = 'pending'
  AND ended_at IS NULL;
GO

-- --------------------------------------------------------------------------
-- Step 1: Mark orphan scheduler_timer 'pending' rows as failed
-- --------------------------------------------------------------------------
-- ended_at is intentionally NOT set here — the status-change DB trigger
-- populates it, matching how normal completion (_log_completion) behaves.
UPDATE jgilpatrick.apps_master_services_log
SET status = 'failed',
    error_message = 'Closed by migration 002: orphaned at status=pending '
                  + 'by SQL Executor timeout during log_start query-back '
                  + '(issues #7/#8).'
WHERE service_name = 'scheduler_timer'
  AND status = 'pending'
  AND ended_at IS NULL;
GO

-- --------------------------------------------------------------------------
-- Step 2: Post-flight count (should be 0)
-- --------------------------------------------------------------------------
SELECT COUNT(*) AS orphan_rows_after
FROM jgilpatrick.apps_master_services_log
WHERE service_name = 'scheduler_timer'
  AND status = 'pending'
  AND ended_at IS NULL;
GO
