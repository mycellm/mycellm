# mycellm — merge-readiness memo: `feat/distributed-training` refreshed against `main`

Branch: `agent/refresh-distributed-training`
Base: `main` @ `cd90444` (*deliver: Per-bootstrap backoff for failing HTTP announces*), version **0.6.2**
Source: `4ae8a85` — *feat(training): federated LoRA averaging prototype (F3)*
Snapshot: 2026-07-24

**Report + refreshed branch only.** Nothing was pushed, no ref was force-updated, no
version bump, and `main` was not merged into. The merge decision is the human's.

---

## 1. Rebase result

`git cherry-pick -x 4ae8a85` onto `cd90444` applied **cleanly — zero conflicts**.
`src/mycellm/node.py` auto-merged (main has since moved that file by +79 lines across
the capability-role-sync, stream-failover and announce-backoff landings, but none of
those touch the fleet-command allowlist or `_execute_fleet_command`'s tail). The
remaining 9 files are all additions or additive hunks, so nothing on main contested
them.

Resulting commit: `c5b4c2d` — same 939 insertions / 10 files as the original.

### What was deliberately dropped

The prior staging branch `agent/rebase-distributed-training` carried a second commit,
`1ab1052` *test(security): assert shipped defaults, not the host's node config*. It is
**not** carried here: main already landed an equivalent fix in `ad63076`
*test(security): assert in-code defaults, ignore local .env*, which solves the same
env-leak (both make the three default assertions independent of the operator's
`.env`). Per the "resolve in favour of main's 0.6.2 behaviour" rule, **main's version
stands** and the branch's is discarded.

> Follow-up worth noting (not fixed here — out of scope): main's `ad63076` passes
> `_env_file=None`, which skips the `.env` files but **not** exported `MYCELLM_*`
> environment variables. The dropped `1ab1052` additionally `monkeypatch.delenv`'d
> them. On a seeder that exports `MYCELLM_QUIC_HOST=0.0.0.0` in the shell,
> `test_default_host_is_localhost` can still fail. Tests pass here; flagging it as a
> latent flake on main, unrelated to training.

### One unrelated lint fix

`ruff check src tests` fails on **unmodified main** with `F401 'time' imported but
unused` in `tests/unit/test_api_auth_lockout.py` (ruff 0.15.7 — newer than whatever
last passed CI). Removed the dead import so the verify command is green. This is a
one-line, no-behaviour change in a file the training work never touches.

---

## 2. Test results

Run in this worktree (`python -m pytest`, `ruff check src tests`):

| Check | Result |
|---|---|
| `pytest` (full suite) | **680 passed**, 0 failed, 41.8 s |
| `pytest tests/unit/test_training_aggregate.py` | **28 passed**, 0.21 s |
| `ruff check src tests` | **All checks passed** |
| `python examples/federated_demo.py` | runs; loss **9.50407 → 0.00037** over 40 rounds, 5 peers, final error vs target `0.0020` |

Both convergence proofs hold on the refreshed base:
`test_federated_training_converges` (4 workers, private unevenly-sized shards → global
least-squares solution) and `test_federated_matches_centralized_one_step` (one FedAvg
round == one gradient step on the pooled data).

### ⚠️ CI will *skip* the 28 training tests, not run them

`tests/unit/test_training_aggregate.py` opens with `np = pytest.importorskip("numpy")`,
which is the right call for an optional extra — but CI (`.github/workflows/lint-and-test.yml`)
installs `pip install -e ".[dev]"`, and the `dev` extra does not pull numpy. **In CI the
whole module silently skips and the green tick means nothing for this feature.** The
28 passes above are from a local env that happens to have numpy 2.4.3.

If this merges, CI needs `pip install -e ".[dev,training]"` (or numpy added to `dev`).
That is a workflow-file edit, outside this ticket's scope, so it is **not** done here —
it is a merge precondition for the human.

---

## 3. Integration surface

Small and additive. Nothing existing changes behaviour.

**`src/mycellm/protocol/envelope.py`** — three new `MessageType` members:
`TRAIN_ROUND` / `TRAIN_UPDATE` / `TRAIN_RESULT` (wire values `"train_round"`,
`"train_update"`, `"train_result"`). `PROTOCOL_VERSION` stays at **1** — correctly, as
no existing message shape changed.

- **No dispatch handler exists.** `MycellmNode._handle_message` has no branch for these
  types, and nothing in the tree ever *sends* one. They are reserved enum values only;
  round transport is explicitly a later phase.
- Forward-compat caveat: `MessageType` is a `str, Enum` on a Pydantic envelope, so a
  0.6.2 peer that has not taken this change would fail envelope validation on a
  `train_*` message rather than ignore it. Inert today (nothing emits them), but the
  transport phase must gate emission on peer capability, not assume tolerance.

**`src/mycellm/node.py`** — one read-only fleet command, `train.status`:

- added to the `_FLEET_COMMANDS` allowlist, so it inherits the existing gate: the
  caller must present a `fleet_admin_key` matching via `hmac.compare_digest`, and the
  node rejects outright if no key is configured. No new auth path.
- `_training_status()` returns `{can_aggregate, can_train_local, base_models, active_jobs}`.
  `can_aggregate` = numpy importable; `can_train_local` = numpy **and** `mlx.core`;
  `base_models` reads `self.inference.loaded_models` (still the current API on main);
  `active_jobs` is a hardcoded `[]` placeholder until round execution lands.
- Both numpy and mlx are probed with guarded `try: import`, so the command answers
  honestly on a default install rather than raising.

**`src/mycellm/training/`** (new package, ~430 lines): `aggregate.py` (`federated_average`,
`adapter_delta`, `l2_norm`, `clip_delta`), `codec.py` (CBOR-safe `{dtype, shape, data}`
tensor round-trip, verified through `cbor2` — the real wire path, per project
convention), `round.py` (`RoundConfig`, `TrainingRound` state machine,
`TRAIN_*` payload builders/parsers, `adapter_fingerprint`).

**Optional-extra discipline — verified.** `pyproject.toml` gains only a `training =
["numpy>=1.24"]` extra; `version` is untouched (`git diff main -- pyproject.toml` shows
no `version` line). A default install never reaches numpy: importing `mycellm`,
`mycellm.node`, `mycellm.protocol.envelope` and `mycellm.cli.main` with numpy forcibly
blocked at the import hook succeeds, and `numpy` is absent from `sys.modules`
afterwards. `mycellm/training/*` is imported by nothing outside itself and the tests.

Also new: `docs/distributed-training.md`, `examples/federated_demo.py` (creates the
`examples/` directory — no such dir existed on main).

---

## 4. Recommendation — **HOLD (merge-ready, but gated on one CI change)**

**Recommend hold, then merge — do not discard.** The code itself is in good shape: it
rebased onto 0.6.2 with zero conflicts, the full 680-test suite is green, ruff is
clean, and the integration surface is genuinely additive — three reserved enum values
with no dispatcher, one read-only fleet command behind the existing HMAC'd
`fleet_admin_key` gate, and a self-contained `training/` package that a default install
never imports. Merging it changes the behaviour of exactly nothing that ships today,
which makes the risk of landing it close to the risk of not landing it, and it banks a
tested aggregation + protocol contract for the three deferred phases (on-device LoRA,
QUIC round transport, `work_type` credit rewards) instead of leaving it to rot against
a moving `main` — it has already needed two refreshes. The one thing that should block
the merge button is the CI gap in §2: as configured, CI installs only `[dev]`, the 28
tests `importorskip` away, and `main` would carry a feature whose entire test evidence
is invisible to the pipeline. Add `training` to the CI install extras (or numpy to
`dev`) in the same PR, confirm the 28 tests actually *run* green there, and then merge.
Secondary, non-blocking: decide whether `main`'s `ad63076` should absorb the env-var
stripping from the dropped `1ab1052`, since that flake is now main's to own.
