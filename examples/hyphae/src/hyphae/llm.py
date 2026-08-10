"""LLM client abstraction over OpenAI-compatible endpoints (e.g. mycellm :8420/v1)."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

import httpx

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


class LLMClient(Protocol):
    """Minimal chat-completion interface every device client implements."""

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str: ...


class OpenAICompatClient:
    """Chat client for any OpenAI-compatible endpoint.

    Works against a mycellm node (`http://host:8420/v1`), Ollama, vLLM, or the
    OpenAI API itself.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.AsyncClient(timeout=timeout, headers=headers)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        resp = await self._http.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"] or ""
        return strip_thinking(content)

    async def aclose(self) -> None:
        await self._http.aclose()


def strip_thinking(text: str) -> str:
    """Remove `<think>...</think>` blocks emitted by reasoning models."""
    return THINK_RE.sub("", text).strip()


def extract_json(text: str) -> Any:
    """Pull a JSON value out of an LLM response.

    Tries, in order: the whole text, fenced ```json blocks, and the first
    balanced top-level object or array. Raises ValueError if nothing parses.
    """
    text = strip_thinking(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for match in FENCE_RE.finditer(text):
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
    candidate = _first_balanced(text)
    if candidate is not None:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    raise ValueError(f"no JSON found in response: {text[:200]!r}")


FILE_HEADER_RE = re.compile(r"^#{2,4} FILE:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
NOTES_HEADER_RE = re.compile(r"^#{2,4} NOTES[ \t]*$", re.MULTILINE)
FENCE_OPEN_RE = re.compile(r"^```[\w+-]*[ \t]*\n", re.MULTILINE)


def parse_file_blocks(text: str) -> tuple[dict[str, str], str] | None:
    """Parse the fenced-file response format:

        ### FILE: relative/path.py
        ```
        <content>
        ```
        ### NOTES
        <summary>

    Returns (files, notes), or None when the text contains no FILE/NOTES
    headers (callers then fall back to the JSON contract).
    """
    text = strip_thinking(text)
    headers = [("file", m) for m in FILE_HEADER_RE.finditer(text)]
    notes_match = NOTES_HEADER_RE.search(text)
    if notes_match:
        headers.append(("notes", notes_match))
    if not headers:
        return None
    headers.sort(key=lambda km: km[1].start())

    files: dict[str, str] = {}
    notes = ""
    for i, (kind, match) in enumerate(headers):
        end = headers[i + 1][1].start() if i + 1 < len(headers) else len(text)
        body = text[match.end() : end]
        if kind == "notes":
            notes = body.strip()
        else:
            content = _extract_fenced(body)
            if content is not None:
                files[match.group(1).strip()] = content
    return files, notes


def _extract_fenced(body: str) -> str | None:
    """File content from a body: the fenced block, or the bare text."""
    open_match = FENCE_OPEN_RE.search(body)
    if open_match is None:
        stripped = body.strip()
        return f"{stripped}\n" if stripped else None
    rest = body[open_match.end() :]
    close = rest.rfind("\n```")
    content = rest[:close] if close != -1 else rest.rstrip()
    if not content.strip():
        return None
    return content if content.endswith("\n") else f"{content}\n"


def _first_balanced(text: str) -> str | None:
    """Return the first balanced {...} or [...] span, respecting strings."""
    openers = {"{": "}", "[": "]"}
    start = next((i for i, ch in enumerate(text) if ch in openers), None)
    if start is None:
        return None
    closer = openers[text[start]]
    opener = text[start]
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
