# Does the swarm actually improve quality? — measured

**§20.3 remains UNCLAIMED, and now for a measured reason rather than an
untested one.**

## What was missing

The overnight report was explicit about why the claim could not be made:

> A real benchmark needs three genuinely working models and a same-model
> N-sample control — the arm most likely to tie, and the one that decides
> whether the story is diversity or best-of-N.

Both blockers are gone. The fleet now runs three working models (aurora
`Qwen3.6-35B-A3B`, hokulea `Qwen3.5-9B`, iPad `Qwen3.5-9B-MLX-4bit`), and
hyphae supplies what a chat transcript cannot: an **objective oracle**. The
generated code is executed and its output compared to an expected value. No LLM
judge, no rubric, no reading the answer and deciding it looks better.

## Method

`hyphae bench` runs three arms over the same tasks:

| arm | what it is |
|---|---|
| `single` | one model, alone. The baseline to beat. |
| `swarm` | `mycellm/swarm` — distinct models propose, one synthesises. |
| `nsample` | **the control.** The SAME model sampled twice, then synthesising its own drafts. |

`nsample` is the arm that decides the story. A swarm that beats one model but
ties N-sampling gained from *sampling more*, not from *heterogeneity* — and
"mixing models is better" would be unearned. It cannot be built with
`mycellm/swarm` pointed at a single model, because the planner degrades that to
one direct call; it is implemented explicitly in `hyphae/bench.py`.

Tasks were fixed **before** any arm ran. Escalating difficulty until the swarm
wins would tune the benchmark into agreeing with a conclusion instead of
testing it.

## Results

Strong baseline — aurora's 35B alone versus the fleet swarm:

```
simple set, 1 rep      single 4/4 (100%)  2.7s     swarm 4/4 (100%)  18.0s    nsample 4/4 (100%)  16.5s
harder set, 2 reps     single 8/8 (100%)  8.1s     swarm 8/8 (100%)  47.4s    nsample 8/8 (100%)  52.7s
```

Weak baseline — hokulea's 9B alone versus the same swarm, asked because
"the swarm lifts a weak node to fleet quality" is a different and more
plausible claim than "the swarm beats the best model":

```
harder set, 2 reps     single 8/8 (100%)  27.5s    swarm 8/8 (100%)  36.2s    nsample 8/8 (95.4s)
```

## What this supports

1. **No quality benefit is demonstrated.** Every arm solved every task. A
   ceiling like this measures nothing about quality — which is itself the
   finding: on bounded, well-specified coding tasks, a 9B already succeeds, so
   there is no headroom for a swarm to recover.

2. **The cost is real and measured.** Swarm was **6.7x** the latency of the
   single model on the simple set and **5.9x** on the harder one. N-sampling on
   the 9B cost **3.5x**. That is the price of the mechanism, paid whether or not
   it helps.

3. **Therefore: route to a swarm where a single model actually fails, not by
   default.** The fabric's value in these runs is reach and failover — a node
   with no local model answering from the fleet — not answer quality.

## What it does NOT support, and what would be needed

This does not show the swarm is useless; it shows these tasks cannot tell.
Settling it needs tasks at the models' competence boundary, where the single
model fails often enough to leave room — and finding that boundary honestly
(rather than by searching for tasks where the swarm happens to win) is a
research exercise, not a release gate.

Until then §20.3 stays unclaimed, which is the same position as before this
benchmark — but now held for a reason that can be checked.

## Reproducing

```bash
hyphae fabric -w <workspace>                       # what each node is really serving
hyphae bench  -w <workspace> --hard --reps 2 \
              --swarm-url https://api.mycellm.dev/v1
```
