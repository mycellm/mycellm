# Mycellm 0.8 — overnight report

**Night of 2026-08-17** · branch `develop`, both repos · Python `app` @ `3b38cd9`
**Tests:** 825 → **950** unit+integration, green · ruff clean · verified on x86_64 and arm64

---

## 1. Read this first: Drydock could not run this work

Its own agent contract says so, and I verified it rather than trusting my notes:

> You cannot drive sessions, mutate tickets, or touch the engine through this
> surface — **by design**. — `GET /api/agents/skill.zip`

An agent can file signals, read memory/context, and post canvas assets and
artifacts. It cannot commission a run. "File the plan, monitor Drydock, unblock
it" therefore had no mechanism behind it — nothing would have started overnight.
So I filed the plan **and built the work directly**, and posted results back.

Filed: signal `f04e1efff705` (project `mycellm`, auto-triaged), canvas assets
under task `0.8-foundation`, artifacts on the project page.

**One thing needs you:** signal `90fb2f81cb17` was filed with the project UUID
where capture wants the slug, so it landed unattached in global triage. Agents
cannot patch signals (405, by design). Please dismiss it; the correct one
supersedes it and says so.

---

## 2. The panel, and where it was wrong

Fable, codex (gpt-5.6-sol) and agy each read the 3,053-line proposal against the
tree independently. **Unanimously**, and confirmed by me: §15.6 targets
`router/router.py`, which is **dead code imported nowhere** — the real routing is
`node.py:2183`. Following the spec's build plan would have burned the night
editing a file nothing calls.

They also disagreed, which was worth more than the agreement. agy said an unknown
`MessageType` makes iOS **"drop the connection"** and built a mandatory rule on
that severity. Fable and codex said message-drop. **I checked: they are right** —
both transports catch and return (`quic.py:118-121`,
`QUICTransport.swift:155-158`). The connection survives and the *message* is
silently lost, which is worse for request/response because `send_and_wait` hangs
until timeout instead of failing fast. Same conclusion, different reason; taking
the consensus at face value would have put something false in the code comments.

On the vertical they split — Fable: swarm first; codex: groups first. I built
groups first *because Fable's own review supplied the reason against its
recommendation*: a swarm fanning out through `route_inference` bypassed privacy
scanning entirely. Then I built the swarm, once that was fixed.

---

## 3. What shipped — 10 commits

### The privacy gate (the prerequisite)
`scan_with_policy` was already correct and had **exactly one caller**: the public
HTTP gateway. `route_inference` — used by the CLI, the OpenAI API and any
fan-out — scanned nothing. `execution/policy.py` now decides egress **per target
during planning**, before anything is dispatched, so a blocked prompt is never
partially sent. Trust is derived from where a target *is*; a caller may lower its
own ceiling but cannot raise a peer's trust.

### `mycellm/swarm` and the execution fabric
`src/mycellm/execution/` — `Job`, `WorkUnit`, `ExecutionPlan`, `Target`, a **pure**
`ExecutionPlanner`, an `ExecutionCoordinator`, `EgressPolicy` — plus the
`mycellm/swarm` synthetic model, `POST /v1/node/plan`, and `GET /v1/node/groups`.

`Target` exists because `PeerEntry` cannot represent a serving group:
`peers_for_model` requires an open QUIC connection, so an HTTP-fronted gateway is
invisible to routing. `execution_targets()` is a read-only projection over loaded
models, QUIC peers and relay groups — not a fourth registry that would drift.

Honesty properties, each tested: a swarm that cannot form **degrades to direct
and says so**; a single-model swarm is labelled *self-consistency sampling*, not
heterogeneity; a proposer dying does not fail the job; synthesis failure returns
the best proposal rather than discarding paid-for work; `token_budget` is a
ceiling and cancelled units do not outlive the job; the plan — including
refusals — is returned in the response.

### Nine live defects in shipped 0.7.1 code
All the same shape: **state recorded correctly, never enforced.**

