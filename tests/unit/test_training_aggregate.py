"""Tests for the federated-averaging core + training-round protocol.

The headline test (test_federated_training_converges) proves the whole loop
actually learns: several simulated participants each hold a private shard of a
linear-regression dataset, train locally, and the coordinator's FedAvg brings a
shared parameter vector to the global least-squares solution — no GPU, no ML
framework, just the aggregation math this module ships.
"""

from __future__ import annotations

import pytest

# Training is an optional extra (`pip install -e .[training]`); skip the whole
# module rather than error-collect when numpy isn't installed.
np = pytest.importorskip("numpy")

from mycellm.training import (  # noqa: E402
    AggregationError,
    ParticipantUpdate,
    RoundConfig,
    TrainingRound,
    adapter_delta,
    adapter_fingerprint,
    build_train_round_payload,
    build_train_update_payload,
    clip_delta,
    federated_average,
    l2_norm,
    parse_train_round_payload,
    parse_train_update_payload,
)
from mycellm.training.codec import (  # noqa: E402
    decode_adapter,
    decode_tensor,
    encode_adapter,
    encode_tensor,
)


def _adapter(**kw) -> dict[str, np.ndarray]:
    return {k: np.asarray(v, dtype=np.float32) for k, v in kw.items()}


# --- FedAvg math ----------------------------------------------------------


def test_single_participant_adopts_full_update():
    base = _adapter(w=[0.0, 0.0])
    upd = ParticipantUpdate("peerA", _adapter(w=[1.0, 2.0]), num_samples=10)
    out = federated_average(base, [upd])
    np.testing.assert_allclose(out["w"], [1.0, 2.0])


def test_equal_samples_is_plain_mean():
    base = _adapter(w=[0.0, 0.0])
    a = ParticipantUpdate("a", _adapter(w=[2.0, 0.0]), num_samples=5)
    b = ParticipantUpdate("b", _adapter(w=[0.0, 4.0]), num_samples=5)
    out = federated_average(base, [a, b])
    np.testing.assert_allclose(out["w"], [1.0, 2.0])


def test_sample_weighting_pulls_toward_larger_shard():
    base = _adapter(w=[0.0])
    a = ParticipantUpdate("a", _adapter(w=[10.0]), num_samples=90)
    b = ParticipantUpdate("b", _adapter(w=[0.0]), num_samples=10)
    out = federated_average(base, [a, b])
    # 0.9*10 + 0.1*0 = 9.0
    np.testing.assert_allclose(out["w"], [9.0])


def test_server_learning_rate_takes_partial_step():
    base = _adapter(w=[0.0])
    upd = ParticipantUpdate("a", _adapter(w=[10.0]), num_samples=1)
    out = federated_average(base, [upd], learning_rate=0.5)
    np.testing.assert_allclose(out["w"], [5.0])


def test_zero_sample_updates_ignored():
    base = _adapter(w=[1.0])
    good = ParticipantUpdate("a", _adapter(w=[2.0]), num_samples=4)
    idle = ParticipantUpdate("b", _adapter(w=[99.0]), num_samples=0)
    out = federated_average(base, [good, idle])
    np.testing.assert_allclose(out["w"], [3.0])  # base + full delta from 'a'


def test_no_signal_raises():
    base = _adapter(w=[1.0])
    with pytest.raises(AggregationError):
        federated_average(base, [ParticipantUpdate("a", _adapter(w=[9.0]), 0)])


def test_shape_mismatch_raises():
    base = _adapter(w=[0.0, 0.0])
    bad = ParticipantUpdate("a", _adapter(w=[1.0]), num_samples=1)
    with pytest.raises(AggregationError):
        federated_average(base, [bad])


def test_key_mismatch_raises():
    base = _adapter(w=[0.0])
    bad = ParticipantUpdate("a", _adapter(x=[1.0]), num_samples=1)
    with pytest.raises(AggregationError):
        federated_average(base, [bad])


def test_negative_samples_rejected():
    with pytest.raises(AggregationError):
        ParticipantUpdate("a", _adapter(w=[1.0]), num_samples=-1)


def test_preserves_dtype():
    base = {"w": np.zeros(3, dtype=np.float16)}
    upd = ParticipantUpdate("a", {"w": np.ones(3, dtype=np.float16)}, num_samples=1)
    out = federated_average(base, [upd])
    assert out["w"].dtype == np.float16


