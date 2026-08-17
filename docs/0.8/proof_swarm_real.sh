#!/usr/bin/env bash
# WORKING PROOF: mycellm/swarm against REAL models on real hardware.
#
# Aurora (100.100.1.81) runs mycellm 0.6.3 and serves two genuinely different
# models: Qwen3.6-35B-A3B (local 35B MoE) and claude-sonnet (via a peer). We add
# it as a relay to a 0.8 develop node and run a real swarm through it.
#
# This proves three things at once:
#   1. mycellm/swarm fans out to real models and synthesises a real answer
#   2. the egress policy blocks a credential-bearing prompt from the same path
#   3. a 0.8 node interoperates with a 0.6.3 node (§20.1 release-blocking)
set -u
APP=/Users/jupiter/data/projects/mycellm/app
D=/tmp/myc-swarm-proof
AURORA=http://100.100.1.81:8420
rm -rf $D && mkdir -p $D
cd $APP

export MYCELLM_CONFIG_DIR=$D MYCELLM_DATA_DIR=$D
.venv/bin/mycellm account create --name swarmproof >/dev/null 2>&1
.venv/bin/mycellm device create --name default   >/dev/null 2>&1

.venv/bin/mycellm serve --host 127.0.0.1 --port 8920 --quic-port 8921 --dht-port 8922 \
    --no-dht --relay $AURORA > $D/node.log 2>&1 &
NODE=$!
trap 'kill $NODE 2>/dev/null' EXIT

for i in $(seq 1 45); do
  curl -sf -m 2 http://127.0.0.1:8920/health >/dev/null 2>&1 && break
  sleep 2
done
KEY0=$(.venv/bin/python -c "from mycellm.config import get_settings; print(get_settings().api_key or '')" 2>/dev/null)
echo "  loading a real local model (Qwen2.5-0.5B) so a swarm can form…"
curl -s -m 300 -H "Authorization: Bearer $KEY0" -X POST http://127.0.0.1:8920/v1/node/models/load \
  -H 'Content-Type: application/json' \
  -d '{"model_path":"/tmp/myc-models/qwen2.5-0.5b-q4.gguf","name":"qwen2.5-0.5b","ctx_len":4096}' \
  | head -c 200; echo
# Loading is asynchronous — wait for the model to actually be servable, or the
# rest of the proof races a model that is not there yet.
for i in $(seq 1 60); do
  if curl -s -m 5 http://127.0.0.1:8920/v1/models | grep -q "qwen2.5-0.5b"; then
    echo "  local model ready after ${i}x2s"; break
  fi
  sleep 2
done
KEY=$(.venv/bin/python -c "from mycellm.config import get_settings; print(get_settings().api_key or '')" 2>/dev/null)
H_AUTH=(-H "Authorization: Bearer $KEY")

echo "═══ 1. Our node (0.8 develop) and Aurora (0.6.3) ═══"
printf "  ours   : "; curl -s -m 5 http://127.0.0.1:8920/health | python3 -c "import json,sys;print(json.load(sys.stdin)['version'])"
printf "  aurora : "; curl -s -m 5 $AURORA/health | python3 -c "import json,sys;print(json.load(sys.stdin)['version'])"

