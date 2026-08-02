# Ticket: land `feat/distributed-training` as a conflict-free merge candidate

**Outcome: `feat/distributed-training` merged cleanly into a branch forked from
`main`. 3 conflicts resolved by hand (documented below); everything else
auto-merged. Merging this branch into `main` itself is still the human's call —
not done here.**

## Why this needed a human-reviewed merge instead of a fast-forward

`main` had already picked up part of F3 independently (the `TRAIN_RESULT`
envelope type, `node.py`'s `train.status` command, and the F2-era
`training/aggregate.py`/`round.py` core) via earlier landed branches, while
`feat/distributed-training` grew its own copy of the same files plus the new
`training/session.py` QUIC wiring. Same files, independent history → git saw
three `add/add` conflicts and one content conflict. None of them are logic
conflicts — in every case one side is a strict superset of the other.

## The 3 conflict resolutions

### 1. `src/mycellm/training/__init__.py`, `aggregate.py`, `round.py` (add/add)

Diffed `main`'s copy against `feat/distributed-training`'s copy directly
(`git show main:<path>` vs `git show feat/distributed-training:<path>`) rather
than trusting the conflict markers, since add/add conflicts don't show a common
ancestor. Confirmed `feat/distributed-training`'s versions are strict supersets:

- `aggregate.py`: same file, plus `_validate_shapes` renamed to the public
  `validate_update` (needed by `session.py`'s coordinator to validate an
  update on arrival, not just inside `federated_average`).
- `round.py`: same file, plus `build_train_result_payload` /
  `parse_train_result_payload` for the `TRAIN_RESULT` wire payload.
- `__init__.py`: re-exports the above plus `training/session.py`'s
  `LocalTrainer`, `LocalUpdate`, `RoundOutcome`, `TrainingCoordinator`,
  `TrainingParticipant`.

Resolved with `git checkout --theirs` (`theirs` = `feat/distributed-training`,
the merge parent) for all three files — no manual splicing, since the whole
file is a superset. `training/codec.py` and `tests/unit/test_training_aggregate.py`
were byte-identical on both sides and auto-merged with no conflict.

### 2. `tests/unit/test_security.py` (content conflict)

Both branches independently fixed the same host-config-leakage hermeticity gap
(the module reads `~/.config/mycellm/.env`, so an operator's real
`MYCELLM_QUIC_HOST=0.0.0.0` was leaking into "assert the shipped default"
tests), but with different mechanisms:

- `main`: fixed at the `tests/conftest.py` / `tests/unit/conftest.py` layer —
  an autouse fixture scrubs `MYCELLM_*` and neutralises the XDG `.env` for
  every test in the suite, so individual test files stay untouched
  (`MycellmSettings(_env_file=None)`, same as before hermeticity was a concern).
- `feat/distributed-training`: fixed locally in this one file with a
  per-file `defaults` fixture + `monkeypatch`.

Per the acceptance criteria, hermeticity belongs in the autouse conftest
fixtures, not scattered per-file. Resolved by taking `main`'s file verbatim
(overwrote with `git show main:tests/unit/test_security.py`) — confirmed with
`git diff main -- tests/unit/test_security.py` (empty). `feat`'s duplicate
fixture is dropped; the conftest-level fixture already covers this file.

### 3. `src/mycellm/node.py`, `protocol/envelope.py`, `transport/messages.py` (auto-merged, no markers)

No conflict, but worth recording what came from where since these are in the
F3 acceptance criteria: `envelope.py`'s `TRAIN_RESULT` enum member and
`node.py`'s `train.status` command were already on `main` (landed ahead of
this merge). The merge only added `transport/messages.py`'s `train_result()`
builder — `feat/distributed-training`'s only new content in that file, needed
by `training/session.py`'s `TrainingCoordinator._publish_result`.

`training/session.py` is intentionally NOT wired into `node.py`'s peer-message
dispatch — its own module docstring says hooking it up is "a single dispatch
arm" left for later, and real on-device training, adapter distribution over
the F2 chunk transport, and credit receipts are all explicitly deferred. This
lands the tested foundation, not the wiring.

## Verification run in this worktree

| Command | Result |
| --- | --- |
| `git merge-base --is-ancestor main HEAD` | pass (fast-forwardable) |
| `git diff --name-only --diff-filter=D main..HEAD` | empty (no file deleted) |
| `git diff main -- tests/unit/test_security.py` | empty |
| `grep ruff pyproject.toml` | `ruff>=0.15,<0.16` kept, comment intact |
| `git grep '^<<<<<<<'` (src/tests/docs/examples/pyproject.toml) | no matches |
| `ruff check src tests` | All checks passed |
| `pytest tests/unit tests/integration tests/e2e/test_harness.py -q` | 682 passed, 2 skipped |

