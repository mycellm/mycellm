from pathlib import Path

from conftest import MockLLM, make_device
from hyphae.config import EXAMPLE_CONFIG, DeviceRegistry, HyphaeConfig
from hyphae.spec import Complexity, Role, TaskCard, TaskType


def card(**kw) -> TaskCard:
    return TaskCard(id="t", description="d", **kw)


def full_registry() -> DeviceRegistry:
    return DeviceRegistry(
        [
            make_device("studio", Role.ARCHITECT, MockLLM()),
            make_device("laptop", Role.BUILDER, MockLLM()),
            make_device("ipad", Role.SCOUT, MockLLM()),
        ]
    )


def test_example_config_parses(tmp_path: Path):
    path = tmp_path / "hyphae.toml"
    path.write_text(EXAMPLE_CONFIG)
    config = HyphaeConfig.from_toml(path)
    assert {d.role for d in config.devices} == set(Role)
    assert config.engine.architect_review is True


def test_single_endpoint_covers_all_roles():
    config = HyphaeConfig.single_endpoint("http://localhost:8420/v1", "auto")
    assert {d.role for d in config.devices} == set(Role)
    assert all(d.name == "solo" for d in config.devices)


def test_routing_by_type_and_complexity():
    registry = full_registry()
    assert registry.for_task(card(type=TaskType.ANALYSIS)).name == "ipad"
    assert registry.for_task(card(complexity=Complexity.MEDIUM)).name == "laptop"
    assert registry.for_task(card(complexity=Complexity.COMPLEX)).name == "studio"
    assert registry.for_task(card(complexity=Complexity.CRITICAL)).name == "studio"


def test_role_fallback_when_degraded():
    builder_only = DeviceRegistry([make_device("laptop", Role.BUILDER, MockLLM())])
    assert builder_only.get(Role.ARCHITECT).name == "laptop"
    assert builder_only.get(Role.SCOUT).name == "laptop"
    assert not builder_only.has_dedicated(Role.SCOUT)
    assert not builder_only.has_dedicated(Role.ARCHITECT)


def test_dedicated_requires_own_device():
    registry = full_registry()
    assert registry.has_dedicated(Role.SCOUT)

    shared = DeviceRegistry(
        [
            make_device("solo", Role.ARCHITECT, MockLLM()),
            make_device("solo", Role.BUILDER, MockLLM()),
            make_device("solo", Role.SCOUT, MockLLM()),
        ]
    )
    assert not shared.has_dedicated(Role.SCOUT)
