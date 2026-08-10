"""Configuration: device definitions, role registry, and engine settings."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

from .llm import LLMClient, OpenAICompatClient
from .spec import Complexity, Role, TaskCard, TaskType

EXAMPLE_CONFIG = """\
# hyphae.toml — device fleet for the Hyphae harness.
# Each device is an OpenAI-compatible endpoint; point them at mycellm nodes.
# Roles: architect (planning/review), builder (code gen), scout (speculation).
# Any missing role degrades gracefully (see hyphae-architecture.md §8.3).

[engine]
architect_review = true
consensus_for_critical = true
max_micro_iterations = 2
run_tests = true          # run the workspace test suite as verification
# test_command = "pytest -q"  # override auto-detection

[[devices]]
name = "studio"
role = "architect"
base_url = "http://studio:8420/v1"
model = "auto:capable"

[[devices]]
name = "laptop"
role = "builder"
base_url = "http://laptop:8420/v1"
model = "auto:fast"

[[devices]]
name = "ipad"
role = "scout"
base_url = "http://ipad:8420/v1"
model = "auto:tiny"
"""


class DeviceConfig(BaseModel):
    name: str
    role: Role
    base_url: str
    model: str
    api_key: str | None = None


class EngineConfig(BaseModel):
    architect_review: bool = True
    consensus_for_critical: bool = True
    max_micro_iterations: int = 2
    temperature: float = 0.2
    max_tokens: int = 4096
    # Test-execution verification: run the workspace test suite after each
    # code task; failures roll the files back and consume an iteration.
    run_tests: bool = True
    test_command: str | None = None  # None = auto-detect pytest
    test_timeout: float = 120.0


class HyphaeConfig(BaseModel):
    devices: list[DeviceConfig]
    engine: EngineConfig = Field(default_factory=EngineConfig)

    @classmethod
    def from_toml(cls, path: Path) -> HyphaeConfig:
        data = tomllib.loads(path.read_text())
        return cls.model_validate(data)

    @classmethod
    def single_endpoint(cls, base_url: str, model: str) -> HyphaeConfig:
        """Degraded mode: one endpoint plays every role."""
        return cls(
            devices=[
                DeviceConfig(name="solo", role=role, base_url=base_url, model=model)
                for role in Role
            ]
        )


class Device:
    """A configured device: its role plus a live LLM client."""

    def __init__(self, config: DeviceConfig, client: LLMClient) -> None:
        self.config = config
        self.client = client

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def role(self) -> Role:
        return self.config.role


# Fallback order when a role has no device (architecture doc §8.3):
# scout work falls to the builder, builder work falls to the architect, and
# architect work falls to the builder (extended local validation mode).
_FALLBACKS: dict[Role, list[Role]] = {
    Role.ARCHITECT: [Role.ARCHITECT, Role.BUILDER, Role.SCOUT],
    Role.BUILDER: [Role.BUILDER, Role.ARCHITECT, Role.SCOUT],
    Role.SCOUT: [Role.SCOUT, Role.BUILDER, Role.ARCHITECT],
}


class DeviceRegistry:
    def __init__(self, devices: list[Device]) -> None:
        if not devices:
            raise ValueError("at least one device is required")
        self._by_role: dict[Role, Device] = {}
        for d in devices:
            self._by_role.setdefault(d.role, d)
        self.devices = devices

    @classmethod
    def from_config(cls, config: HyphaeConfig) -> DeviceRegistry:
        return cls(
            [
                Device(dc, OpenAICompatClient(dc.base_url, dc.model, api_key=dc.api_key))
                for dc in config.devices
            ]
        )

    def get(self, role: Role) -> Device:
        for candidate in _FALLBACKS[role]:
            if candidate in self._by_role:
                return self._by_role[candidate]
        raise RuntimeError("unreachable: registry is never empty")

    def has_dedicated(self, role: Role) -> bool:
        """True if this role is served by its own physical device.

        A role that is missing (fallback) or shares a device with another
        role is not dedicated — e.g. speculation is pointless when the Scout
        is the same box as the Builder, since there is no idle compute.
        """
        device = self._by_role.get(role)
        if device is None:
            return False
        other_names = {d.name for r, d in self._by_role.items() if r != role}
        return device.name not in other_names

    def for_task(self, task: TaskCard) -> Device:
        """Route a task to a device by type and complexity (doc §8.2)."""
        if task.type == TaskType.ANALYSIS:
            return self.get(Role.SCOUT)
        if task.complexity in (Complexity.COMPLEX, Complexity.CRITICAL):
            return self.get(Role.ARCHITECT)
        return self.get(Role.BUILDER)
