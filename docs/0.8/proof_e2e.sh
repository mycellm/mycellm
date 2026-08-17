#!/usr/bin/env bash
# END-TO-END PROOF: a real mycellm node, real HTTP relays, the real API.
set -u
APP=/Users/jupiter/data/projects/mycellm/app
D=/tmp/myc-e2e-proof
rm -rf $D && mkdir -p $D
cd $APP

# Two throwaway OpenAI-compatible endpoints, both serving "llama3".
python3 - <<'PY' > $D/relays.log 2>&1 &
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            b = json.dumps({"data":[{"id":m,"owned_by":"local"} for m in self.server.models]}).encode()
            self.send_response(200); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
        else:
            self.send_response(404); self.end_headers()
    def log_message(self,*a): pass
for port, models in ((8881, ["llama3","qwen3"]), (8882, ["llama3"])):
    s = HTTPServer(("127.0.0.1", port), H); s.models = models
    threading.Thread(target=s.serve_forever, daemon=True).start()
threading.Event().wait()
PY
RELAYPID=$!
sleep 2

export MYCELLM_CONFIG_DIR=$D MYCELLM_DATA_DIR=$D
.venv/bin/mycellm account create --name proof >/dev/null 2>&1
.venv/bin/mycellm device create --name default >/dev/null 2>&1
.venv/bin/mycellm serve --host 127.0.0.1 --port 8890 --quic-port 8891 --dht-port 8892 --no-dht \
    --relay http://127.0.0.1:8881 --relay http://127.0.0.1:8882 > $D/node.log 2>&1 &
NODEPID=$!

for i in $(seq 1 40); do
  curl -sf -m 2 http://127.0.0.1:8890/health >/dev/null 2>&1 && break
  sleep 2
done

KEY=$(.venv/bin/python -c "
from mycellm.config import get_settings
print(get_settings().api_key or '')" 2>/dev/null)

echo "═══ 1. Node is up ═══"
curl -s -m 5 http://127.0.0.1:8890/health | python3 -m json.tool 2>/dev/null | head -4

echo
echo "═══ 2. Advertised version (was hardcoded 0.1.0) ═══"
curl -s -m 5 http://127.0.0.1:8890/health | python3 -c "import json,sys; print('  version =', json.load(sys.stdin)['version'])"

echo
echo "═══ 3. Serving groups — BOTH relays, both llama3 deployments ═══"
curl -s -m 5 -H "Authorization: Bearer $KEY" http://127.0.0.1:8890/v1/node/groups | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(f\"  groups={d.get('count')} healthy={d.get('healthy_count')} deployments={d.get('deployment_count')}\")
for g in d.get('groups',[]):
    print(f\"   • {g['group_id']:<20} healthy={g['healthy']}  {g['endpoint']}\")
    for dep in g['deployments']:
        print(f\"       - {dep['model']:<26} {dep['deployment_id']}\")
" 2>&1

echo
echo "═══ 4. /v1/models — no collision, both llama3s present ═══"
curl -s -m 5 http://127.0.0.1:8890/v1/models | python3 -c "
import json,sys
d=json.load(sys.stdin)
ids=[m['id'] for m in d.get('data',[])]
print('  models:', ids)
print('  llama3-bearing:', [i for i in ids if 'llama3' in i])
" 2>&1

echo
echo "═══ 5. Kill relay :8881 → its models must be WITHDRAWN ═══"
python3 -c "
import urllib.request
try: urllib.request.urlopen('http://127.0.0.1:8881/__die', timeout=1)
except Exception: pass" 2>/dev/null
kill $RELAYPID 2>/dev/null; sleep 1
curl -s -m 10 -H "Authorization: Bearer $KEY" -X POST http://127.0.0.1:8890/v1/node/relay/refresh >/dev/null 2>&1
sleep 3
curl -s -m 5 -H "Authorization: Bearer $KEY" http://127.0.0.1:8890/v1/node/groups | python3 -c "
import json,sys
d=json.load(sys.stdin)
for g in d.get('groups',[]):
    print(f\"   • {g['group_id']:<20} healthy={g['healthy']}  deployments={len(g['deployments'])}\")
print(f\"  total deployments now: {d.get('deployment_count')}\")
" 2>&1

echo
echo "═══ node log: withdrawal lines ═══"
grep -iE "withdrew|withdraw" $D/node.log | tail -5

kill $NODEPID 2>/dev/null
wait $NODEPID 2>/dev/null
echo
echo "(node log at $D/node.log)"