# --- delta / norm / clip --------------------------------------------------


def test_adapter_delta_roundtrips():
    base = _adapter(w=[1.0, 2.0])
    trained = _adapter(w=[1.5, 2.5])
    d = adapter_delta(base, trained)
    np.testing.assert_allclose(d["w"], [0.5, 0.5])


def test_l2_norm_flat():
    a = _adapter(x=[3.0, 0.0], y=[4.0])
    assert l2_norm(a) == pytest.approx(5.0)


def test_clip_scales_down_only_when_over():
    d = _adapter(w=[3.0, 4.0])  # norm 5
    clipped = clip_delta(d, max_norm=1.0)
    assert l2_norm(clipped) == pytest.approx(1.0)
    # under the cap → untouched
    small = _adapter(w=[0.1])
    assert clip_delta(small, max_norm=1.0) is small


def test_clip_bounds_a_poison_update():
    base = _adapter(w=[0.0])
    honest = ParticipantUpdate("h", _adapter(w=[1.0]), num_samples=1)
    poison = ParticipantUpdate("p", _adapter(w=[1000.0]), num_samples=1)
    round_ = TrainingRound(
        RoundConfig("job", 0, "m", min_participants=2, max_delta_norm=2.0),
        base,
    )
    round_.submit(honest)
    round_.submit(poison)
    out = round_.aggregate()
    # poison clipped to norm 2 → contributes 2.0 not 1000; mean of 1 and 2 = 1.5
    np.testing.assert_allclose(out.adapter["w"], [1.5])


# --- codec ----------------------------------------------------------------


@pytest.mark.parametrize("dtype", ["float16", "float32", "int8", "uint8"])
def test_tensor_codec_roundtrip(dtype):
    arr = (np.arange(12).reshape(3, 4) - 3).astype(dtype)
    out = decode_tensor(encode_tensor(arr))
    assert out.dtype == arr.dtype
    np.testing.assert_array_equal(out, arr)
    assert out.flags.writeable


def test_adapter_codec_roundtrip():
    a = _adapter(w=[1.0, 2.0], b=[[1.0, 0.0], [0.0, 1.0]])
    out = decode_adapter(encode_adapter(a))
    for k in a:
        np.testing.assert_allclose(out[k], a[k])


def test_codec_survives_cbor():
    # The real wire path is cbor2 over QUIC — make sure the encoded form is
    # actually CBOR-serializable (bytes + lists + str keys, no ndarray leaks).
    cbor2 = pytest.importorskip("cbor2")
    a = _adapter(w=np.random.RandomState(0).randn(5).tolist())
    blob = cbor2.dumps(encode_adapter(a))
    out = decode_adapter(cbor2.loads(blob))
    np.testing.assert_allclose(out["w"], a["w"])


# --- round protocol payloads ---------------------------------------------


def test_round_payload_roundtrip():
    cfg = RoundConfig("job1", 3, "Qwen2.5-3B", local_epochs=2, min_participants=2)
    base = _adapter(w=[0.1, 0.2])
    cfg2, base2 = parse_train_round_payload(build_train_round_payload(cfg, base))
    assert cfg2.job_id == "job1" and cfg2.round_index == 3
    assert cfg2.local_epochs == 2 and cfg2.min_participants == 2
    np.testing.assert_allclose(base2["w"], [0.1, 0.2])


def test_update_payload_roundtrip():
    upd = ParticipantUpdate("peerX", _adapter(w=[0.5]), 42, metrics={"loss": 0.3})
    out = parse_train_update_payload(build_train_update_payload(upd))
    assert out.peer_id == "peerX" and out.num_samples == 42
    assert out.metrics == {"loss": 0.3}
    np.testing.assert_allclose(out.delta["w"], [0.5])


def test_fingerprint_is_deterministic_and_content_sensitive():
    a = _adapter(w=[1.0, 2.0])
    b = _adapter(w=[1.0, 2.0])
    c = _adapter(w=[1.0, 2.001])
    assert adapter_fingerprint(a) == adapter_fingerprint(b)
    assert adapter_fingerprint(a) != adapter_fingerprint(c)


# --- round state machine --------------------------------------------------


