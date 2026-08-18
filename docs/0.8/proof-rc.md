# 0.8.0 RC — working proofs

Everything below was run against live processes on 2026-08-17, after the RC
changes. Unit tests are not repeated here; these are the paths where a unit
test could pass while the real thing was broken — and two of them were.

---

## 1. Version, live

```
$ curl -s localhost:8420/v1/node/version
{"current":"0.8.0","latest":"0.7.1","update_available":false}
```

The advertised capability version is the real package version now (D6), so
this is also what peers see rather than the hardcoded `"0.1.0"`.

## 2. Serving groups, live

Two throwaway OpenAI-compatible endpoints attached as relays:

```
$ curl -s localhost:8420/v1/node/groups
count 2 | healthy 2 | deployments 3
  grp_aurora  http://127.0.0.1:9401  [relay:llama3-70b, relay:qwen2.5-7b]
  grp_vulcan  http://127.0.0.1:9402  [relay:mixtral-8x7b]
```

Each deployment carries `deployment_id`, `upstream_model` and `parallelism`.
Two groups serving the same model name stay distinguishable.

## 3. `/v1/node/plan` — selection and refusal

```
$ POST /v1/node/plan {"model":"mycellm/swarm","messages":[{"role":"user",
                      "content":"why is the sky blue"}]}
strategy: swarm
  proposer    -> group:grp_aurora:relay:llama3-70b
  proposer    -> group:grp_aurora:relay:qwen2.5-7b
  proposer    -> group:grp_vulcan:relay:mixtral-8x7b
  synthesizer -> group:grp_aurora:relay:llama3-70b
```

Synthesis landed on the 70B, not on whichever target sorted first — the
parameter-count rule that replaced the `tok_s` tie-break after it failed live
during the overnight run.

The same request carrying a credential:

```
strategy: swarm | units: 0
  REFUSED group:grp_aurora:relay:llama3-70b  — sensitive data blocked for
                                               untrusted egress: AWS access key
  REFUSED group:grp_aurora:relay:qwen2.5-7b  — (same)
  REFUSED group:grp_vulcan:relay:mixtral-8x7b — (same)
reasons: ['every candidate refused by egress policy']
```

Nothing was dispatched, and the refusals are named rather than silent.

## 4. Fleet commands over real QUIC

A second node (`rc-peer`) bootstrapped to the first; commands relayed through
`POST /v1/admin/fleet/command` on the bootstrap, which never holds the key.

```
relay.add    → success, group_id grp_vulcan-remote, models [mixtral-8x7b]
node.groups  → count 1 | healthy 1 | deployments 1
node.targets → group:grp_vulcan-remote:relay:mixtral-8x7b  remote=True  56.0B
               peer:9f12ca70:relay:llama3-70b              remote=True  70.0B
               peer:9f12ca70:relay:qwen2.5-7b              remote=True   7.0B
               peer:9f12ca70:relay:mixtral-8x7b            remote=True  56.0B
relay.remove → {"status":"removed"}
relay.wipe   → {"success":false,"error":"Command not allowed: relay.wipe"}
wrong key    → {"success":false,"error":"Invalid fleet admin key"}
```

`node.plan` on the peer, with a credential in the prompt, shows the trust
derivation doing exactly what it claims:

```
  REFUSED group:grp_vulcan-remote:relay:mixtral-8x7b — sensitive data blocked
                                                       for untrusted egress
  peer:9f12ca70:*  — allowed with warning (high severity)
```

A federated QUIC peer sharing a network is *trusted*; an external HTTP gateway
is not. Same prompt, different verdicts, derived from where each target is —
not from anything either target claimed.

## 5. Dashboard, in a real browser

Driven headlessly against the live node at `10.1.1.121:8420` (screenshots in
the session scratchpad). Confirmed on screen:

- **Model selector** shows a `Strategies` group containing `mycellm/swarm`.
  Before this release the node advertised the model on `/v1/models` and the
  selector filtered on `owned_by` in {`local`, `fleet:*`, `peer:*`}, so the
  headline 0.8 feature was unreachable from the UI.
- **No "Fastest" button.** It sent `routing: "fastest"`, which 0.8 refuses with
  HTTP 400 — a control that could only fail.
- **Serving Groups panel** renders both groups with endpoint, health and each
  deployment's local→upstream name mapping.
- **Execution plan card** under a swarm answer: `SWARM · 3 proposers · 3 failed
  · degraded · 0.022s`, expanding to units, refusals and per-target errors.
  (The stubs do not implement chat completions, which is what makes this the
  failure-path proof.)

### Two defects only the live run found

**The dashboard was reading a field the node does not send.** `GET
/v1/node/groups` returns `endpoint`; `POST /v1/node/relay/add` takes `url`. I
assumed symmetry and typed `ServingGroup.url`, which type-checked cleanly and
rendered an empty string against the live node. Deployments likewise carry
`upstream_model`, not the `registered_as` I had guessed.

**The routing panel drew over the toolbar.** It had always rendered as a
sibling of its toggle inside the toolbar's flex row; with four option groups
that fit on one line. Adding trust, proposer count and token budget made it
wrap, and the second row covered the model selector and the "N models on
network" label. The panel is now a separate export rendered below the toolbar,
where ChatTab already had a placeholder comment for it.

Neither was reachable by `tsc`, by the unit tests, or by reading the diff.

## 6. Cross-implementation CBOR vectors

Not a live run, but the same category of proof — the two codecs checked against
each other's actual bytes rather than against a shared reading of the spec:

- `Capabilities08CompatTests.testDecodesRealCBORProducedByThePythonNode`
  decodes a checked-in payload produced by `cbor2.dumps(Capabilities.to_dict())`.
- `TestCrossImplementationGoldenVector` in
  `tests/unit/test_capabilities_08_compat.py` decodes a checked-in payload
  produced by `Capabilities.toCBORValue().encode()` on iOS.

Both assert the nested `power`/`thermal`/`network` shape, that `world_size`
survives as an `int` rather than a float, and that a 0.7 decoder reading the
same bytes with only the keys it knew still gets a usable model.

## 7. Suites

| Suite | Result |
|---|---|
| Python unit + integration | **969 passed**, 2 skipped |
| ruff | clean |
| iOS (iPhone 17 Pro simulator) | **251 passed**, 0 failures |
| Dashboard `tsc -b && vite build` | clean |
