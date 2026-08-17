"""WORKING PROOF: relay lifecycle against real HTTP endpoints.

Unlike the unit tests, this drives `RelayManager._discover_models` — the real
code path, over real sockets — against two throwaway OpenAI-compatible servers
that we start, kill, and restart. It is the proof that D1/D2/D3 are actually
fixed in the path production uses, not just in methods a test double calls.

Run:  .venv/bin/python proof_relay.py
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "src")
from mycellm.inference.relay import RelayManager  # noqa: E402


class FakeOpenAI(BaseHTTPRequestHandler):
    models: list = []

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            body = json.dumps({"data": [{"id": m, "owned_by": "local"}
                                        for m in self.server.models]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, *a):  # silence
        pass


class Server:
    def __init__(self, port, models):
        self.port = port
        self.httpd = HTTPServer(("127.0.0.1", port), FakeOpenAI)
        self.httpd.models = list(models)
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def set_models(self, models):
        self.httpd.models = list(models)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Inference:
    """Records load/unload the way InferenceManager would."""
    class _M:
        def __init__(self, name): self.name = name

    def __init__(self):
        self._names = []

    @property
    def loaded_models(self):
        return [self._M(n) for n in self._names]

    async def load_model(self, path, *, name, **kw):
        self._names.append(name); return True

    async def unload_model(self, name):
        if name in self._names:
            self._names.remove(name)
        return True


OK, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(label, cond, detail=""):
    results.append(cond)
    print(f"  {OK if cond else FAIL} {label}" + (f" — {detail}" if detail else ""))


async def main():
    inf = Inference()
    mgr = RelayManager(inf)

    print("\n╔══ D1: a relay that dies must withdraw its models ══")
    a = Server(8791, ["llama3", "qwen3"])
    r = await mgr.add(a.url, name="alpha")
    check("two models discovered over HTTP", sorted(r.registered) ==
          ["relay:llama3", "relay:qwen3"], str(sorted(r.registered)))
    check("models are loaded", len(inf._names) == 2, str(sorted(inf._names)))

    a.stop()                      # endpoint dies
    await mgr.refresh()
    check("relay marked offline", r.online is False)
    check("models WITHDRAWN, not ghosts", inf._names == [], str(inf._names))
    check("ownership record cleared", r.registered == {}, str(r.registered))
    check("healthy is False", r.healthy is False)

    print("\n╔══ D1b: recovery restores them ══")
    a2 = Server(8791, ["llama3", "qwen3"])
    await mgr.refresh()
    check("relay back online", r.online is True)
    check("models re-registered", sorted(inf._names) ==
          ["relay:llama3", "relay:qwen3"], str(sorted(inf._names)))

    print("\n╔══ D2: two relays serving the same model ══")
    b = Server(8792, ["llama3"])
    rb = await mgr.add(b.url, name="bravo")
    check("bravo registered its own llama3", list(rb.registered) ==
          ["relay:bravo:llama3"], str(list(rb.registered)))
    check("both llama3 deployments exist",
          sum(1 for n in inf._names if n.endswith("llama3")) == 2,
          str(sorted(inf._names)))
    check("deployment ids are distinct",
          r.deployment_id("relay:llama3") != rb.deployment_id("relay:bravo:llama3"))

    print("\n╔══ D2b: removing one leaves the other serving ══")
    await mgr.remove(a2.url)
    check("bravo's model survives alpha's removal",
          "relay:bravo:llama3" in inf._names, str(sorted(inf._names)))
    check("alpha's models gone", "relay:llama3" not in inf._names)
    a2.stop()

    print("\n╔══ D3: a model withdrawn upstream is unregistered ══")
    b.set_models([])              # bravo now serves nothing
    await mgr.refresh()
    check("withdrawn model unregistered", inf._names == [], str(inf._names))
    check("ownership record empty", rb.registered == {}, str(rb.registered))

    b.set_models(["phi4"])        # and a new one appears
    await mgr.refresh()
    check("newly-added upstream model registered",
          list(rb.registered) == ["relay:phi4"], str(list(rb.registered)))
    b.stop()

    print(f"\n  ── {sum(results)}/{len(results)} checks passed ──\n")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
