# Proof 3 — end-to-end through a live node and the real HTTP API
```
═══ 1. Node is up ═══
{
    "status": "ok",
    "version": "0.7.1",
    "peer_id": "fd4f0b9b05ba818815f8c4246e8411c3",

═══ 2. Advertised version (was hardcoded 0.1.0) ═══
  version = 0.7.1

═══ 3. Serving groups — BOTH relays, both llama3 deployments ═══
  groups=2 healthy=2 deployments=3
   • grp_localhost-8882   healthy=True  http://127.0.0.1:8882
       - relay:llama3               dep_grp_localhost-8882_relay-llama3
   • grp_localhost-8881   healthy=True  http://127.0.0.1:8881
       - relay:localhost-8881:llama3 dep_grp_localhost-8881_relay-localhost-8881-llama3
       - relay:qwen3                dep_grp_localhost-8881_relay-qwen3

═══ 4. /v1/models — no collision, both llama3s present ═══
  models: ['auto', 'llama3', 'localhost-8881:llama3', 'qwen3']
  llama3-bearing: ['llama3', 'localhost-8881:llama3']

═══ 5. Kill relay :8881 → its models must be WITHDRAWN ═══
    s = HTTPServer(("127.0.0.1", port), H); s.models = models
    threading.Thread(target=s.serve_forever, daemon=True).start()

   • grp_localhost-8882   healthy=False  deployments=0
   • grp_localhost-8881   healthy=False  deployments=0
  total deployments now: 0

═══ node log: withdrawal lines ═══
                    INFO     Relay http://127.0.0.1:8882: withdrew 1 model(s) — 
                    INFO     Relay http://127.0.0.1:8881: withdrew 2 model(s) — 

(node log at /tmp/myc-e2e-proof/node.log)
```
