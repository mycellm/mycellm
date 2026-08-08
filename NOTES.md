# Triage: agent/sse-auth-header repo drift

## Task
Drydock flagged: `agent/sse-auth-header` has 1 unmerged commit, and a loop
(`682d723de54c`) still reads "Land agent/sse-auth-header to main — the SSE-auth
P0 fix EXISTS (72365a6) but was never merged." Proposed action: merge it and
close the loop, or record why it's being abandoned.

## Finding: the fix already landed on main, in a better form. Don't merge.

`agent/sse-auth-header`'s one commit is `72365a6` ("fix: send SSE auth via
Authorization header instead of api_key URL param" — replaces the dashboard's
`EventSource` + `?api_key=` query param with a `fetch`-based stream using
`Authorization: Bearer`).

That exact fix is already on `main` as `0f97bb3`, same title, same author,
same date. `0f97bb3`'s own commit message says so explicitly:

> Originally written on agent/sse-auth-header (72365a6) and re-applied onto
> main, which has since refactored the client.

`0f97bb3` isn't a straight cherry-pick — it's an improved re-implementation:
- `stream()` goes through `fetchWithAuth` (introduced by the sibling fix
  `867f973`), so the stream shares the 401→logout path with every other API
  call, instead of `72365a6`'s bare `fetch`.
- Adds `tests/unit/test_sse_auth.py` (209 lines), which `72365a6` never had.
- `useActivityStream.ts` / `useLogStream.ts` on main already use the
  `SseConnection` type end to end.

Confirmed mechanically:
- `git diff main...agent/sse-auth-header` — the branch's only content vs
  main is the CHANGELOG entry, `client.ts`, and the two hooks, all strictly
  older/worse than what's already on main.
- `git merge-tree $(git merge-base main agent/sse-auth-header) main
  agent/sse-auth-header` — conflicts in `CHANGELOG.md` and `client.ts`
  (the branch's pre-refactor `stream()` collides with main's
  `fetchWithAuth`-based one).

Merging `agent/sse-auth-header` now would reintroduce the pre-refactor,
untested implementation and conflict with newer work already on main
(capability-aware model resolution, remote-token-leak fix) for zero net gain.

## Disposition

**Abandoning `agent/sse-auth-header`** — its payload is fully superseded by
`main`@`0f97bb3`. While triaging, checked the other branch names carrying the
same work:

- `agent/land-sse-auth-header` — tip **is** `0f97bb3`; already an ancestor of
  `main`, safe to delete.
- `agent/sse-auth-header-r1-5e51` — already an ancestor of `main`
  (unrelated later tip, `7ac58ae`), safe to delete.
- `agent/sse-auth-header-refresh-r1-42a5` — already an ancestor of `main`
  (unrelated later tip, `2ff9809`), safe to delete.
- `agent/sse-auth-header-refresh` — **not** an ancestor of `main`; carries the
  same superseded pre-refactor `client.ts`/hooks diff as `agent/sse-auth-header`
  plus its own NOTES.md. Same disposition: abandon, don't merge.

Not deleted here — worker branches don't push/delete remote refs per
`.drydock/procedures.md`; this is a record for the human, and the loop
(`682d723de54c`) should be closed as resolved-on-main rather than re-queued.

No code changes were needed; the fix this loop wants is already shipped.