The two tool commands are run as `.venv/bin/ruff` / `.venv/bin/python`, so they
need a venv **inside this worktree** — a git worktree does not inherit the
`.venv/` of the checkout it was branched from (and pointing at the parent
checkout's editable install would import `mycellm` from *that* tree's `src/`,
not this branch's). Recreate it with:

```bash
python3 -m venv .venv
PIP_USER=0 .venv/bin/pip install -e ".[dev,training]"   # PIP_USER=0: the host sets PIP_USER=1, which pip refuses inside a venv
```

`.venv/` is gitignored, so this is worktree setup, not a tracked change. The
`training` extra is what supplies numpy; without it the F3 tests would silently
`importorskip` away rather than run.

## Not done here (human's call)

Merging `agent/land-distributed-training` into `main`, pushing, tagging, or
bumping the version. `main` is already 29 commits ahead of `origin/main` and
unpushed, and per project procedures workers don't merge/deploy — this branch
is staged as a fast-forward-ready candidate for the human to merge.

## Dependency-roll baseline (2026-07-30, chore/deps-2026-07)

Stack: mlx 0.32.0, mlx-lm 0.31.3, mlx-vlm 0.6.8, llama-cpp-python 0.3.34,
cbor2 6.1.3 (arm64) / 5.8.0 pinned locally (Intel cp314 has no 6.x wheel).
Unit suite: 654 green on hokulea (arm64+MLX), 652+2 skipped on Intel dev box.

bench_mlx_batching.py, hokulea M1 16GB, Qwen3-1.7B-4bit, max_tokens=80:

  conc |  seq tok/s | batch tok/s | speedup
     1 |       48.6 |        49.9 |   1.03x
     2 |       47.6 |        72.2 |   1.52x
     4 |       47.0 |        76.0 |   1.62x
     8 |       47.2 |        68.7 |   1.45x
    16 |       47.4 |       114.3 |   2.41x

Live hokulea venv rolled to same stack 2026-07-30; node restarted healthy
(no local model was enabled before or after — fleet/relay serving only).

## Context calibration (2026-07-30, hokulea M1 16GB, Qwen3-1.7B-4bit)

scripts/bench_context_calibration.py: measured_kv ≈ 0.66GB + 0.87 × predicted_kv
(ctx 2k–16k). The preflight v2 analytical model is slightly conservative per
token; the miss is a ~0.66GB CONSTANT transient, covered by the 1.0GB
preflight_overhead_gb reserve → kv_factor stays 1.0 on this hardware. Never
calibrate from small-ctx ratios (they're dominated by the constant).

Chunked prefill knob (mlx_prefill_step_size): 16k prompt on Qwen3-1.7B-4bit,
step 2048 → peak 3.09GB/61.9s; step 512 → 3.04GB/63.9s. Small win on a small
model — transient scales with hidden size, so the knob is for big models near
the ceiling. Default 0 (mlx-lm's 2048).

## Speculative decoding measurements (2026-07-30, hokulea M1 16GB)

mlx-lm 0.31.3 draft-model speculative decoding, num_draft_tokens=3, greedy:
- Qwen3-1.7B-4bit + Qwen3-0.6B draft: 31.5 vs 51.3 tok/s baseline (63% accept) — SLOWER
- Qwen3-8B-4bit + Qwen3-0.6B draft: 9.9 vs 12.5 tok/s baseline (58% accept) — SLOWER
- Qwen3.5-9B (hybrid/ArraysCache): unsupported upstream — "requires a trimmable
  prompt cache" (linear-attention layers can't rewind).

Conclusion: two-model drafting does not pay on M1-class hardware with mlx-lm
0.31.3; oMLX's wins come from MTP-head drafting (Lightning MTP), which needs
MTP checkpoints and custom kernels. Feature is opt-in (`draft_model` load
option), correct, and left off by default. Revisit on M3/M4 nodes or when
mlx-lm grows MTP support.

## Ticket: stop the dashboard sending the local admin key to remote node origins

**Outcome: fixed. `remote()` in `web/src/api/client.ts` now POSTs to a new
same-origin route, `POST /v1/node/proxy`, instead of `fetch()`-ing the
target node's origin directly. The daemon relays the request server-side.**

### The leak

`ModelTable`, `LocalFileLoader`, `ModelBrowser`, `VariantTable`, and
`ApiProviderForm` all let an admin pick another fleet device from
`DeviceTable` (`selectedDevice`, sourced from `node_registry.api_addr` via
`/v1/admin/nodes` + `/v1/node/fleet/hardware`) and call `remote(nodeAddr,
path, opts)` to manage models on it. The old `remote()` did a direct
cross-origin `fetch()` to `http://{nodeAddr}` and attached
`getHeaders()` — the browser session's own `Authorization: Bearer
<local api_key>`. Since `nodeAddr` is whatever `api_addr` a peer
self-reported in its announce (`POST /v1/admin/nodes/announce`, see
`admin.py::announce_node`), a malicious or compromised fleet member could
announce an attacker-controlled `api_addr` and, once approved, harvest the
admin's real dashboard credential the next time the admin opened that
device's Model tab.

### The fix

`remote()` now calls `this.post(API.node.proxy, {node_addr, path, method,
body})` — same-origin, so the existing `getHeaders()`/AuthMiddleware
handshake is between the browser and *this* daemon only, same as every
other `api.*` call. The new handler, `proxy_to_node` in
`src/mycellm/api/node.py`, does the actual outbound hop:

1. Rejects if `node_addr` isn't `api_addr` on an entry with `status ==
   "approved"` in `node.node_registry` — the same trust boundary
   `openai.py`'s fleet HTTP routing (`_route_via_fleet`,
   `_stream_via_fleet_http`) already relies on for inference forwarding.
2. Builds the outbound request with only `Content-Type: application/json`
   — no `Authorization` header, and specifically never the local
   `settings.api_key` or the caller's own Bearer token.

### Outbound-credential decision — settled from the acceptance criteria + code, no open question

The acceptance criteria for this ticket is explicit and unambiguous: the
test must assert the local api_key is *not* in the outbound headers the
proxy builds. That fully decides the question on its own, so this wasn't a
"guess vs. stop" judgment call — but it's worth recording why that's also
the right call architecturally, since `openai.py` already forwards
`settings.api_key` (this node's *own* configured key) to fleet peers for
inference routing (`route_inference` → `_route_via_fleet` /
`_stream_via_fleet_http`, both do `if settings.api_key: headers["Authorization"]
= f"Bearer {settings.api_key}"`). That pattern only works because it's
server-to-server: two nodes an operator configured with the *same* shared
`MYCELLM_API_KEY` trusting each other for inference. The dashboard proxy is
a different trust shape — it's forwarding a *specific browser session's*
authenticated action, and node_registry entries don't store any
credential for the target node (`announce_node` never persists one). There
is no key on hand that's known to be valid for the target, and forwarding
the local one would be exactly the leak this ticket exists to close (it's
also the *browser's* key, not a value this handler should ever see reused
outbound). So: no Authorization header at all on the proxied request. If a
target node has its own `MYCELLM_API_KEY` set, its proxied dashboard calls
will 401 from the target's own `ApiKeyMiddleware` — a real product gap
(no shared credential store for fleet-to-fleet dashboard management), but
out of scope here per the ticket (review items 2-17, and no new
credential-storage design was asked for). Left as-is rather than guessed at.

### Left untouched (out of scope)

- `openai.py`'s existing `settings.api_key`-forwarding for inference
  routing — different code path, different trust model (shared fleet
  key), not part of this ticket's scope list.
- `admin.py`'s QUIC `fleet_command` relay — explicitly out of scope.

Verify commands run in this worktree: `ruff check src tests` (clean),
`pytest -q` (747 passed, 2 skipped — matches main's baseline), and `cd web
&& npm ci && npm run lint && npm run build` (all clean). The web build
regenerates `src/mycellm/web/assets/*` with new content hashes; per
`git log -- src/mycellm/web/assets`, those compiled assets are only
refreshed at release time, not on every `web/src` change (last touched at
`6cf24d8`, long before several unrelated `web/src` commits) — so the
rebuilt output was reverted after verifying the build succeeds, rather than
committed here.

## Ticket: Stop api.remote() forwarding the local node key to remote origins (dispatched again, already resolved)

**Outcome: no code change. This ticket describes exactly the leak fixed
above (commits `867f973`/`6815478`, already on `main`), down to matching
prose in the ticket's own acceptance criteria. `agent/remote-proxy-no-token-forward`
was forked from `main` after that fix landed, so `HEAD` here is bit-identical
to `main` before I touched anything (`git diff main --stat` was empty).**

Re-verified independently rather than trusting the prior commit message:
`grep _PUBLIC_PATHS src/mycellm/api/app.py` confirms `/v1/node/proxy` isn't
public, so it sits behind `AuthMiddleware` like every other `/v1` route;
`proxy_to_node` in `src/mycellm/api/node.py` only relays to `node_addr`
values matching an `approved` entry in `node.node_registry` (not an
arbitrary caller URL) and builds the outbound request with only
`Content-Type`, never the local `api_key`; `remote()` in
`web/src/api/client.ts` calls `POST /v1/node/proxy` same-origin instead of
`fetch()`-ing the target node directly, with all 8 call sites unchanged.
Reran all three verify commands fresh in this worktree: `ruff check src
tests` clean, `pytest tests/unit tests/integration -q` → 736 passed, 2
skipped, and `cd web && npm ci && npm run lint && npm run build` all clean
(rebuilt web assets reverted, same as above).

Two differences from this ticket's stated scope, neither functional: the
existing test file is `tests/unit/test_node_proxy.py` rather than the
`tests/unit/test_remote_proxy.py` named in scope, and the prior fix also
touched `web/src/api/endpoints.ts`, `CHANGELOG.md` and `NOTES.md`, which
aren't in this ticket's touch-list. Adding a second, differently-named test
file or a second changelog entry for the same fix would be pure churn, so I
left the tree as-is rather than manufacture a diff. This reads as the same
ticket dispatched twice (concurrent or re-queued Drydock runs) — worth
deduping the ticket rather than re-running this branch again. Flagging per
`.drydock/procedures.md`'s STOP-and-document rule for ambiguous/duplicate
work rather than guessing.

## Environment: `.venv` bootstrap in fresh worktrees (verify-run failure)

The previous verify run on this branch failed with `exit 127` on
`.venv/bin/ruff check src tests` — not a code defect. `.venv/` is gitignored
(`.gitignore:1`), so `git worktree add` produces a tree with no interpreter,
and the verify commands invoke `.venv/bin/ruff` / `.venv/bin/python` by path.
Earlier runs on this branch passed their checks against the *system* `ruff`
and `pytest` on `PATH` (`~/.local/bin`), which masked the gap.

Bootstrapping it surfaced a second trap worth recording, since it fails
silently: a global pip config sets `user = true`, so `pip install -e ".[dev]"`
inside the venv aborts with

    ERROR: Can not perform a '--user' install. User site-packages are not
    visible in this virtualenv.

**while still exiting 0** — leaving an empty `.venv` that reproduces the exact
same `exit 127` on the next command. `PIP_USER=0` is required. Added to
`CLAUDE.md`'s Development block so the next worktree doesn't rediscover it.

With the venv built, all four checks pass here against the code already on
this branch (no source change was needed):

- `.venv/bin/ruff check src tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/unit tests/integration -q` → **695 passed,
  4 skipped** (`tests/unit/test_node_proxy.py` → 4 passed)
- `cd web && npm ci && npm run lint` → clean
- `cd web && npm run build` → built in 2.52s

Re-confirmed the hard requirement directly rather than inferring it from the
diff: every `fetch()` in `web/src` that attaches an `Authorization` header
targets `window.location.origin` (`client.ts:8` `getBaseUrl()`, the only
header-bearing path at `client.ts:26`), and `remote()` no longer constructs a
remote origin at all. The rebuilt `src/mycellm/web/assets/*` was reverted
again for the reason documented in the section above — those compiled assets
already lag several unrelated `web/src` commits and are refreshed at release
time, so rebuilding them here would fold three unrelated UI commits into this
one. **That does mean the committed dashboard bundle still contains the
pre-fix `remote()`; it needs the normal release rebuild before the fix reaches
anyone installing from the repo.** Flagging rather than widening scope.

## Ticket: salvage and document the reverted working tree in the main checkout

**Outcome: the main checkout at `/data/projects/mycellm/app` was found dirty —
`HEAD` (`2ff9809`) is unchanged, but the index+worktree net out to a partial
revert of the token-leak fixes that had just landed on `main`. Captured
non-destructively with `git stash create` and pinned to a ref so the state
survives even if something later cleans the tree. The main checkout and the
`main` ref were not written to — no `git add`, `restore`, `stash push/pop`,
`clean`, `reset`, or `checkout --` was run against them, only read-only
inspection (`status`, `diff`, `log`, `show`, `ls-tree`, `ls-files`) plus the
non-mutating `stash create` (which does not touch the index/worktree/refs by
itself) and `update-ref` (which only wrote a new, previously-unused ref).**

### What's dirty, and against what

`git diff HEAD --stat` in `/data/projects/mycellm/app` (captured verbatim):

```
 CLAUDE.md                           |  13 +---
 NOTES.md                            |  75 --------------------
 tests/unit/test_node_proxy.py       | 132 ------------------------------------
 web/src/api/client.ts               |  32 ++-------
 web/src/components/chat/ChatTab.tsx |  41 ++++-------
 5 files changed, 19 insertions(+), 274 deletions(-)
```

Three other files (`CHANGELOG.md`, `src/mycellm/api/node.py`,
`web/src/api/endpoints.ts`) show as staged-and-modified in `git status`
(`MM`), but their staged change and unstaged change net to zero against
`HEAD` (`git diff HEAD -- <path>` is empty for all three) — the working tree
still matches `HEAD` for those files' actual content, so they're omitted from
the `--stat` above and don't need restoring.

`tests/unit/test_node_proxy.py` is a special case: it's staged as deleted
(`git ls-files -s` has no entry) while an untracked file with the same name
and content still sits on disk (`git status` shows both `D` and `??` for it).
`git diff HEAD` correctly reports it as a full deletion since it ignores the
untracked copy, but the bytes aren't actually gone from the worktree.

### What this reverts

The dirty state undoes the file-level changes introduced by three commits
already on `main`'s history at `HEAD`:

- **`2ff9809`** (merge, current `HEAD`) — "Stop api.remote() forwarding the
  local node key to remote origins" — touched `CLAUDE.md` + `NOTES.md`.
- **`b6e8751`** / its pre-merge equivalent **`180d3a4`** — "Route ChatTab
  through the authenticated client and make cancel abort the retry backoff"
  — touched `web/src/api/client.ts` + `web/src/components/chat/ChatTab.tsx`
  (identical file list/stat on both, confirming `b6e8751` is `180d3a4`
  landed through the merge).
- **`fb9f3f7`** — "docs: record .venv bootstrap (incl. PIP_USER=0) that
  blocked verification" — touched `CLAUDE.md` + `NOTES.md` (merged into
  `2ff9809` alongside `180d3a4`).

Confirmed by diffing each commit's own `--stat` against the paths in the
dirty diff above — same files, same line counts, opposite sign. `git log
--graph` also confirms `2ff9809` is a merge commit with `180d3a4` and
`fb9f3f7` as its two branches into `main`.

### Salvage

Snapshotted via `git stash create` (does not touch the index, worktree, or
`HEAD`, and — unlike `git stash push` — does not create a stash-list entry
either):

```
$ git stash create
105388c0d8849d513daa1eeb9f9e509186143476
$ git update-ref refs/salvage/main-worktree-20260731 105388c0d8849d513daa1eeb9f9e509186143476
```

Ref name: **`refs/salvage/main-worktree-20260731`** — resolves to commit
`105388c0`, a snapshot of the exact index+worktree state described above,
parented on `2ff9809` (`HEAD`). `main` and the main checkout were re-verified
byte-for-byte unchanged immediately after (`git status --short` identical
before/after, `git rev-parse HEAD` still `2ff9809`).

### Restoring (human decision, not performed here)

The human can bring the reverted content back into the main checkout's
worktree+index with:

```
git restore --source=HEAD --staged --worktree -- CLAUDE.md NOTES.md tests/unit/test_node_proxy.py web/src/api/client.ts web/src/components/chat/ChatTab.tsx
```

That restores from `HEAD` (`2ff9809`, where these fixes already live) rather
than from the salvage ref — the salvage ref exists so the *current* dirty
state isn't lost if someone cleans first, not as the restore source. If the
dirty state itself turns out to be desired (e.g. it's someone's in-progress
revert, not accidental damage), the salvage ref is what to recover instead:
`git stash apply refs/salvage/main-worktree-20260731`.

### Not done here (human's call, per `.drydock/procedures.md`)

Cleaning the main checkout — running any of `git checkout -- .`,
`git restore` (without the human choosing a direction first), `git stash
push/pop`, `git clean`, or `git reset` against
`/data/projects/mycellm/app` — was **not performed**. `.drydock/procedures.md`
gates outward-facing/irreversible actions to explicit human decision, and
this repo's own binding rule set (HARD STOP: never perform an irreversible
action on a throwaway branch) applies with more force to the *canonical*
checkout, which isn't a throwaway branch at all. Whether the dirty state is
accidental damage to discard or an in-progress edit to keep is not
determinable from the tree alone, so the choice — and the `git restore` /
`git stash apply` command above — is left for a human to run.
