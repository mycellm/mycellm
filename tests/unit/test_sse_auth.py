"""Tests that dashboard SSE streams authenticate by header, not by URL.

The dashboard's activity and log streams used to be opened with
`new EventSource(url + "?api_key=" + key)`, because `EventSource` cannot set
request headers. A credential in a URL is not a private credential: it lands
in browser history, `Referer` headers, proxy logs and server access logs, all
of which routinely outlive and out-scope the session that created it.

`ApiClient.stream()` now opens the stream with `fetch` through the same
`fetchWithAuth` helper as every other call, so the key rides in
`Authorization: Bearer …` and the URL stays clean.

Two things are guarded here:

* a source-level check over `web/src` that fails if any credential-shaped
  query parameter reappears in the TypeScript, or if `EventSource` is
  constructed again (which would force the key back into the URL); and
* server-level checks that the stream routes still sit behind auth — a
  `Bearer` header is accepted and an unauthenticated request is rejected.

The source check is deliberately a text scan rather than a JS unit test: the
`web/` package ships no test runner, and the property being defended ("no
credential ever appears in a URL") is a property of the source text.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from mycellm.api.app import create_app

WEB_SRC = Path(__file__).resolve().parents[2] / "web" / "src"

# Query-parameter names that would carry a credential. Matched as `name=`
# with no `?`/`&` anchor on purpose: the original bug built the string as
# `${separator}api_key=${...}`, so the `=` is what identifies a query
# parameter being assembled, not the preceding punctuation.
#
# The `=` must be tight against the name, which is what separates a query
# parameter from an ordinary assignment (`const apiKey = ...`, formatted with
# spaces by eslint/prettier). `(?![=>])` drops `==`/`===`/`=>`.
_CREDENTIAL_PARAM = re.compile(
    r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|session[_-]?token|password|secret)"
    r"=(?![=>])",
    re.IGNORECASE,
)


def _ts_sources() -> list[Path]:
    files = [
        p
        for suffix in ("*.ts", "*.tsx")
        for p in WEB_SRC.rglob(suffix)
        if "node_modules" not in p.parts
    ]
    assert files, f"no TypeScript sources found under {WEB_SRC}"
    return files


def test_no_credential_in_any_url_in_web_sources():
    """No credential-shaped query parameter may appear anywhere in web/src."""
    offenders: list[str] = []
    for path in _ts_sources():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            match = _CREDENTIAL_PARAM.search(line)
            if match:
                rel = path.relative_to(WEB_SRC.parents[1])
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "credential-shaped query parameter(s) found in the dashboard source. "
        "Credentials in a URL leak into browser history, Referer headers and "
        "proxy/access logs — send them in an Authorization header instead:\n"
        + "\n".join(offenders)
    )


def test_web_sources_do_not_construct_eventsource():
    """`EventSource` can't set headers, so using it forces the key into the URL."""
    offenders: list[str] = []
    for path in _ts_sources():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if re.search(r"\bnew\s+EventSource\b", line):
                rel = path.relative_to(WEB_SRC.parents[1])
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "EventSource cannot send an Authorization header, so reintroducing it "
        "means putting the api_key back in the query string. Use "
        "ApiClient.stream(), which reads text/event-stream from an authed "
        "fetch:\n" + "\n".join(offenders)
    )


def test_stream_helper_sends_auth_header():
    """`ApiClient.stream()` must route through the authed fetch helper."""
    client_ts = (WEB_SRC / "api" / "client.ts").read_text()
    stream_body = client_ts.split("stream(path: string)", 1)
    assert len(stream_body) == 2, "ApiClient.stream() not found in client.ts"
    body = stream_body[1]

    assert "this.fetchWithAuth(" in body, (
        "stream() must open the SSE connection via fetchWithAuth so it carries "
        "the same Authorization: Bearer header as every other request"
    )


# --- server side -----------------------------------------------------------

STREAM_PATHS = ["/v1/node/logs/stream", "/v1/node/activity/stream"]
API_KEY = "stream_test_secret_key"


class _OneShotQueue:
    """Yields a single event, then raises `CancelledError` like a disconnect.

    The stream routes loop forever on `await queue.get()` and only exit via
    `CancelledError`. httpx's `ASGITransport` does not stream — it runs the
    ASGI app to completion and buffers the body — so a genuinely endless
    generator would hang the test client rather than return a response. The
    second `get()` therefore raises exactly what a disconnecting client
    raises, which the route already handles, letting the response finish.
    """

    def __init__(self, item):
        self._items = [item]

    async def get(self):
        if self._items:
            return self._items.pop(0)
        raise asyncio.CancelledError()


class _FakeBroadcaster:
    """Stands in for node.log_broadcaster / node.activity."""

    def __init__(self, first_item):
        self.queue = _OneShotQueue(first_item)
        self.recent: list = []

    def subscribe(self):
        return self.queue

    def unsubscribe(self, q):  # noqa: ARG002 - interface parity
        pass


def _make_node():
    class _Event:
        def to_dict(self):
            return {"kind": "test"}

    node = MagicMock()
    node.peer_id = "sse_test_peer"
    node.log_broadcaster = _FakeBroadcaster({"msg": "hello"})
    node.activity = _FakeBroadcaster(_Event())
    return node


def _settings():
    settings = MagicMock()
    settings.api_key = API_KEY
    settings.public = False
    return settings


def _app():
    with patch("mycellm.config.get_settings", return_value=_settings()):
        return create_app(_make_node())


@pytest.mark.parametrize("path", STREAM_PATHS)
async def test_stream_rejects_unauthenticated_request(path):
    """An unauthenticated stream request is refused before the route runs."""
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)

    assert response.status_code == 401, (
        f"{path} must stay behind auth; got {response.status_code}"
    )


@pytest.mark.parametrize("path", STREAM_PATHS)
async def test_stream_rejects_wrong_bearer_token(path):
    """A wrong key in the header is refused too — the header isn't a bypass."""
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path, headers={"Authorization": "Bearer wrong"})

    assert response.status_code == 401


@pytest.mark.parametrize("path", STREAM_PATHS)
async def test_stream_accepts_authorization_header(path):
    """The key the dashboard now sends as a header is accepted by the server."""
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path, headers={"Authorization": f"Bearer {API_KEY}"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert b"data:" in response.content
