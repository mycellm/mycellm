# mycellm — local branch triage

Snapshot taken 2026-07-24 against `main` @ `a977304`
(*deliver: Land fix/capability-role-sync into a merge-candidate branch*).

This is a **report only**. Nothing was deleted, nothing was pushed, and no ref was
force-updated. Every deletion below is a recommendation for the human to execute.

Counts are `git rev-list --left-right --count main...<branch>` — **ahead** = commits on
the branch that `main` does not have, **behind** = commits on `main` the branch does not
have. "merged" means `git merge-base --is-ancestor <branch> main` exits 0, i.e. the
branch tip is already reachable from `main` and its residual diff vs `main` is empty.

---

## Summary

| Branch | Ahead | Behind | Status | Recommendation |
|---|---:|---:|---|---|
| `agent/branch-triage-report` | 1* | 0 | this report's working branch | **keep** until merged, then delete |
| `agent/fix-chat-hscroll-v3` | 0 | 8 | merged into `main` @ `7ba9eb9` | **delete** |
| `agent/triage-3ffc5044` | 0 | 6 | merged into `main` @ `4b6d347` | **delete** |
| `agent/land-capability-role-sync` | 0 | 1 | merged into `main` @ `a977304` | **delete** |
| `main.sync-conflict-20260722-021358-Z4GE43I` | 0 | 5 | merged; stale sync artifact | **delete** |
| `agent/fix-chat-input-hscroll` | 1 | 9 | superseded; ancestor of `agent/land-chat-input-hscroll` | **delete** (after the hscroll call below) |
| `agent/land-chat-input-hscroll.sync-conflict-20260722-021319-Z4GE43I` | 2 | 5 | strict ancestor of `agent/land-chat-input-hscroll` | **delete** (after the hscroll call below) |
| `agent/land-chat-input-hscroll` | 3 | 5 | **unmerged residual delta — decision needed** | **keep** until merge-or-discard is decided |
| `fix/capability-role-sync` | 0 | 8 | merged, but **claimed by an in-flight run** | **KEEP — do not delete** |
| `feat/distributed-training` | 4 | 4 | unmerged, **claimed by an in-flight run** | **KEEP — do not delete** |
| `agent/rebase-distributed-training` | 2 | 4 | rebase staging branch for the above | **keep** while that run is open |
| `origin/main.sync-conflict-20260722-015216-Z4GE43I` | 0 | 9 | remote-tracking sync artifact | **out of scope** — see below |

\* `agent/branch-triage-report` is the branch this report is being written on; its count
is 0 ahead at fork time and 1 ahead once this file is committed.

---

## Safe to delete now — merged, zero residual diff

These four have empty `git diff main...<branch>` output. Deleting them loses nothing.

### `agent/fix-chat-hscroll-v3` — 0 ahead / 8 behind
Merged into `main` at `7ba9eb9`; carried the `min-w-0` Safari horizontal-scroll fix on
the `/chat` input row, which is live on `main` today.
**Delete** — fully absorbed, no unique content.

### `agent/triage-3ffc5044` — 0 ahead / 6 behind
Merged into `main` at `4b6d347`; the accessible-label fix for the chat input and the
send/stop buttons.
**Delete** — fully absorbed, no unique content.

### `agent/land-capability-role-sync` — 0 ahead / 1 behind
Merged into `main` at `a977304`; the merge candidate that landed
`fix/capability-role-sync` plus `ad63076` (*test(security): assert in-code defaults,
ignore local .env*).
**Delete** — it was staging scaffolding and its whole payload is on `main`.

### `main.sync-conflict-20260722-021358-Z4GE43I` — 0 ahead / 5 behind
Tip is `4b6d347`, a plain ancestor of `main`. A tooling artifact from the 2026-07-22 sync
run, not a piece of work.
**Delete** — stale duplicate of an old `main` tip.

---

## The `/chat` input hscroll cluster — one decision, three branches

Three branches carry the same one-line change. The decision below is **one call**; the
other two branches are strict ancestors and follow whatever is decided.

### `agent/land-chat-input-hscroll` — 3 ahead / 5 behind — **DECISION NEEDED**

Residual delta vs `main` is 2 files, +62/−1:

1. **`web/src/components/chat/ChatInput.tsx`** — the real change. `main` already has
   `min-w-0` on the flex-1 textarea (landed via `agent/fix-chat-hscroll-v3`); this branch
   additionally adds **`overflow-x-hidden`** to that same className and reorders the
   utilities to `flex-1 min-w-0 overflow-x-hidden`:

   ```diff
   -  'max-h-24 min-h-[40px] min-w-0 flex-1 resize-none rounded-xl border border-white/10 bg-black',
   +  'max-h-24 min-h-[40px] flex-1 min-w-0 overflow-x-hidden resize-none rounded-xl border border-white/10 bg-black',
   ```

2. **`NOTES.md`** (+61) — a verify-blocked writeup, not product code. It should **not**
   be merged into `main` as-is; if the textarea change is taken, cherry-pick or
   re-commit only the `ChatInput.tsx` hunk.

**This is a genuine merge-or-discard call for the human**, and the only unmerged UI work
left in the tree:

- **Merge it** if the belt-and-braces `overflow-x-hidden` is still wanted on the
  textarea. It is one Tailwind utility, in-convention with the surrounding `cn()` class
  lists, and harmless alongside the `min-w-0` already on `main`.
