# Triage: agent/audit-query-param-auth-r1-df8e repo drift

## Task
Drydock flagged: `agent/audit-query-param-auth-r1-df8e` has 2 unmerged
commits, idle 13 days. Proposed action: merge it, or delete it and say so.

## What the branch contains

Two commits on top of `agent/audit-query-param-auth`:
- `892a7be` — audits whether the server-side `?api_key=` query-param auth
  path (`src/mycellm/api/app.py`) can be retired, and adds four
  characterisation tests to `tests/unit/test_security.py` pinning the current
  three-way credential acceptance (`Authorization: Bearer`, `X-API-Key`,
  `?api_key=`).
- `04474e0` — extends that audit to note the shipped compiled dashboard
  bundle as a query-param consumer too.

Both commits are audit-only (no `src/` behaviour change) and append ~145
lines to `NOTES.md`, plus the 58-line test addition.

## Disposition: take the tests, don't merge the branch's NOTES.md

The tests are still accurate and valuable — `src/mycellm/api/app.py` still
accepts all three credential forms today (confirmed by reading
`ApiKeyMiddleware` on `main` and running the cherry-picked tests: 7 passed).
Cherry-picked `tests/unit/test_security.py` verbatim from the branch;
`src/mycellm/api/app.py` needed no change for the tests to pass, since
nothing about the middleware moved.

Not merging the branch's `NOTES.md` additions directly: this repo's
convention (see prior triage entries, e.g. `agent/sse-auth-header`'s) is that
`NOTES.md` is a per-ticket scratch document each worker overwrites, not an
append-only log — `main`'s `NOTES.md` has been reset to a single ticket's
findings several times. A raw merge of the branch's 145-line append would
conflict with that convention and with this triage's own `NOTES.md`. The
branch itself still exists as the durable record of the full audit (consumer
table, recommendation, verification transcript) if anyone needs the detail
later; the essentials are also captured in project memory
(`project-api-key-query-param-audit`), which additionally records that the
audit's blocking prerequisite has since been resolved.

**The audit's own finding is now stale in one respect**: it recommended
retiring `?api_key=` server-side only after fixing `ApiClient.stream()`'s
`EventSource`-based SSE auth, which at audit time (2026-08-01) was the only
in-repo consumer. That fix landed on `main` as `0f97bb3` (2026-08-05) —
`stream()` now uses `fetchWithAuth` with `Authorization: Bearer`, confirmed
here by `grep api_key web/src/api/client.ts` returning nothing. So the
audit's gating condition is satisfied and retiring `?api_key=` server-side
is now unblocked, but that retirement itself is out of scope for this triage
(a behaviour change belongs in its own ticket, not a branch-hygiene pass) and
is left for a human to schedule.

## Not deleted here

Per `.drydock/procedures.md`, worker branches don't push/delete remote refs.
`agent/audit-query-param-auth-r1-df8e`, its base `agent/audit-query-param-auth`,
and its sibling `agent/audit-query-param-auth-r2-ea1e` (confirmed identical to
`main` — already merged, safe to delete) are left for the human to clean up.