| | Defect |
|---|---|
| D1 | Dead relays kept serving **ghost models**; the fleet routed to a corpse |
| D2 | **Relay name collisions** — second relay silently dropped; `remove()` unloaded the *other's* model |
| D3 | **No reconciliation** — a model withdrawn upstream stayed advertised forever |
| D4 | **Network isolation skipped locally-originated routing** (enforced on relayed traffic only) |
| D5 | **Per-model `scope` enforced nowhere** — `models_visible_to_network` had the right rule and *zero callers* |
| D6 | **Advertised version hardcoded `"0.1.0"`**, blocking all future feature-gating |
| D7 | **`routing`/`min_context`/`max_cost` accepted and ignored** — callers had credit ceilings that did not exist |
| D8 | **Capabilities are not signed** despite the docstring saying so (documented; the fix is not additive) |
| D9 | **A restarted node reported a group unhealthy with 0 deployments while serving it** |

`execution_roles` was written, **deleted** for having no consumer, then restored
once `ExecutionPlanner._eligible_for` existed to read it. That sequence is the
discipline: no field ships without a consumer the same night.

---

## 4. Working proofs

**Live swarm, real models, real hardware** (`docs/0.8/proof-swarm-live.md`):
a local GGUF plus a **35B MoE on Aurora reached over HTTP**, Aurora running
**0.6.3** — so this also demonstrates §20.1 cross-version interop on real
hardware.

```
strategy      : swarm
units ok/fail : 2 / 0
synthesized_by: group:external:relay:Qwen3.6-35B-A3B
wall          : 72s
ANSWER: The sky appears blue because molecules in Earth's atmosphere scatter
shorter wavelengths of sunlight … known as Rayleigh scattering.
```

The same request with a credential: the remote model **refused by name**, HTTP
422, `every candidate refused by egress policy`.

**A/B against unmodified `main`** (`proof-ab.md`) — same script, two codebases:
baseline leaves ghost models, `develop` withdraws them.

**Relay lifecycle over real HTTP** (`proof-relay.md`) — 16/16, including "one
relay dies, the other keeps serving".

**End-to-end through a live node** (`proof-e2e.md`) — 2 groups, 3 deployments,
both same-named `llama3` deployments distinct, all withdrawn on death.

**Cross-machine** — wheel installed on hokulea (arm64): **896 passed, 0 failed**.

### Two bugs only the live runs found

**A remote endpoint was classified `local`, defeating the privacy gate.**
`Target.kind` came from `serving_group_id`, which `model_configs.json` does not
persist. After a restart the auto-loaded relay model came back with no group id,
was treated as local, and a credential-bearing prompt was **not blocked**.
Remoteness now derives from `LOCAL_BACKENDS`, which cannot go missing in a config
round-trip. 946 unit tests were green while this was broken.

**Synthesis gates the whole swarm, and "prefer local" destroyed it.** The 35B
answered correctly, the 0.5B emitted gibberish, and because the 0.5B was also
chosen to synthesise, the swarm **returned gibberish while reporting success**.
My first fix ranked by `tok_s` with local as tie-break — and failed live *again*,
because nothing had measured either target so both reported `0.0`. Parameter
count (0.5 vs 35) is the signal that separates them.

---

## 5. Not claimed

- **§20.3 swarm quality superiority is NOT demonstrated.** The local 0.5B emits
  gibberish on this host, so the swarm has one useful proposer; the 35B alone
  would do as well. Mechanism is proven; value is not. A real benchmark needs
  three genuinely working models and a same-model N-sample control — the arm most
  likely to tie, and the one that decides whether the story is diversity or
  best-of-N.
- **No oMLX cluster exists.** A mock proves the *adapter contract*, not
  distributed oMLX.
- **`usage` reports 0 tokens** for openai-compat backends — pre-existing.
- **iOS unchanged.** `develop` exists at parity with `main`; the panel agreed no
  changes are needed and build 31 is in App Store review.
- **Capability signing** cannot be done additively; it needs D6 to age first.

Two e2e tests (`test_three_nodes`, `test_resilience_recovery`) fail under load —
verified failing on a clean worktree of `main` too, so pre-existing.

---

## 6. Next steps

1. **Merge decision.** `develop` is 10 commits ahead of `main`, all green. The
   0.7.1→0.8 protocol changes are additive and asserted byte-identical when unset.
2. **Get a third working model** and run the deterministic 4-arm benchmark. Until
   then §20.3 stays unclaimed.
3. **Let the version fix age** before gating anything on it.
4. **Streaming for `mycellm/swarm`** — currently non-streaming only; the
   synthesis stage is the natural place to stream from.
5. **Per-WorkUnit receipts** — each unit already produces a real receipt on the
   serving side; job-level aggregation is deferred, not blocked.
