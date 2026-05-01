-- ============================================================================
-- Phase 1: Scheduler Simplification - Additive Migration
-- ============================================================================
-- Part of the scheduler refactor (GitHub Issue #5).
--
-- This migration:
--   1. Backfills NULL max_execution_minutes rows with a default of 30
--   2. Adds a DEFAULT 30 constraint on max_execution_minutes
--   3. Makes max_execution_minutes NOT NULL
--   4. Clears stuck transient state left by the old retry logic
--
-- No columns are dropped. Deprecated columns (retry_count, max_retries,
-- next_retry_at) remain in place and will be removed in Phase 5.
--
-- Safe to re-run (idempotent).
-- ============================================================================

-- --------------------------------------------------------------------------
-- Step 1: Backfill NULLs so the NOT NULL alter won't fail
-- --------------------------------------------------------------------------
IF COL_LENGTH('jgilpatrick.apps_central_scheduling', 'max_execution_minutes') IS NOT NULL
BEGIN
    UPDATE jgilpatrick.apps_central_scheduling
    SET max_execution_minutes = 30
    WHERE max_execution_minutes IS NULL;
END;
GO

-- --------------------------------------------------------------------------
-- Step 2: Add DEFAULT 30 constraint (idempotent)
-- --------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1
    FROM sys.default_constraints dc
    WHERE dc.parent_object_id = OBJECT_ID('jgilpatrick.apps_central_scheduling')
      AND dc.name = 'DF_apps_central_scheduling_max_execution_minutes'
)
BEGIN
    ALTER TABLE jgilpatrick.apps_central_scheduling
    ADD CONSTRAINT DF_apps_central_scheduling_max_execution_minutes
    DEFAULT 30 FOR max_execution_minutes;
END;
GO

-- --------------------------------------------------------------------------
-- Step 3: Make NOT NULL (only safe after Step 1 backfilled all NULLs)
-- --------------------------------------------------------------------------
IF COL_LENGTH('jgilpatrick.apps_central_scheduling', 'max_execution_minutes') IS NOT NULL
BEGIN
    ALTER TABLE jgilpatrick.apps_central_scheduling
    ALTER COLUMN max_execution_minutes INT NOT NULL;
END;
GO

-- --------------------------------------------------------------------------
-- Step 4: Clear stuck transient state from old retry logic
-- --------------------------------------------------------------------------
-- Any active service stuck in 'processing' or 'failed' gets reset to
-- 'pending' with retry columns zeroed out so the new dispatcher treats
-- them cleanly on the next scheduled tick.
-- --------------------------------------------------------------------------
UPDATE jgilpatrick.apps_central_scheduling
SET status = 'pending',
    next_retry_at = NULL,
    retry_count = 0,
    error_message = NULL
WHERE is_active = 1
  AND status IN ('processing', 'failed');
GO
