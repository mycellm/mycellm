"""A/B probe: does a dead relay leave ghost models behind?

Uses ONLY the public RelayManager surface, so it runs unchanged against
0.7.1 and against the fix. That is the point: same script, two codebases.
"""
import asyncio, json, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, "src")
from mycellm.inference.relay import RelayManager

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            b = json.dumps({"data": [{"id": m, "owned_by": "local"} for m in self.server.models]}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a): pass

class Inf:
    class _M:
        def __init__(s, n): s.name = n
    def __init__(s): s._n = []
    @property
    def loaded_models(s): return [s._M(x) for x in s._n]
    async def load_model(s, p, *, name, **k): s._n.append(name); return True
    async def unload_model(s, name):
        if name in s._n: s._n.remove(name)
        return True

async def main():
    inf = Inf(); mgr = RelayManager(inf)
    httpd = HTTPServer(("127.0.0.1", 8795), H); httpd.models = ["llama3", "qwen3"]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    await mgr.add("http://127.0.0.1:8795", name="probe")
    before = sorted(inf._n)
    print(f"  after discovery : {before}")

    httpd.shutdown(); httpd.server_close()      # the endpoint dies
    await mgr.refresh()
    after = sorted(inf._n)
    print(f"  after it died   : {after}")

    ghosts = bool(after)
    print(f"\n  RESULT: {'GHOST MODELS PRESENT (bug)' if ghosts else 'models withdrawn (fixed)'}")
    return 1 if ghosts else 0

sys.exit(asyncio.run(main()))
