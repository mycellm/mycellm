"""Self-contained demo of mycellm federated (LoRA-style) training.

Simulates a coordinator + several participant nodes, each holding a private,
unevenly-sized shard of a dataset. Each round, participants "train" locally and
the coordinator FedAvg-merges their adapter deltas. Prints per-round global loss
so you can watch it converge — the same aggregation code paths the fleet uses.

    pip install -e '.[training]'
    python examples/federated_demo.py

No GPU, no ML framework, no network — just the numerical core.
"""

from __future__ import annotations

import numpy as np

from mycellm.training import ParticipantUpdate, RoundConfig, TrainingRound, adapter_fingerprint


def main() -> None:
    rng = np.random.RandomState(0)
    true_w = np.array([1.5, -2.0, 0.75, 3.0])
    n = true_w.size

    # A global dataset split into 5 shards of different sizes across "peers".
    X = rng.randn(500, n)
    y = X @ true_w + 0.02 * rng.randn(500)
    bounds = [0, 30, 90, 200, 330, 500]
    shards = [(X[a:b], y[a:b]) for a, b in zip(bounds, bounds[1:])]

    def local_train(w0, Xs, ys, epochs=5, lr=0.05):
        w = w0.copy()
        for _ in range(epochs):
            w -= lr * (Xs.T @ (Xs @ w - ys) / len(ys))
        return w

    def global_loss(w):
        return float(np.mean((X @ w - y) ** 2))

    print(f"peers={len(shards)} shard_sizes={[len(s[1]) for s in shards]}")
    print(f"target weights = {true_w}\n")

    global_w = {"w": np.zeros(n)}
    for r in range(40):
        cfg = RoundConfig("demo", r, "toy", min_participants=len(shards), max_delta_norm=50.0)
        rnd = TrainingRound(cfg, global_w)
        for i, (Xs, ys) in enumerate(shards):
            trained = local_train(global_w["w"], Xs, ys)
            rnd.submit(ParticipantUpdate(f"peer{i}", {"w": trained - global_w["w"]}, len(ys)))
        result = rnd.aggregate()
        global_w = result.adapter
        if r % 5 == 0 or r == 39:
            print(
                f"round {r:2d}  loss={global_loss(global_w['w']):.5f}  "
                f"contributors={len(result.contributors)}  "
                f"adapter={adapter_fingerprint(global_w)}"
            )

    print(f"\nfinal weights = {np.round(global_w['w'], 3)}")
    print(f"error vs target = {np.linalg.norm(global_w['w'] - true_w):.4f}")


if __name__ == "__main__":
    main()
