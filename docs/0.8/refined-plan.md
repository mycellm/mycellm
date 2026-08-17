# Mycellm 0.8 — refined plan after panel review

**Status:** Plan of record for the overnight build
**Date:** 2026-08-17
**Supersedes scope in:** `mycellm-0.8-adaptive-inference-fabric-proposal.md` §11, §15.6, §22
**Panel:** Fable, codex (gpt-5.6-sol), agy — one brief, run independently

---

## 0. Why this document exists

The proposal is directionally right and factually wrong about the code in
several load-bearing places. Three reviewers read it against the tree
independently. Everything below marked **[verified]** I confirmed by reading the
code myself rather than trusting the panel, because the whole value of a panel
is lost if you relay a confident error.

The scope in §11.1 is roughly three releases of work. This plan is what can be
built and *proven* in one night, plus an honest statement of what cannot.

---

## 1. What the panel agreed on

Unanimous across three independent reviewers:

| # | Finding | Status |
|---|---|---|
| 1 | `router/router.py` is **dead code**. §15.6 would modify a file nothing calls. Real routing is `MycellmNode.route_inference` (`node.py:2183`) and `route_inference_stream` (`:2259`), called from `api/openai.py:303,470,927,1077`. | **[verified]** — grep shows zero production importers |
| 2 | A ServingGroup **cannot be a `PeerEntry`**. `peers_for_model` filters on `is_live()`, which requires an open QUIC protocol object (`registry.py:39-56`). An HTTP-reached gateway would be permanently invisible to routing. | **[verified]** |
| 3 | Additive fields on `ModelCapability`/`HardwareInfo` are **genuinely safe both directions** — both decoders read named keys and ignore extras. | **[verified]** |
| 4 | **Do not add new `MessageType` values.** Unknown types are lost. | **[verified]** — see §2 for the correction |
| 5 | `RelayManager` (`inference/relay.py`) is already ~80% of the "OpenAI/oMLX ServingGroup adapter". Extend it; do not build `groups/adapters/openai.py` beside it. | Accepted |
| 6 | **§20.3 is not demonstrable overnight** and must not be claimed. | Accepted |
| 7 | **iOS needs no changes.** It already serves `INFERENCE_REQ` and already self-demotes on thermal/power. It is also frozen in App Store review. | Accepted |

## 2. Where the panel was wrong — and it matters

agy stated that an unknown `MessageType` makes iOS **"drop the connection"**, and
built a "MANDATORY RULE" on that severity. Fable and codex both said it is
message-drop, not connection-drop.

**[verified] Fable and codex are right.** Both transports wrap the parse:

- Python `transport/quic.py:116-121` — `except Exception: logger.error(...); return`
- iOS `QUICTransport.swift:155-158` — `guard let msg = try? ... else { return }`

The connection survives; the *message* is lost. The real constraint is subtler
and worse in one respect: a `send_and_wait` on a new message type **hangs until
timeout** rather than failing fast, and feature detection is impossible today
(see §3, version string). The conclusion — keep 0.8 data inside existing
messages — is unchanged, but for the correct reason.

Recorded because acting on the overstated version would have been acting on
something false that two of three reviewers had already corrected.

---

## 3. Live defects the panel found in shipped code

These are not 0.8 work. They are bugs in 0.7.1, found while reviewing 0.8, and
they sit exactly in the seams 0.8 builds on. Several are the project's
recurring failure mode: **a capability that is advertised or accepted but never
enforced.**