def test_round_quorum_and_expiry():
    cfg = RoundConfig("j", 0, "m", min_participants=2, deadline_s=100.0)
    r = TrainingRound(cfg, _adapter(w=[0.0]))
    r.submit(ParticipantUpdate("a", _adapter(w=[1.0]), 1))
    assert not r.has_quorum() and not r.ready(now=r._started_at)
    r.submit(ParticipantUpdate("b", _adapter(w=[1.0]), 1))
    assert r.has_quorum() and r.ready(now=r._started_at)


def test_round_ready_on_deadline_with_partial_signal():
    cfg = RoundConfig("j", 0, "m", min_participants=5, deadline_s=10.0)
    r = TrainingRound(cfg, _adapter(w=[0.0]))
    r.submit(ParticipantUpdate("a", _adapter(w=[1.0]), 1))
    assert not r.ready(now=r._started_at)
    assert r.ready(now=r._started_at + 11)  # deadline passed, has 1 usable


def test_round_last_write_wins_per_peer():
    r = TrainingRound(RoundConfig("j", 0, "m"), _adapter(w=[0.0]))
    r.submit(ParticipantUpdate("a", _adapter(w=[1.0]), 1))
    r.submit(ParticipantUpdate("a", _adapter(w=[5.0]), 1))
    out = r.aggregate()
    np.testing.assert_allclose(out.adapter["w"], [5.0])
    assert out.contributors == ["a"]


# --- the real thing: convergence -----------------------------------------


def test_federated_training_converges():
    """N participants with private data shards reach the global LSQ solution.

    Each round: every worker takes a few local gradient steps on ITS shard from
    the shared weights, reports the delta + its sample count, and FedAvg merges
    them. Over rounds the shared weights must approach the solution you'd get by
    training on the pooled data — the property that makes federated training
    worthwhile.
    """
    rng = np.random.RandomState(1)
    true_w = np.array([2.0, -3.0, 0.5])
    n_features = true_w.size

    # Global dataset, then sharded unevenly across 4 workers.
    X = rng.randn(400, n_features)
    y = X @ true_w + 0.01 * rng.randn(400)
    shards = [(X[0:40], y[0:40]), (X[40:120], y[40:120]),
              (X[120:250], y[120:250]), (X[250:400], y[250:400])]

    def local_train(w0, Xs, ys, epochs=5, lr=0.05):
        w = w0.copy()
        for _ in range(epochs):
            grad = Xs.T @ (Xs @ w - ys) / len(ys)
            w -= lr * grad
        return w

    global_w = {"w": np.zeros(n_features, dtype=np.float64)}
    for round_index in range(60):
        cfg = RoundConfig("lsq", round_index, "toy", min_participants=len(shards))
        rnd = TrainingRound(cfg, global_w)
        for i, (Xs, ys) in enumerate(shards):
            trained = local_train(global_w["w"], Xs, ys)
            delta = {"w": trained - global_w["w"]}
            rnd.submit(ParticipantUpdate(f"peer{i}", delta, num_samples=len(ys)))
        global_w = rnd.aggregate().adapter

    # Converged to within a small tolerance of the true generating weights.
    np.testing.assert_allclose(global_w["w"], true_w, atol=0.05)


def test_federated_matches_centralized_one_step():
    """One FedAvg round == one gradient step on the pooled data (equivalence).

    With sample-weighted averaging and identical local LR, a single local step
    per worker aggregates to exactly the step you'd take centrally — the
    theoretical anchor for the loop above.
    """
    rng = np.random.RandomState(7)
    w0 = rng.randn(3)
    X = rng.randn(100, 3)
    y = rng.randn(100)
    lr = 0.1

    # Centralized one step.
    grad_all = X.T @ (X @ w0 - y) / len(y)
    central = w0 - lr * grad_all

    # Federated: 2 shards, each one local step, sample-weighted merge.
    fed_updates = []
    for sl in (slice(0, 30), slice(30, 100)):
        Xs, ys = X[sl], y[sl]
        g = Xs.T @ (Xs @ w0 - ys) / len(ys)
        w_local = w0 - lr * g
        fed_updates.append(ParticipantUpdate(f"s{sl.start}", {"w": w_local - w0}, len(ys)))
    fed = federated_average({"w": w0}, fed_updates)["w"]

    np.testing.assert_allclose(fed, central, atol=1e-10)