- **Discard it** if `min-w-0` alone is considered sufficient — that fix is already
  shipped on `main`, so the Safari symptom is addressed and `overflow-x-hidden` is
  strictly redundant hardening.

There is no correct answer derivable from the repo; it depends on whether Safari was
re-tested after `min-w-0` landed. **Keep the branch until that is decided.**

#### The `NOTES.md` blockers are addressed by the gitignore fix

`NOTES.md` records two environmental blockers that stopped `npm run build` on that
branch — neither is a code defect in the branch:

1. The verifier invoked `npm` at the repo root, which has no `package.json` (this is a
   Python project; the web app lives in `web/`). The project's own command is
   `cd web && npm run lint && npm run build`, per `.drydock/procedures.md`.
2. `web/.gitignore:2` is the bare pattern `logs`, which also matches the **source**
   directory `web/src/components/logs/`, so `web/src/components/logs/LogsTab.tsx` — the
   module `web/src/App.tsx:21` imports — was never committed. Clean checkouts fail with
   `TS2307: Cannot find module '@/components/logs/LogsTab'`.

Both are resolved by the gitignore fix `NOTES.md` itself prescribes: narrow
`web/.gitignore`'s `logs` to `/logs/` so it stops matching the source tree, then
`git add -f web/src/components/logs/LogsTab.tsx`. That is a repo-infra fix and correctly
belongs in its own commit, **not** smuggled into the hscroll branch — which is exactly why
the branch is verify-blocked rather than broken.

**Status check as of this snapshot:** that fix has *not* landed yet.
`git show main:web/.gitignore` still has bare `logs` on line 2, `git ls-tree main --
web/src/components/logs/` is empty, and `git check-ignore -v` still reports
`web/.gitignore:2:logs`. So the blockers are *understood and addressed by a known fix*,
but the fix is still pending. **Land the gitignore fix first** — it is a prerequisite for
re-verifying the hscroll branch (and, separately, `main` is un-buildable from a fresh
clone until it lands).

### `agent/land-chat-input-hscroll.sync-conflict-20260722-021319-Z4GE43I` — 2 ahead / 5 behind
A strict ancestor of `agent/land-chat-input-hscroll` (verified with `merge-base
--is-ancestor`). Same one-line `ChatInput.tsx` delta, without `NOTES.md`.
**Delete** — a sync artifact whose content is a subset of the branch above; it adds no
information to the decision.

### `agent/fix-chat-input-hscroll` — 1 ahead / 9 behind
The original one-commit branch (`e16c53f`), also a strict ancestor of
`agent/land-chat-input-hscroll`. It predates `min-w-0` landing on `main`, so its diff is
against an older `ChatInput.tsx`.
**Delete** — superseded twice over; keep only the land branch while the call is open.

---

## Do NOT delete — claimed by in-flight runs

### `fix/capability-role-sync` — 0 ahead / 8 behind — **KEEP**
Tip `f01dada` is already reachable from `main` (merged via `fa791e7` →
`agent/land-capability-role-sync` → `a977304`), so it *looks* like a safe merged-branch
deletion. **It is not.** This branch is the subject of an in-flight run — the merge
candidate is still awaiting a human merge-or-return decision, and deleting the source
branch would strand that review. **Leave it alone until that run closes.**

### `feat/distributed-training` — 4 ahead / 4 behind — **KEEP**
Unmerged; 11 files, +964/−10 vs `main` (federated LoRA averaging prototype, F3). Was
rebased onto 0.6.2 `main` and re-verified on 2026-07-23 (`48d22d2`). An in-flight run
owns this branch pending a CI confirmation and merge decision. **Do not delete.**

### `agent/rebase-distributed-training` — 2 ahead / 4 behind — keep for now
The staging branch for that rebase; its tree is byte-identical to
`feat/distributed-training` (`294420a` both). Redundant *content*, but it is the artifact
the in-flight run produced. **Keep while that run is open**, then delete alongside it once
`feat/distributed-training` is merged or dropped.

---

## Remote-tracking refs — out of scope for this pass

No remote operations were performed and none are recommended here; deleting anything on
`origin` is a human, network-touching action.

### `origin/main.sync-conflict-20260722-015216-Z4GE43I` — 0 ahead / 9 behind
Tip `95acdd1` (*release: 0.6.2*), already an ancestor of `main` — zero residual diff. A
sync-conflict artifact that was pushed to the remote. It is dead weight, but pruning it
means `git push origin --delete`, which is explicitly **out of scope** for this task and
gated to the human. No local action needed; the local read-only copy disappears on its own
with `git fetch --prune` once the remote ref is gone.

### `origin/feat/distributed-training` — 1 ahead / 9 behind (noted for completeness)
Tip `4ae8a85`, the pre-rebase version of the F3 prototype. Superseded locally by the
0.6.2 rebase. Leave it — it is the remote's record of that branch and will be updated by
whatever push finally lands the feature.

---

## Suggested order of operations (for the human)

1. Land the `web/.gitignore` fix (`logs` → `/logs/`, force-add `LogsTab.tsx`) so clean
   checkouts build again.
2. Make the `agent/land-chat-input-hscroll` merge-or-discard call on `overflow-x-hidden`.
3. Delete the four fully-merged branches, plus the two hscroll ancestors once step 2 is
   settled.
4. Leave `fix/capability-role-sync`, `feat/distributed-training`, and
   `agent/rebase-distributed-training` untouched until their runs close.
5. Prune the `origin` sync-conflict ref separately, as a deliberate remote operation.
