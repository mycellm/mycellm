# Running a coding agent on a heterogeneous local fleet

Notes from building **hyphae**, a coding agent that runs entirely on mycellm nodes —
no cloud API — and what measuring it actually taught us. The harness lives in
[`examples/hyphae/`](../examples/hyphae/).

The short version: local models are good enough to write correct multi-file code,
and the thing that stops them is almost never reasoning ability. It is output
format, cold-start latency, and how many models you try to hold in memory at once.

> **What was measured, and with what.** Every number below was produced by an
> earlier implementation of hyphae, in April and May 2026, against real nodes.
> The code now in `examples/hyphae/` is a later, cleaner reimplementation of the
> same design; its test suite runs against mocks and it has **not** been
> re-measured on hardware. The benchmark scripts that produced these numbers are
> included so you can run them yourself, but do not read the timings as a
> benchmark *of the code in this repo*.

## The problem that shaped the design

A coding agent needs the model to produce structured output — which file, what
contents. The obvious mechanism is OpenAI-style tool calling.

Small local models are bad at it. Not "slightly worse than a frontier model" —
bad enough to be unusable. So hyphae does not use tool calls for code generation
at all. It asks for fenced markdown blocks with a filename, and extracts them.
Models that cannot reliably emit a JSON tool call are perfectly good at writing
a Python file inside a code fence, because that is what their training data is
made of.

That was a design guess. In May we tested it properly.

## Testing the guess: tool calling on small models

We ran the eval scenarios from [antoinezambelli/forge](https://github.com/antoinezambelli/forge)
(MIT) against a mycellm node, comparing native tool calling with forge's
prompt-injection mode, where tool descriptions are inlined into the system prompt
and the model's free-text attempt is rescue-parsed.

**Conditions:** `qwen2.5-coder-1.5b-instruct-q8_0`, served by a single mycellm
node on a 16GB M1, 5 runs per scenario, 2026-05-19.

| Scenario | Completeness | Accuracy | Avg time |
|---|---|---|---|
| basic_2step | 100% | 100% | 3.8s |
| sequential_3step | 40% | 40% | 18.5s |
| error_recovery | 40% | 0% | 5.2s |
| tool_selection | 100% | 0% | 5.2s |

The headline is the comparison, not the table. On `basic_2step` the same 1.5B
model scored **0/3 in native tool-calling mode** — exhausting five retries
without ever emitting a valid call — and **5/5 in prompt mode**. Same model,
same hardware, same task. The failure was entirely in the output-format
mechanism.

Two honest limits on that result. First, `error_recovery` and `tool_selection`
show where a 1.5B model actually runs out: it completes the workflow and picks
the wrong tool, or repeats an invalid argument without being able to
self-correct. Prompt mode fixes the format problem, not the judgement problem.
Second, moving to a 7B model cleared `sequential_3step` where 1.5B could not — of
the runs that completed, 2/2 passed — but we could not get a clean 5-run
measurement, because the node kept falling over (see below). We are reporting
2 stable runs, not 5.

### The bug this surfaced

In native mode the 7B model did not merely fail — it took the node down with it
(`RemoteProtocolError`, then refused connections). mycellm was not routing the
OpenAI `tools` parameter through to the backend correctly.

That is fixed. Tool and function calling landed across the whole stack in
**0.3.0 (2026-05-20)** — the day after this eval — carrying `tools` and
`tool_choice` end-to-end through peer routing, with rescue-parsing for models
that emit `<tool_call>` XML or JSON fences instead of structured calls. The eval
is preserved here because finding it is the point, not because it is still true.

## Code generation: five tasks, four fleets

A separate benchmark generates five small Python projects (a FastAPI hello
service, a pydantic model, a dataclass store, an argparse CLI, a Flask
blueprint) and scores the result on whether it imports, parses, and satisfies
task-specific checks.

**Read this benchmark for what it is.** All five tasks are single-file
markdown-extraction work, and every configuration below scored 5/5 at 1.00. It
has no discriminating power on quality — it tells you a fleet is *working*, and
it tells you about latency. It does not tell you a small local model is as good
as a large one, and we are not claiming that.

| Date | Model | Total | Per-task |
|---|---|---|---|
| 2026-05-19 | qwen2.5-coder-1.5b-instruct-q8_0 (16GB M1) | 176.1s | 14.5 / 18.2 / 37.1 / 72.5 / 33.8 |
| 2026-05-20 | Qwen3-Coder-30B-A3B-MLX-4bit (64GB M1 Max) | 370.2s | 217.7 / 102.0 / 18.5 / 18.4 / 13.6 |
| 2026-05-20 | Qwen3-Coder-30B-A3B-MLX-4bit (64GB M1 Max) | 34.2s | 4.8 / 4.4 / 8.9 / 9.3 / 6.8 |

The two 30B rows are the same model on the same machine, four hours apart, and
the ten-fold difference is the most useful thing in the table.

Look at the per-task column rather than the total. In the 370s run the first
task takes 217.7s and the fifth takes 13.6s, decaying monotonically; in the 34s
run every task lands between 4.4s and 9.3s. The consistent reading is that the
first run paid for loading a 30B model into memory and the second did not —
the cost is amortised across whatever you do next, not charged per task. We did
not instrument load time separately, so treat that as the explanation the shape
of the data supports rather than as a measured quantity.

The practical consequence: **a headline "total time" for a local coding agent is
close to meaningless unless it says whether the model was already resident.**
Quoting 370s would misrepresent the fleet; quoting 34s would misrepresent the
first thing a user experiences.

## What the fleet taught us

**One model per node.** The original design routed roles — a large model for
planning, a code-specialised model for generation, a small fast one for drafts —
and assumed a node could hold more than one. It could not. Loading multiple
models onto the 64GB machine destabilised it, and under sustained eval load the
node crashed mid-batch and needed a restart. We collapsed to one model per node
and merged the planning and generation roles onto a single 30B.

That is worth stating plainly because it cuts against the interesting part of
the design. Role-based routing across devices is the reason to build an agent on
a distributed fleet at all, and the memory reality of the fleet pushed us the
other way. The honest position: routing works when each role has its own box,
and "one big node" is a much more common setup than the design assumed.

**Failure is the normal case, not the exception.** A fleet of laptops, desktops
and tablets has nodes that sleep, drop off Wi-Fi, and get closed. An agent that
treats an unreachable device as an exceptional condition will spend most of its
life in exceptional conditions.

## What has not been shown

Being explicit, since these are the parts that would most justify the approach:

- **Speculative drafting** — a fast small model producing a draft while a larger
  model is still planning — is implemented and unit-tested, never measured on
  hardware.
- **Multi-model consensus** — generating a task on three devices and comparing
  structural skeletons — likewise. It also needs three genuinely distinct
  devices to mean anything; with two, a fallback can make one model agree with
  itself.
- The multi-role topology has not run in its intended form since the fleet was
  collapsed to one model per node.

## Reproducing

```bash
# Code generation benchmark against any mycellm node
python3 examples/hyphae/benchmark/run_benchmark.py --mycellm http://localhost:8420

# Tool-calling eval (needs a forge checkout; see the script header)
FORGE_REPO=../forge python3 examples/hyphae/benchmark/tool_call_eval.py \
    --base-url http://localhost:8420/v1 --mode prompt --runs 5
```

Raw result files from the original runs are deliberately not committed: they
record internal hostnames, and editing a recorded result to sanitise it makes it
no longer a record. The numbers above, with their conditions, and the scripts
that produce them are the honest version.
