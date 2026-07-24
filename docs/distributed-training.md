# Distributed (federated) training — prototype

Status: **prototype / de-risking** (2026-07-03). The numerical core and round
protocol are implemented and tested; on-device LoRA training, QUIC round
broadcast, and credit rewards are scoped as the next phases below.

This realizes F3 from `docs/spec-evolution.md` and the F3 section of
`fleet-features-plan.md` — coordinated **LoRA/adapter averaging (FedAvg)**, not
distributed gradient descent. A round is a single request/response cycle, which
is what makes it viable over a high-latency, intermittently-connected fleet.

## How a round works

```
coordinator ──TRAIN_ROUND──▶  participants     base adapter + hyperparams
                                   │            each trains locally on its
                                   │            own private data
participant ──TRAIN_UPDATE──▶ coordinator       adapter delta + sample count
coordinator                        │            clip → (gate) → FedAvg
coordinator ──TRAIN_RESULT──▶ participants       new global adapter manifest
```

The coordinator collects updates until a quorum (`min_participants`) is reached
or the round deadline passes with at least one usable update, then aggregates:

```
new = base + server_lr · ( Σ nᵢ · deltaᵢ ) / ( Σ nᵢ )
```

sample-count-weighted so a participant that trained on more examples pulls
proportionally harder (McMahan et al. 2017). One participant ⇒ adopt-their-
update; equal counts ⇒ plain mean.

## What's implemented (`src/mycellm/training/`)

- **`aggregate.py`** — `federated_average`, `adapter_delta`, `l2_norm`,
  `clip_delta` (per-round influence bound = the cooperative-trust analogue of
  gradient clipping). Pure numpy; an "adapter" is a `name → ndarray` map, so the
  math is framework-agnostic and testable without a GPU.
- **`codec.py`** — CBOR-safe tensor (de)serialization: `{dtype, shape, data}`
  with a little-endian buffer, exact round-trip for the fp16/fp32/int8 dtypes
  LoRA adapters use. Verified serializable through `cbor2` (the real wire path).
- **`round.py`** — `RoundConfig`, `TrainingRound` (collect → quorum/deadline →
  clip/gate/aggregate), the `TRAIN_ROUND/UPDATE/RESULT` payload builders/parsers,
  and `adapter_fingerprint` (deterministic short hash that names a global adapter
  version and doubles as the CAS/DHT key for distributing it via F2).
- Protocol: `MessageType.TRAIN_ROUND / TRAIN_UPDATE / TRAIN_RESULT`
  (`protocol/envelope.py`).
- Fleet discovery: read-only `train.status` fleet command reports
  `can_aggregate` / `can_train_local` / `base_models` so a coordinator knows
  which fleet nodes can join a job.

Tests: `tests/unit/test_training_aggregate.py` (28) — math, dtype preservation,
poison-clipping, codec+CBOR round-trips, round state machine, and two
convergence proofs:

- `test_federated_training_converges` — 4 workers with **private, unevenly-sized
  data shards** reach the global least-squares solution over 60 rounds.
- `test_federated_matches_centralized_one_step` — one FedAvg round equals one
  gradient step on the pooled data (exact equivalence — the theoretical anchor).

Run: `pip install -e '.[training]' && pytest tests/unit/test_training_aggregate.py`
Demo: `python examples/federated_demo.py` (self-contained, prints per-round loss).

## Safety posture (prototype)

Cooperative-trust, matching the plan. Present: **norm-clipping** bounds any one
participant per round; **eval metrics** ride along in each update so the
coordinator can gate a bad delta out before it's averaged. Not present (and
called out as such): Byzantine robustness and secure aggregation are research —
this MVP assumes participants are semi-honest and gated by reputation/eval.

## Next phases (not built)

1. **On-device LoRA training** — bridge `mlx-lm` LoRA weights ↔ the `Adapter`
   map on Mac participants; produce a real delta from local data. mlx-swift
   training on iOS is unproven → **iOS is participant-only, and only after the
   Mac path works** (iOS can contribute distributed *eval* much sooner).
2. **Round transport** — broadcast `TRAIN_ROUND` and collect `TRAIN_UPDATE` over
   the existing QUIC relay; adapters ride the **F2 chunk transport** (>10 MB
   adapters exceed the frame cap), keyed by `adapter_fingerprint`.
3. **Rewards** — a new receipt `work_type` (`"train"`) via the co-signed credit
   receipt. **Highest regression risk** (the CBOR map-header/insertion-order
   gotcha + a protocol-version bump) — do it last, behind its own tests.

Do not run training rounds on the live serving nodes (hokulea is 16 GB and
seeds public); use a dedicated participant or a throttled window.
