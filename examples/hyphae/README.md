# hyphae — a coding agent that runs on your own fleet

**An example harness built on mycellm.** It decomposes a coding request into a
task DAG and executes it across local nodes, using no cloud API.

This lives in `examples/` because that is what it is: a demonstration of what
mycellm makes possible, not a second product. mycellm itself is a drop-in
OpenAI-compatible backend and works with Claude Code, aider and Continue.dev —
reach for one of those if you want a coding agent to use rather than to read.

> **Status.** The design has been exercised end-to-end on a real fleet, and
> [`docs/coding-agent-on-a-heterogeneous-fleet.md`](../../docs/coding-agent-on-a-heterogeneous-fleet.md)
> reports what that measured. **This code is a later reimplementation of that
> design and has not itself been re-measured on hardware** — its test suite runs
> against mocks. Treat it as a well-tested reference implementation, not as the
> thing that produced the numbers.

Hyphae decomposes a natural-language coding request into a DAG of subtasks and
executes them across a heterogeneous fleet of devices, each playing the role
its hardware is best at:

| Role | Device profile | Job |
|---|---|---|
| **Architect** | Big unified memory (e.g. M1 Max 64GB) | Planning, review, complex tasks |
| **Builder** | Mid-tier (e.g. M1 16GB) | Primary code generation |
| **Scout** | Small/fast (e.g. iPad M4) | Speculative execution, analysis |

Every device is just an OpenAI-compatible endpoint — point the roles at
mycellm nodes (`http://<node>:8420/v1`), Ollama, vLLM, or anything else that
speaks the API. [`ARCHITECTURE.md`](ARCHITECTURE.md) is the full design
document — the `§` references throughout the source point into it.

## What's implemented (v0.1)

- **Planner** — request → structured spec → validated task DAG, with a
  self-repair loop when the model emits an invalid plan.
- **Generate → Validate → Fix loop** — fast local validation (Python `ast`,
  JSON, TOML) feeds diagnostics back as *micro-iterations* that never consume
  a full task iteration; Architect review gates everything non-trivial.
- **Test-execution verification** — after each code task, the workspace test
  suite runs (auto-detected pytest or a configured `test_command`); a failure
  rolls the files back and feeds the output into the next iteration.
  Deterministic — catches what LLM review rubber-stamps.
- **Speculative task execution** — while a task sits in Architect review, the
  Scout drafts the next task; the Builder then refines the draft instead of
  starting cold. Disabled automatically when no dedicated Scout exists.
- **Structural consensus verification** — critical tasks (auth, money,
  persistence) are generated independently on all three devices; outputs are
  compared by AST skeleton and a 2-of-3 structural majority wins.
- **Structural memory** — a SQLite symbol graph (definitions, imports) of the
  workspace, updated live as tasks complete, used for context assembly.
- **Graceful degradation** — missing roles fall back along sensible chains;
  with a single endpoint, Hyphae still works as a solo plan/execute/review
  loop.

## Quickstart

```bash
pip install -e "examples/hyphae[dev]"    # from the repo root

# zero config: a local mycellm node at :8420 plays every role
hyphae run "Add a --verbose flag to the CLI" --workspace ~/code/myproject

# or define your fleet
hyphae init            # writes hyphae.toml
hyphae devices         # connectivity check
hyphae plan "Add dark mode to the settings page"   # show the DAG, don't run
hyphae run  "Add dark mode to the settings page"
```

`hyphae.toml`:

```toml
[[devices]]
name = "studio"
role = "architect"
base_url = "http://studio:8420/v1"
model = "auto:capable"

[[devices]]
name = "laptop"
role = "builder"
base_url = "http://laptop:8420/v1"
model = "auto:fast"

[[devices]]
name = "ipad"
role = "scout"
base_url = "http://ipad:8420/v1"
model = "auto:tiny"
```

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

The whole pipeline is testable without any model: see `tests/conftest.py` for
the scriptable `MockLLM` and `tests/test_executor.py` for end-to-end runs
covering warm starts, micro-iterations, review rejection, and consensus.

## Not yet here (roadmap)

- Cross-device speculative *decoding* (token-level drafting iPad → Architect)
- LoRA adapter hot-swap hints in TaskCards
- Tree-sitter/LSP validation for non-Python languages
- Adaptive re-planning after task failures
