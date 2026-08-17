# Proof — mycellm/swarm against real models on real hardware

Run 2026-08-17 from a 0.8 `develop` node on jupiter, fanning out to:

- `qwen2.5-0.5b` — a real GGUF (491MB, Q4_K_M) loaded locally via llama.cpp
- `relay:Qwen3.6-35B-A3B` — a real 35B MoE served by **Aurora**, an
  independent Mac running mycellm **0.6.3**, reached over HTTP

Two genuinely different models, one of them on another machine running an older
release. Nothing here is mocked.

## Plan

```
cand group:external:relay:Qwen3.6-35B-A3B         remote=True
cand local:qwen2.5-0.5b                           remote=False
 • proposer     local:qwen2.5-0.5b
 • proposer     group:external:relay:Qwen3.6-35B-A3B
 • synthesizer  group:external:relay:Qwen3.6-35B-A3B
```

## Execution

```
strategy      : swarm
units ok/fail : 2 / 0
synthesized_by: group:external:relay:Qwen3.6-35B-A3B
degraded      : False
wall          : 72s

ANSWER: The sky appears blue because molecules in Earth's atmosphere scatter
shorter wavelengths of sunlight (blue light) more efficiently than longer
wavelengths (red light) through a process known as Rayleigh scattering.
```

## Egress gate, same path

The identical request with a credential in the prompt:

```
cand  group:external:relay:Qwen3.6-35B-A3B  remote=True
units    : [('direct', 'local:qwen2.5-0.5b')]
REJECTED : group:external:relay:Qwen3.6-35B-A3B
           :: sensitive data blocked for untrusted egress: AWS access key
reasons  : swarm requested but only 1 eligible proposer — degraded to direct
```

And through `/v1/chat/completions`:

```
HTTP 422 | type: swarm_execution_error | code: swarm_failed
message: every candidate refused by egress policy
```

## Backward compatibility (§20.1)

```
ours   : 0.7.1   (0.8 develop)
aurora : 0.6.3
```

A 0.8 node discovered and served a 0.6.3 node's model throughout.

## Two bugs this run found that no unit test did

**1. A remote endpoint was classified `local`, defeating the privacy gate.**
`Target.kind` was derived from `serving_group_id`, which `model_configs.json`
does not persist. After a node restart the auto-loaded relay model came back
with no group id, was treated as local, and a credential-bearing prompt was
**not blocked**. Remoteness is now derived from the backend (`LOCAL_BACKENDS`),
which cannot go missing in a config round-trip. Re-verified in the exact restart
scenario: `remote=True`, credential blocked.

**2. Synthesis quality gates the whole swarm, and "prefer local" destroyed it.**
Preferring a local synthesiser looks like the obvious privacy default. In the
first live run the 35B produced a correct answer, the 0.5B produced gibberish,
and because the 0.5B was also chosen to synthesise, the **swarm returned
gibberish while reporting success** — two proposers OK, not degraded.

The privacy argument does not hold once any proposer is remote: the prompt has
already left. The first fix ranked by `tok_s` with local as the tie-break — and
failed live again, because nothing had measured either target so both reported
`tok_s 0.0` and the tie-break chose the 0.5B. Parameter count (0.5 vs 35) is the
signal that actually separates them, and is free from the advertised value or
the model name.

## What is NOT claimed

- **No quality claim.** §20.3 asks for a task class where a heterogeneous swarm
  beats the best single participating model. Not demonstrated. The local 0.5B
  emits gibberish on this host, so this swarm has one useful proposer; the
  35B alone would answer as well or better. Mechanism is proven, value is not.
- `usage` reports 0 tokens for openai-compat backends — a pre-existing
  accounting defect, not introduced here.
- After a restart, `/v1/node/groups` shows the group unhealthy with 0
  deployments: the model auto-loads from its saved config, so `RelayManager`
  finds the name already claimed and does not take ownership. Serving works and
  remoteness is correct; only the group *view* is wrong.
