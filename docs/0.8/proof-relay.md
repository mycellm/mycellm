# Proof 1 — relay lifecycle over real HTTP (isolated servers)
```
Relay http://127.0.0.1:8791: connection refused

╔══ D1: a relay that dies must withdraw its models ══
  PASS two models discovered over HTTP — ['relay:llama3', 'relay:qwen3']
  PASS models are loaded — ['relay:llama3', 'relay:qwen3']
  PASS relay marked offline
  PASS models WITHDRAWN, not ghosts — []
  PASS ownership record cleared — {}
  PASS healthy is False

╔══ D1b: recovery restores them ══
  PASS relay back online
  PASS models re-registered — ['relay:llama3', 'relay:qwen3']

╔══ D2: two relays serving the same model ══
  PASS bravo registered its own llama3 — ['relay:bravo:llama3']
  PASS both llama3 deployments exist — ['relay:bravo:llama3', 'relay:llama3', 'relay:qwen3']
  PASS deployment ids are distinct

╔══ D2b: removing one leaves the other serving ══
  PASS bravo's model survives alpha's removal — ['relay:bravo:llama3']
  PASS alpha's models gone

╔══ D3: a model withdrawn upstream is unregistered ══
  PASS withdrawn model unregistered — []
  PASS ownership record empty — {}
  PASS newly-added upstream model registered — ['relay:phi4']

  ── 16/16 checks passed ──

```