| # | Defect | Evidence | Severity |
|---|---|---|---|
| D1 | **Dead relays keep serving ghost models.** Health failure sets `online=False` but never unregisters the models, which stay in `loaded_models` and keep being advertised. | `relay.py:158-167`, `node.py:584` | High — routes to a dead endpoint |
| D2 | **Relay model-name collision.** Two relays exposing the same model both register `relay:{model_id}`; the second silently wins. | `relay.py:188` | High — silent misrouting |
| D3 | **Network isolation is not applied to locally-originated routing.** The peer-relay path passes `network_ids`; `route_inference` calls `chain_builder.route(model)` with none. | `node.py:2220,2272` vs `node.py:962` | High — cross-network leak |
| D4 | **`routing: "ensemble"` is accepted by the public API and implemented nowhere.** Clients get ordinary routing and HTTP 200. | `api/openai.py:153` | Medium — silent wrong behaviour |
| D5 | **Per-model `scope`/`visible_networks` is enforced nowhere.** `models_visible_to_network` has zero callers. | `registry.py:199-214` | Medium |
| D6 | **Capabilities are not signed**, though the module docstring says "signed by device key". `NodeHello` signs nonce + timestamp + peer ID only. | `node_hello.py:42`, `capabilities.py:1` | Medium — false security claim |
| D7 | **Advertised version is hardcoded `"0.1.0"`** while iOS correctly sends `0.7.1`. Makes all future feature-gating impossible. | `node.py:1497` | Medium — blocks 0.8+ negotiation |
| D8 | `min_context` is a documented no-op; `max_cost` is unenforced. | `model_resolver.py:367` | Low |

**D1–D3 and D6–D7 are in scope tonight.** They are provable, they are real, and
0.8 cannot be built honestly on top of them.

---

## 4. Tonight's scope

### Ship and prove

1. **Additive protocol fields** — `deployment_id`, `serving_group_id`,
   `parallelism` on `ModelCapability`; device telemetry on `HardwareInfo`.
   Omitted from the wire when unset, so a 0.7 network sees byte-identical
   announcements. **With a consumer in the same night** (see §5).
2. **`ServingGroup` / `Deployment` identity over the existing relay** — not a
   parallel `ResourceRegistry`. A group is a *gateway-owned serving service*
   (codex's definition), not a set of peers.
3. **D1, D2, D3, D6, D7 fixed**, each with a regression test that fails before
   and passes after.
4. **Group introspection API** — read-only, so the state is visible.
5. **An honest exploratory benchmark harness** — deterministic scoring only.

### Explicitly NOT shipping, and why

- **`mycellm/swarm`, `ExecutionPlan`, `ExecutionCoordinator`** — fable argues for
  building this first and the argument is good, but codex is right that
  deployment identity, health, and privacy must be coherent underneath it. A
  swarm fanning out through `route_inference` today would bypass privacy
  scanning entirely (`gateway.py:262-279` is the only enforcement point) and
  route across networks (D3). Building it tonight means building it on sand.
- **`execution_roles`** — I added this field, then removed it. Nothing would
  consume it tonight, and shipping an advertised-but-unenforced capability is
  precisely the bug this project keeps shipping. It lands with its consumer.
- **oMLX cluster proof** — no cluster is deployed. A mock OpenAI endpoint proves
  the *adapter contract*, not "distributed oMLX works". That distinction will be
  stated in the report rather than blurred.
- **§20.3 swarm quality superiority** — not demonstrable overnight. Any benchmark
  run is exploratory evidence, reported as such.
- **iOS changes** — unnecessary and unshippable (build 31 is in review).

---

## 5. The rule this plan is built around

> **No field ships without a consumer the same night.**

`deployment_id` / `serving_group_id` / `parallelism` are consumed by the group
registry and the `/v1/node/groups` introspection endpoint. `HardwareInfo`
telemetry is consumed by an eligibility check. `execution_roles` had no consumer
and was therefore removed.

This is not tidiness. Three separate shipped bugs in this codebase have the same
shape: embedding models tagged `["embedding"]` that the chat path never checked;
`routing: "ensemble"` accepted and ignored; `models_visible_to_network` written
and never called.

---

## 6. Acceptance

A morning report should be able to show, with transcripts:

1. 825 existing tests still green — ordinary 0.7 behaviour is boringly unchanged.
2. A 0.7.1 decoder ignores every new field; 0.8 decodes old payloads.
3. Announcements are byte-identical when the new fields are unset.
4. A configured OpenAI-compatible endpoint gets stable group/deployment identity.
5. An unhealthy endpoint's models become **ineligible** (D1), and recovery restores them.
6. Two endpoints exposing the same model **do not collide** (D2).
7. A request on network A is **not** served by a peer only on network B (D3).
8. The advertised version is the real one (D7).
9. Benchmark: whatever it actually showed, including "no observed win".
