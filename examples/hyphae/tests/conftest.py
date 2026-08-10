from __future__ import annotations

import json

import pytest

from hyphae.config import Device, DeviceConfig, DeviceRegistry
from hyphae.spec import Role
from hyphae.workspace import Workspace


class MockLLM:
    """Scriptable LLM: either a fixed response queue or a router callable
    that receives the message list and returns a response string."""

    def __init__(self, responses: list[str] | None = None, router=None) -> None:
        self.responses = list(responses or [])
        self.router = router
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, messages, *, temperature=0.2, max_tokens=4096) -> str:
        self.calls.append(messages)
        if self.router is not None:
            return self.router(messages)
        return self.responses.pop(0)


def gen_response(files: dict[str, str], notes: str = "done") -> str:
    return json.dumps(
        {"files": [{"path": p, "content": c} for p, c in files.items()], "notes": notes}
    )


def review_response(approved: bool, feedback: str = "") -> str:
    return json.dumps({"approved": approved, "feedback": feedback})


def make_device(name: str, role: Role, client: MockLLM) -> Device:
    config = DeviceConfig(name=name, role=role, base_url="http://test", model="mock")
    return Device(config, client)


def make_registry(
    architect: MockLLM, builder: MockLLM, scout: MockLLM
) -> DeviceRegistry:
    return DeviceRegistry(
        [
            make_device("studio", Role.ARCHITECT, architect),
            make_device("laptop", Role.BUILDER, builder),
            make_device("ipad", Role.SCOUT, scout),
        ]
    )


@pytest.fixture
def workspace(tmp_path) -> Workspace:
    return Workspace(tmp_path)
