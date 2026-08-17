# Proof 2 — A/B: ghost models, baseline vs fix
```
--- BASELINE (main @ 012b558, unmodified 0.7.1) ---
Relay http://127.0.0.1:8795: connection refused
  after discovery : ['relay:llama3', 'relay:qwen3']
  after it died   : ['relay:llama3', 'relay:qwen3']

  RESULT: GHOST MODELS PRESENT (bug)
--- DEVELOP (@ 0735a42, with fix) ---
Relay http://127.0.0.1:8795: connection refused
  after discovery : ['relay:llama3', 'relay:qwen3']
  after it died   : []

  RESULT: models withdrawn (fixed)
```
