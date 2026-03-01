# Phase 3: Keystone Dashboard - Context

**Gathered:** 2026-02-28
**Status:** Ready for planning

<domain>
## Phase Boundary

React UI in Keystone Platform giving developers full visibility and control over all 43+ scheduled services. Covers service overview, health indicators, execution history, schedule CRUD, manual trigger, and filtering/sorting. Consumes the 7 API endpoints built in Phase 2.

</domain>

<decisions>
## Implementation Decisions

### Service list layout
- Card grid grouped by function app — each function app is a card tile
- Cards arranged alphabetically by function app name
- Each card shows: app name, aggregate health badge, number of scheduled functions, error count
- Click card to expand — reveals inline sub-grid of individual functions from `apps_central_scheduling`
- Sub-grid rows show: function name, status, frequency, last run, next run
- Match sub-grid patterns established in EQuIP dashboard views
- No top-level filtering needed — grouping by function app serves that purpose
- Sort functionality on sub-grid columns (status, last run, etc.)

### History & detail view
- Right-side drawer (slide panel) opens when clicking a function in the sub-grid
- Drawer shows execution history — summary rows by default: status badge, start time, duration, error message
- Click a run to expand and see full request/response payloads
- Payloads displayed as formatted, syntax-highlighted, collapsible JSON tree
- Load last 10 runs by default with "Load more" button for older history

### Schedule editing UX
- Create/edit forms live in the same right-side drawer (reuse pattern from history)
- Frequency set via dropdown presets: hourly, daily, weekly, monthly — with time/day selectors
- No custom cron expression input
- Enable/disable toggle directly inline on the sub-grid row — instant, no form needed
- Delete requires confirmation dialog before soft-delete

### Live status feedback
- Manual trigger shows inline spinner on the row while running, flips to success/failed badge on completion
- Dashboard auto-refreshes via polling interval (30-60 seconds) to catch timer-triggered runs
- Manual trigger polls every 3 seconds for completion status
- Failed status shows status only — no inline retry button; user re-triggers via the standard trigger button

### Claude's Discretion
- Exact polling interval for auto-refresh (30s vs 60s)
- Drawer width and responsive breakpoints
- Loading skeleton design
- Empty state messaging
- Exact color values for health badges (within red/yellow/green scheme)
- Error boundary and error state handling

</decisions>

<specifics>
## Specific Ideas

- Sub-grid pattern should match EQuIP dashboard views (existing pattern in Keystone Platform)
- Card grid layout for function apps — think Azure portal resource overview style
- Drawer pattern reused for both history viewing and schedule editing (consistent interaction model)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-keystone-dashboard*
*Context gathered: 2026-02-28*
