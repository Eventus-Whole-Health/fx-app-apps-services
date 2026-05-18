# Scheduler #9 — Async Misclassification Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the scheduler from recording successful 202-async service runs as bogus `error`/`failed` rows in `apps_scheduler_execution_log`, and backfill the historical false rows.

**Architecture:** A 202 + valid `log_id` is the point of no return — the job is dispatched and `scheduler_jobs/job_manager.py` owns reconciliation. The fix extracts the per-service exception-handler decision into a pure, unit-testable helper that records a `dispatched` row (preserving `log_id`) whenever a 202+log_id was already received, instead of an `error` row with `log_id=NULL` that the job manager can never see (it filters `AND el.log_id IS NOT NULL`). A one-time idempotent SQL migration reclassifies the historical false rows against the authoritative `apps_master_services_log`.

**Tech Stack:** Python 3.11, Azure Functions v4, `pytest` (`asyncio_mode=auto`, `pytest-asyncio`), `unittest.mock`, Azure SQL via SQL Executor API, `sqlcmd`.

---

## Background (read before starting)

The defect is confirmed by live DB query. Every day, schedules 25 (`refresh-patient-cache`) and 28 (`wiki-analytics-collector`) get one row in `jgilpatrick.apps_scheduler_execution_log` with `status='error', http_status_code=NULL, log_id=NULL, duration_ms=0` — while `jgilpatrick.apps_master_services_log` shows the same service `success` every run. Two code paths produce false rows:

- **Path A (confirmed, primary):** `functions/scheduler/timer_function.py` per-service `except Exception as e:` handler at **line 779–816**. Its "best-effort execution log" call (≈ line 804–812) passes no `http_status_code`, no `log_id`, and a fresh `triggered_at`, so `log_execution` writes `status='error', http=NULL, log_id=NULL, duration_ms≈0`. An exception in a *post-dispatch* SQL write (e.g. the `apps_central_scheduling` UPDATE at line 673–688, a SQL Executor read-timeout — the same fragility as #7) drops a *successfully dispatched* job into this handler.
- **Path B (latent, secondary):** the `else:` failure branch at line 744–777, reached when `execute_service_request` returns `success=False` — including the "202 Accepted but missing log_id" sentinel at line 462–467. Produces `status='failed', http=500, log_id=NULL`. Not yet DB-confirmed for these services; Task 4 gates the fix on a DB check.

Because `functions/scheduler_jobs/job_manager.py` line 98–100 filters `WHERE el.status = 'dispatched' AND el.log_id IS NOT NULL`, any row written with `log_id=NULL` is invisible to reconciliation forever. This is the true root cause behind data-services #14/#16/#17.

### Repo constraints (do not fight these)

- **`tests/` is gitignored** (`.gitignore:18`). Tests are local-only verification. **Do not `git add tests/`** — it will fail or require `-f`; do not force it. Committed artifacts are only: `functions/scheduler/timer_function.py`, `migrations/003_*.sql`, `CLAUDE.md`.
- **Virtualenv:** `~/venv/fx-app-apps-services` (already exists with `pytest 9`, `pytest-asyncio`). Activate with `source ~/venv/fx-app-apps-services/bin/activate`.
- **Pre-flight commit gate (mandatory):** commit any uncommitted changes before editing anything (Task 0).
- **Deploy:** merging to `main` auto-deploys via GitHub Actions ("Build and deploy ... fx-app-apps-services"). The migration is **not** auto-run — apply it with `sqlcmd` alongside the deploy, exactly like `migrations/002`. Do not push/deploy unless the user has said the passphrase "magnolia".
- `Optional` and `datetime` are already imported in `timer_function.py`; the new helper needs no new imports.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `functions/scheduler/timer_function.py` | Add pure decision helper `_exception_exec_log_args`; add defensive initializers; rewire the per-service exception handler to use the helper | Modify |
| `tests/test_scheduler_exec_log_classification.py` | Unit tests for `_exception_exec_log_args` (local-only, not committed) | Create |
| `migrations/003_backfill_false_async_exec_log_rows.sql` | One-time idempotent reclassification of historical false rows | Create |
| `CLAUDE.md` | Add migration 003 to the Schema Migrations table + Changelog | Modify |

---

## Task 0: Pre-flight — branch and clean tree

**Files:** none (git only)

- [ ] **Step 1: Verify clean tree; commit anything uncommitted**

Run:
```bash
cd /Users/jgilpatrick/Development/active/fx-app-apps-services/.worktrees/scheduler-issues-7-8
git status --porcelain
```
Expected: empty output. If non-empty, STOP and ask the user how to handle it (do not stash). Do not proceed until the tree is clean.

- [ ] **Step 2: Create the feature branch off up-to-date main**

Run:
```bash
git fetch origin -q
git checkout main
git pull --ff-only origin main
git checkout -b fix/scheduler-9-async-misclassification
```
Expected: `Switched to a new branch 'fix/scheduler-9-async-misclassification'`. If `git pull` reports divergence, STOP and ask the user which strategy to use.

---

## Task 1: Pure decision helper + failing test

**Files:**
- Modify: `functions/scheduler/timer_function.py` (add module-level function near the other helpers, e.g. directly above `async def execute_service_request` at line 415)
- Test: `tests/test_scheduler_exec_log_classification.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_scheduler_exec_log_classification.py`:
```python
"""Tests for the per-service exception-handler exec-log classification (#9)."""
from datetime import datetime

import pytz

from functions.scheduler.timer_function import _exception_exec_log_args

EASTERN = pytz.timezone("US/Eastern")
TRIGGERED = EASTERN.localize(datetime(2026, 5, 18, 0, 30, 0))


def test_dispatched_when_log_id_present():
    """A 202+log_id was received before the raise -> record 'dispatched'
    with the log_id so the job manager can reconcile it."""
    args = _exception_exec_log_args(
        log_id=139440,
        exec_triggered_at=TRIGGERED,
        error_msg="HTTPSConnectionPool: Read timed out.",
        json_body='{"x":1}',
    )
    assert args["status"] == "dispatched"
    assert args["log_id"] == 139440
    assert args["http_status_code"] == 202
    assert args["triggered_at"] == TRIGGERED
    assert args["error_message"] is None


def test_error_when_no_log_id():
    """No log_id means nothing was dispatched -> genuine 'error' row."""
    args = _exception_exec_log_args(
        log_id=None,
        exec_triggered_at=TRIGGERED,
        error_msg="claim failed",
        json_body=None,
    )
    assert args["status"] == "error"
    assert args["log_id"] is None
    assert args["http_status_code"] is None
    assert args["error_message"] == "claim failed"


def test_error_msg_truncated_for_error_row():
    """error_message is bounded at 2000 chars on the 'error' path."""
    args = _exception_exec_log_args(
        log_id=None,
        exec_triggered_at=TRIGGERED,
        error_msg="z" * 5000,
        json_body=None,
    )
    assert len(args["error_message"]) == 2000
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
source ~/venv/fx-app-apps-services/bin/activate
cd /Users/jgilpatrick/Development/active/fx-app-apps-services/.worktrees/scheduler-issues-7-8
python -m pytest tests/test_scheduler_exec_log_classification.py -v
```
Expected: FAIL — `ImportError: cannot import name '_exception_exec_log_args'`.

- [ ] **Step 3: Implement the helper**

In `functions/scheduler/timer_function.py`, directly above `async def execute_service_request(` (line 415), insert:
```python
def _exception_exec_log_args(
    *,
    log_id: Optional[int],
    exec_triggered_at: datetime,
    error_msg: str,
    json_body: Optional[str],
) -> dict:
    """Decide how to record an apps_scheduler_execution_log row when the
    per-service block raised AFTER execute_service_request returned.

    If a 202 + log_id was already received, the job IS dispatched — the
    failure was a post-dispatch SQL write (e.g. SQL Executor read-timeout
    on the central-scheduling UPDATE, same fragility as #7). Record it as
    'dispatched' with the log_id so scheduler_jobs/job_manager.py can
    reconcile it to its true terminal state. Recording 'error' with
    log_id=NULL here would strand a successful run as a permanent false
    error, invisible to the job manager (which filters log_id IS NOT NULL).
    See GitHub issue #9.
    """
    if log_id is not None:
        return {
            "status": "dispatched",
            "http_status_code": 202,
            "triggered_at": exec_triggered_at,
            "response_detail": (
                "Dispatched (202); post-dispatch error absorbed: "
                + error_msg[:1000]
            ),
            "error_message": None,
            "log_id": log_id,
        }
    return {
        "status": "error",
        "http_status_code": None,
        "triggered_at": exec_triggered_at,
        "response_detail": None,
        "error_message": error_msg[:2000],
        "log_id": None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
python -m pytest tests/test_scheduler_exec_log_classification.py -v
```
Expected: PASS — 3 passed.

- [ ] **Step 5: Commit (code only — tests are gitignored)**

```bash
git add functions/scheduler/timer_function.py
git commit -m "feat(scheduler): add _exception_exec_log_args decision helper (#9)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Defensive initializers in the per-service loop

**Files:**
- Modify: `functions/scheduler/timer_function.py` (the per-service `try:` at line 589)

Rationale: the per-service `except Exception as e:` at line 779 must be able to read `log_id` and `exec_triggered_at` even if the exception is raised *before* their real assignments (line 662 / line 655). Without this, the handler itself raises `NameError` and the row classification is lost.

- [ ] **Step 1: Add the initializers immediately after the per-service `try:`**

In `functions/scheduler/timer_function.py`, line 589 is `try:` (the first statement of the `for service in services:` body, line 582). The next line (590) is `# Determine if this service should fire now`. Insert two lines between them so it reads:
```python
                try:
                    log_id: Optional[int] = None
                    exec_triggered_at = datetime.now(eastern)
                    # Determine if this service should fire now
```
(The real assignments at line ~655 and ~662 still override these defaults on the normal path. `eastern` is the in-scope `pytz` timezone used throughout this function.)

- [ ] **Step 2: Verify the module still imports**

Run:
```bash
source ~/venv/fx-app-apps-services/bin/activate
cd /Users/jgilpatrick/Development/active/fx-app-apps-services/.worktrees/scheduler-issues-7-8
python -c "import functions.scheduler.timer_function as t; print('import OK')"
```
Expected: `import OK`.

- [ ] **Step 3: Commit**

```bash
git add functions/scheduler/timer_function.py
git commit -m "fix(scheduler): defensively init log_id/exec_triggered_at per service (#9)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Rewire the per-service exception handler to use the helper

**Files:**
- Modify: `functions/scheduler/timer_function.py` (the "Best-effort execution log" block inside the `except Exception as e:` at line ≈ 803–814)

- [ ] **Step 1: Replace the best-effort execution-log call**

In the per-service `except Exception as e:` handler (line 779), the current best-effort block reads exactly:
```python
                    # Best-effort execution log
                    try:
                        await log_execution(
                            sql_client, schedule_id=service_id,
                            function_app=function_app, service_name=service_name,
                            triggered_at=datetime.now(eastern), status="error",
                            request_payload=service.get("json_body"),
                            error_message=error_msg[:2000],
                            trigger_source="timer",
                        )
                    except Exception:
                        pass
```
Replace that entire block with:
```python
                    # Best-effort execution log. If a 202 + log_id was already
                    # received, the job IS dispatched (the failure was a
                    # post-dispatch SQL write) -- record 'dispatched' with the
                    # log_id so the job manager reconciles it. Recording
                    # 'error' with log_id=NULL would strand a successful run as
                    # a permanent false error (GitHub issue #9).
                    try:
                        _exec_args = _exception_exec_log_args(
                            log_id=log_id,
                            exec_triggered_at=exec_triggered_at,
                            error_msg=error_msg,
                            json_body=service.get("json_body"),
                        )
                        await log_execution(
                            sql_client, schedule_id=service_id,
                            function_app=function_app, service_name=service_name,
                            triggered_at=_exec_args["triggered_at"],
                            status=_exec_args["status"],
                            http_status_code=_exec_args["http_status_code"],
                            request_payload=service.get("json_body"),
                            response_detail=_exec_args["response_detail"],
                            error_message=_exec_args["error_message"],
                            trigger_source="timer",
                            log_id=_exec_args["log_id"],
                        )
                    except Exception:
                        pass
```

- [ ] **Step 2: Verify import + full test suite still green**

Run:
```bash
source ~/venv/fx-app-apps-services/bin/activate
cd /Users/jgilpatrick/Development/active/fx-app-apps-services/.worktrees/scheduler-issues-7-8
python -c "import functions.scheduler.timer_function; print('import OK')"
python -m pytest tests/ -v
```
Expected: `import OK`; all tests pass (the new classification tests plus the existing keystone tests — no regressions).

- [ ] **Step 3: Commit**

```bash
git add functions/scheduler/timer_function.py
git commit -m "fix(scheduler): record dispatched (not error) when 202 log_id known (#9)

Post-dispatch SQL failures (SQL Executor read-timeout, same as #7) no
longer strand a successful 202-async run as a false error row with
log_id=NULL that the job manager can never reconcile.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Path B investigation gate (failure-branch / "202 without log_id")

**Files:** none (DB query; conditional follow-up)

Path B is latent and not DB-confirmed for these services. Per YAGNI, confirm it exists before changing code.

- [ ] **Step 1: Query for Path B rows**

Run:
```bash
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -W -s"|" -Q "SET NOCOUNT ON; SELECT TOP 20 el.execution_id, el.service_name, CONVERT(varchar,el.triggered_at,121) trig, el.status, el.http_status_code hsc, el.log_id, LEFT(el.response_detail,60) rd FROM jgilpatrick.apps_scheduler_execution_log el JOIN jgilpatrick.apps_central_scheduling cs ON el.schedule_id=cs.id WHERE el.status='failed' AND el.log_id IS NULL AND el.http_status_code IN (500,408) AND cs.last_response_code=202 ORDER BY el.execution_id DESC"
```

- [ ] **Step 2: Decide**

- If **zero rows**: Path B is not occurring. Document in the PR description: "Path B (202-without-log_id failure branch) produced no rows for 202-async schedules; no code change made — covered structurally by the data-services 202 contract." Skip Steps 3–4. Done with this task.
- If **rows exist**: proceed to Step 3.

- [ ] **Step 3 (only if rows exist): Make the "202 without log_id" sentinel attributable**

In `functions/scheduler/timer_function.py`, line 462–467 currently returns:
```python
            if response_code == 202:
                if log_id is None:
                    LOGGER.error(
                        f"Service {service_id} returned 202 but no log_id in response"
                    )
                    return False, 500, "202 Accepted but missing log_id", None
```
Change the returned response code from `500` to `502` and make the detail explicit so these untrackable dispatches are not confused with generic HTTP-500 service failures:
```python
            if response_code == 202:
                if log_id is None:
                    LOGGER.error(
                        f"Service {service_id} returned 202 but no log_id in response"
                    )
                    return (
                        False,
                        502,
                        "202 Accepted but service returned no log_id — "
                        "untrackable dispatch (#9 Path B)",
                        None,
                    )
```

- [ ] **Step 4 (only if rows exist): Verify + commit**

Run:
```bash
source ~/venv/fx-app-apps-services/bin/activate
cd /Users/jgilpatrick/Development/active/fx-app-apps-services/.worktrees/scheduler-issues-7-8
python -c "import functions.scheduler.timer_function; print('import OK')"
python -m pytest tests/ -v
```
Expected: `import OK`; all tests pass.

```bash
git add functions/scheduler/timer_function.py
git commit -m "fix(scheduler): mark 202-without-log_id as 502 untrackable, not 500 (#9 Path B)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Migration 003 — backfill historical false rows

**Files:**
- Create: `migrations/003_backfill_false_async_exec_log_rows.sql`

`apps_scheduler_execution_log.service_name` uses hyphens (`refresh-patient-cache`); `apps_master_services_log.service_name` uses underscores (`refresh_patient_cache`). The match must normalize on hyphen→underscore and pair each false exec row with a same-service `success` master row whose `started_at` falls on the same UTC calendar day.

- [ ] **Step 1: Write the migration**

Create `migrations/003_backfill_false_async_exec_log_rows.sql`:
```sql
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
```

- [ ] **Step 2: Dry-run the pre-flight SELECT only (do NOT run the UPDATE yet)**

Run only the Step 0 count to confirm the migration targets a sane number of rows before it is applied at deploy time:
```bash
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -W -Q "SELECT COUNT(*) AS false_rows_before FROM jgilpatrick.apps_scheduler_execution_log el JOIN jgilpatrick.apps_central_scheduling cs ON el.schedule_id=cs.id WHERE el.status IN ('error','failed') AND el.log_id IS NULL AND cs.last_response_code=202"
```
Expected (verified 2026-05-18): **~357 rows**, dominated by schedule 28 (`wiki-analytics-collector`, ~321 over 77 days — it fires ~4×/day, not the originally-estimated ~2/day total) plus schedule 25 (`refresh-patient-cache`, ~36). Only schedules 25 & 28 (both legitimate 202-async) are in scope — the count is high because of schedule 28's cadence, not a broad scope clause. If the result is materially different (e.g. thousands, or other schedule_ids appear), STOP and re-check the scope clause with the user before the migration is ever applied.

- [ ] **Step 3: Commit the migration**

```bash
git add migrations/003_backfill_false_async_exec_log_rows.sql
git commit -m "feat(migrations): 003 backfill false 202-async exec-log rows (#9)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Documentation

**Files:**
- Modify: `CLAUDE.md` (Schema Migrations table + Changelog table)

- [ ] **Step 1: Add migration 003 to the Schema Migrations table**

In `CLAUDE.md`, the Schema Migrations table lists `001_...` and `002_...`. Add a third row immediately after the `002` row, matching the existing column layout (`| File | Purpose | Status |`):
```
| `003_backfill_false_async_exec_log_rows.sql` | Reclassify historical bogus `error`/`failed` exec-log rows for 202-async services to `success` against `apps_master_services_log` (#9) | Pending — apply at deploy |
```

- [ ] **Step 2: Add a Changelog row**

In the Changelog table, add a new row at the top of the data rows (most recent first), matching the existing `| Date | Change | Impact |` layout:
```
| 2026-05-18 | Per-service exception handler records `dispatched` (with log_id) instead of bogus `error`/NULL for already-dispatched 202-async jobs; migration 003 backfills history (fixes #9) | Job manager can now reconcile these runs; data-services #14/#16/#17 false errors resolved |
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record #9 fix and migration 003 in CLAUDE.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: PR + deploy + verify (deploy gated on "magnolia")

**Files:** none

- [ ] **Step 1: Push the branch and open a PR (only after the user says "magnolia")**

Do not run this step until the user has explicitly said "magnolia" in a message. Then:
```bash
git push -u origin fix/scheduler-9-async-misclassification
gh pr create --repo Eventus-Whole-Health/fx-app-apps-services \
  --title "fix(scheduler): stop bogus error rows for successful 202-async services (#9)" \
  --body "$(cat <<'EOF'
Fixes #9.

## What
- New pure helper `_exception_exec_log_args` in `timer_function.py`: when the per-service block raises *after* a 202+log_id was received, record `status='dispatched'` with the `log_id` (job manager reconciles it) instead of `status='error', log_id=NULL` (which the job manager can never see — it filters `log_id IS NOT NULL`).
- Defensive `log_id`/`exec_triggered_at` initializers so the handler can't `NameError`.
- Migration `003` reclassifies historical false rows against `apps_master_services_log`.
- Path B handled per Task 4 gate (see PR thread for the DB-check outcome).

## Verification
Post-deploy DB checks below; root cause is the same SQL-Executor-read-timeout fragility as #7 but in the per-service dispatch path, untouched by 95bd1fd.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Confirm CI deploy succeeded**

After merge, run:
```bash
gh run list --repo Eventus-Whole-Health/fx-app-apps-services --branch main --limit 3
```
Expected: the top run is the "Build and deploy ... fx-app-apps-services" workflow for the merge commit, `completed`/`success`.

- [ ] **Step 3: Apply migration 003**

Run (only after deploy success):
```bash
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -i migrations/003_backfill_false_async_exec_log_rows.sql
```
Expected: `false_rows_before` ≈ 357 (the Task 5 Step 2 count). `false_rows_after` will be **N residual, not 0** — Step 1 only matches rows whose master log row is `status='success'`, so exec rows whose underlying run genuinely failed (master row `failed`/`error`) correctly remain `error`/`failed` and are excluded. Report the outcome as "357 → N residual genuine failures", not as a partial migration. A near-0 residual is plausible (these services succeed almost every run) but a non-zero residual is correct behavior, not a failure.

- [ ] **Step 4: Verify next live runs are correct**

After the next scheduled runs (schedule 25 ≈ 03:15 UTC, schedule 28 ≈ 04:30 UTC), run:
```bash
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -W -s"|" -Q "SET NOCOUNT ON; SELECT TOP 6 service_name, CONVERT(varchar,triggered_at,121) trig, status, http_status_code hsc, log_id FROM jgilpatrick.apps_scheduler_execution_log WHERE service_name IN ('refresh-patient-cache','wiki-analytics-collector') ORDER BY execution_id DESC"
```
Expected: newest rows show `status='dispatched'` then reconciled to `success` (by the job manager), each with a non-NULL `log_id` — **no** `status='error', log_id=NULL` rows after the deploy timestamp.

- [ ] **Step 5: Close the downstream data-services issues**

Run:
```bash
for n in 14 16 17; do gh issue close $n --repo Eventus-Whole-Health/data-services --comment "Root cause was fx-app-apps-services #9 (scheduler recorded successful 202-async runs as bogus error rows with log_id=NULL). Fixed and historical rows backfilled via migration 003. The data-services functions were healthy throughout."; done
```
Expected: each issue reports `✓ Closed`.

---

## Self-Review

**1. Spec coverage** (against the #9 plan delivered in conversation + issue #9 body):
- "Preserve `log_id` scope" → Task 2. ✅
- "Reorder/记录 dispatched before downgrade" → realized as the helper returning `dispatched` whenever `log_id` is known (Task 1) + handler rewire (Task 3); the job manager already owns reconciliation, so no physical write-reorder is needed — the classification fix is sufficient and simpler (YAGNI). ✅
- "Exception handler log_id-aware" → Task 3. ✅
- "Disambiguate 202-without-log_id (Path B)" → Task 4 (gated on DB evidence). ✅
- "Data backfill migration" → Task 5 (003). ✅
- "Regression test at a correct seam" → Task 1: the defect is the classification *decision*; extracting it to a pure function is the sharpest deterministic seam (Phase-1 ideal). The handler→helper wiring is verified by Task 3's suite run + Task 7 Step 4 live DB check. This seam choice and the residual (wiring not unit-tested because the surrounding loop has no cheap seam) are documented here intentionally. ✅
- "Close data-services #14/#16/#17" → Task 7 Step 5. ✅
- "Branch off main, pre-flight commit, deploy gate" → Task 0 + Task 7 Step 1. ✅

**2. Placeholder scan:** No "TBD/TODO/handle edge cases/similar to Task N". Every code step shows complete code; every command shows expected output. ✅

**3. Type consistency:** `_exception_exec_log_args` is defined once (Task 1) with keyword-only params `log_id`, `exec_triggered_at`, `error_msg`, `json_body` and is called with exactly those names in Task 3. Returned dict keys (`status`, `http_status_code`, `triggered_at`, `response_detail`, `error_message`, `log_id`) match the keys read in Task 3 and the `log_execution` parameter names (`functions/scheduler/timer_function.py:115–126`). ✅

**Note on `json_body` param:** it is currently unused by the helper body (the handler passes `service.get("json_body")` directly to `log_execution`). It is retained in the signature so the decision function has full context if Path B later needs request-payload-aware branching; this is a deliberate, single-call-site interface choice, not a placeholder. If the executing engineer prefers strict YAGNI, dropping the `json_body` param and its test argument is a safe, isolated change.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-18-scheduler-9-async-misclassification.md`.**
