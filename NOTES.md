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