echo
echo "═══ 2. Aurora's real models, discovered as a ServingGroup ═══"
curl -s -m 10 "${H_AUTH[@]}" http://127.0.0.1:8920/v1/node/groups | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(f\"  groups={d.get('count')} healthy={d.get('healthy_count')} deployments={d.get('deployment_count')}\")
for g in d.get('groups',[]):
    print(f\"   • {g['group_id']}  healthy={g['healthy']}\")
    for dep in g['deployments']:
        print(f\"       - {dep['model']}\")
"

echo
echo "═══ 3. mycellm/swarm advertised? (only when a swarm can form) ═══"
curl -s -m 10 http://127.0.0.1:8920/v1/models | python3 -c "
import json,sys
ids=[m['id'] for m in json.load(sys.stdin).get('data',[])]
print('  models:', ids)
print('  swarm advertised:', 'mycellm/swarm' in ids)
"

echo
echo "═══ 4. The PLAN for a swarm request (no execution) ═══"
curl -s -m 15 "${H_AUTH[@]}" -X POST http://127.0.0.1:8920/v1/node/plan \
  -H 'Content-Type: application/json' \
  -d '{"model":"mycellm/swarm","messages":[{"role":"user","content":"Name one cause of the 1929 crash."}]}' \
  | python3 -c "
import json,sys
d=json.load(sys.stdin); p=d['plan']
print('  strategy:', p['strategy'])
for u in p['units']: print(f\"   • {u['role']:12} {u['target']}\")
for r in p['reasons']: print('   reason:', r)
for r in p['rejected']: print('   REJECTED:', r)
"

echo
echo "═══ 5. EGRESS GATE: same request, but with a credential in the prompt ═══"
curl -s -m 15 "${H_AUTH[@]}" -X POST http://127.0.0.1:8920/v1/node/plan \
  -H 'Content-Type: application/json' \
  -d '{"model":"mycellm/swarm","messages":[{"role":"user","content":"deploy using AKIAIOSFODNN7EXAMPLE now"}]}' \
  | python3 -c "
import json,sys
d=json.load(sys.stdin); p=d['plan']
print('  strategy:', p['strategy'], '| units:', len(p['units']))
for r in p['rejected']: print('   REJECTED:', r['target'], '::', r['reason'])
for r in p['reasons']: print('   reason:', r)
"

echo
echo "═══ 6. REAL SWARM EXECUTION against real models ═══"
curl -s -m 300 -X POST http://127.0.0.1:8920/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"mycellm/swarm","max_tokens":160,
       "messages":[{"role":"user","content":"In two sentences: why did the Tacoma Narrows Bridge collapse in 1940?"}]}' \
  > $D/swarm.json 2>&1
python3 - <<'PY'
import json
d = json.load(open("/tmp/myc-swarm-proof/swarm.json"))
if "error" in d:
    print("  ERROR:", json.dumps(d["error"])[:400]); raise SystemExit
m = d.get("mycellm", {})
print("  strategy      :", m.get("strategy"))
print("  proposers     :", m.get("proposers_planned"), "ok:", m.get("units_ok"), "failed:", m.get("units_failed"))
print("  synthesized_by:", m.get("synthesized_by"))
print("  degraded      :", m.get("degraded", False), m.get("degradation",""))
print("  tokens        :", d.get("usage"))
print("  elapsed_s     :", m.get("elapsed_s"))
print()
print("  ── ANSWER ──")
txt = d["choices"][0]["message"]["content"].strip()
for line in txt.splitlines()[:8]:
    print("   ", line)
PY

echo
echo "═══ 7. EGRESS GATE on the executing path (not just the planner) ═══"
curl -s -m 120 -w "\n  [HTTP %{http_code}]" -X POST http://127.0.0.1:8920/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"mycellm/swarm","max_tokens":40,
       "messages":[{"role":"user","content":"ship it with AKIAIOSFODNN7EXAMPLE"}]}' \
  | python3 -c "
import json,sys
raw=sys.stdin.read()
body=raw.rsplit('[HTTP',1)[0]
code=raw.rsplit('[HTTP',1)[1].strip(' ]\n') if '[HTTP' in raw else '?'
try:
    d=json.loads(body)
    e=d.get('error',{})
    print('  HTTP', code, '| type:', e.get('type'), '| code:', e.get('code'))
    print('  message:', str(e.get('message'))[:200])
except Exception as ex:
    print('  HTTP', code, 'unparsed:', body[:200])
"

echo
echo "═══ node log: swarm + relay lines ═══"
grep -iE "swarm|withdrew|relay model registered" $D/node.log | tail -6
echo
echo "(full log: $D/node.log)"
