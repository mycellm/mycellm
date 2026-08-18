"""Fleet commands for the 0.8 fabric: groups, targets, plan, relays.

Until these existed, every 0.8 concept was reachable only on a node's own
loopback API. An operator could see that a peer advertised a model but not
which serving group backed it, and could not attach or detach a gateway
without shelling into the host.

The tests that matter here are not "the command returns data" — they are the
ones that pin down what a fleet admin key does *not* buy: no privacy override
on `node.plan`, no literal credential required on the wire for `relay.add`,
and no command outside the allowlist.
"""

import types

import pytest
from unittest.mock import AsyncMock, MagicMock

from mycellm.execution.models import Target
from mycellm.node import MycellmNode
from mycellm.protocol.envelope import MessageType
from mycellm.transport.messages import fleet_command

from tests.unit.test_fleet_admin import FakeNode


class FakeRelayManager:
    """Enough of RelayManager for the fleet surface, with call recording."""

    def __init__(self):
        self.added = []
        self.removed = []
        self.refreshed = []
        self._groups = [
            {
                "group_id": "grp_aurora",
                "name": "aurora",
                "url": "http://aurora.lan:8080",
                "online": True,
                "healthy": True,
                "deployments": [
                    {"deployment_id": "grp_aurora/qwen", "model": "qwen"},
                ],
            },
            {
                "group_id": "grp_dead",
                "name": "dead",
                "url": "http://dead.lan:8080",
                "online": False,
                "healthy": False,
                "deployments": [],
            },
        ]

    def get_groups(self):
        return self._groups

    def get_status(self):
        return [{"url": g["url"], "name": g["name"], "online": g["online"]}
                for g in self._groups]

    async def add(self, url, api_key="", name="", max_concurrent=32):
        self.added.append({"url": url, "api_key": api_key, "name": name})
        relay = MagicMock()
        relay.url = url
        relay.name = name or "relay"
        relay.group_id = "grp_new"
        relay.online = True
        relay.error = ""
        relay.registered = {"remote-model": "remote-model"}
        return relay

    async def remove(self, url):
        self.removed.append(url)
        return url == "http://aurora.lan:8080"

    async def refresh(self, url):
        self.refreshed.append(url)
        return 1

    async def refresh_all(self):
        self.refreshed.append("*")
        return 2


class FakeSecretStore:
    def resolve(self, ref):
        return "RESOLVED_SECRET" if ref.startswith("secret:") else ref


def _make_fabric_node(targets=None, relay_manager=None):
    node = FakeNode()
    node._handle_fleet_command = types.MethodType(
        MycellmNode._handle_fleet_command, node)
    node._execute_fleet_command = types.MethodType(
        MycellmNode._execute_fleet_command, node)
    node._FLEET_COMMANDS = MycellmNode._FLEET_COMMANDS
    node.relay_manager = relay_manager
    node.secret_store = FakeSecretStore()
    node._targets = targets if targets is not None else []
    node.execution_targets = lambda model="": node._targets
    node._own_network_ids = lambda: None
    return node


async def _run(node, command, params=None):
    protocol = MagicMock()
    protocol.reply_on_stream = AsyncMock()
    msg = fleet_command("admin_peer", command, params or {}, "correct_key_123")
    await node._handle_fleet_command(protocol, msg, stream_id=0)
    reply = protocol.reply_on_stream.call_args[0][1]
    assert reply.type == MessageType.FLEET_RESPONSE
    return reply.payload


# ── Allowlist ───────────────────────────────────────────────────────────


def test_fabric_commands_are_allowlisted():
    for cmd in ("node.groups", "node.targets", "node.plan",
                "relay.list", "relay.add", "relay.remove", "relay.refresh"):
        assert cmd in MycellmNode._FLEET_COMMANDS


@pytest.mark.asyncio
async def test_unlisted_fabric_command_still_refused():
    """The allowlist grew; it did not become a prefix match."""
    node = _make_fabric_node(relay_manager=FakeRelayManager())
    payload = await _run(node, "relay.wipe")
    assert payload["success"] is False
    assert "not allowed" in payload["error"]


# ── node.groups ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_node_groups_counts_health_separately_from_membership():
    node = _make_fabric_node(relay_manager=FakeRelayManager())
    data = (await _run(node, "node.groups"))["data"]
    assert data["count"] == 2
    # A dead group is still a group. Counting it as healthy is exactly the
    # ghost-model defect this release removed.
    assert data["healthy_count"] == 1
    assert data["deployment_count"] == 1


@pytest.mark.asyncio
async def test_node_groups_on_node_without_relays():
    node = _make_fabric_node(relay_manager=None)
    data = (await _run(node, "node.groups"))["data"]
    assert data == {"groups": [], "count": 0, "healthy_count": 0,
                    "deployment_count": 0}


# ── node.targets ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_node_targets_reports_remoteness_and_roles():
    node = _make_fabric_node(targets=[
        Target(model="local-a", kind="local", params_b=7.0),
        Target(model="remote-b", kind="group", serving_group_id="grp_aurora",
               params_b=35.0, roles=("proposer",)),
    ])
    data = (await _run(node, "node.targets"))["data"]
    assert data["count"] == 2
    by_model = {t["model"]: t for t in data["targets"]}
    assert by_model["local-a"]["remote"] is False
    assert by_model["remote-b"]["remote"] is True
    assert by_model["remote-b"]["roles"] == ["proposer"]
    assert by_model["remote-b"]["params_b"] == 35.0


# ── node.plan ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_node_plan_does_not_execute_and_returns_the_plan():
    node = _make_fabric_node(targets=[
        Target(model="local-a", kind="local", params_b=7.0),
        Target(model="remote-b", kind="group", params_b=35.0),
    ])
    payload = await _run(node, "node.plan", {
        "model": "mycellm/swarm",
        "messages": [{"role": "user", "content": "why is the sky blue"}],
    })
    assert payload["success"] is True
    plan = payload["data"]["plan"]
    assert plan["strategy"] == "swarm"
    assert payload["data"]["candidate_count"] == 2
    assert len(plan["units"]) == 3  # two proposers + synthesis


@pytest.mark.asyncio
async def test_node_plan_shows_egress_refusals_rather_than_hiding_them():
    """A target blocked by policy must be distinguishable from one absent."""
    node = _make_fabric_node(targets=[
        Target(model="local-a", kind="local"),
        Target(model="remote-b", kind="group"),
    ])
    payload = await _run(node, "node.plan", {
        "messages": [{"role": "user",
                      "content": "deploy with AKIAIOSFODNN7EXAMPLE please"}],
    })
    plan = payload["data"]["plan"]
    refused = {r["target"] for r in plan["rejected"]}
    assert any("remote-b" in t for t in refused)


@pytest.mark.asyncio
async def test_node_plan_cannot_override_privacy():
    """⚠️ THE FLEET KEY MUST NOT DISABLE EGRESS SCANNING.

    The HTTP `/v1/node/plan` honours an `X-Privacy-Override` header because it
    is the operator's own loopback surface. The fleet channel is a different
    grant: it lets a remote admin submit a prompt of their choosing and see
    where it would go. If an override were plumbed through, the same key that
    manages models would silently turn off the privacy gate for arbitrary
    text on someone else's machine.
    """
    node = _make_fabric_node(targets=[Target(model="remote-b", kind="group")])
    payload = await _run(node, "node.plan", {
        "messages": [{"role": "user", "content": "key AKIAIOSFODNN7EXAMPLE"}],
        # Every spelling an override might arrive under.
        "override_privacy": True,
        "privacy_override": "acknowledged",
        "X-Privacy-Override": "acknowledged",
    })
    plan = payload["data"]["plan"]
    assert plan["units"] == []
    assert plan["rejected"], "the remote target must still be refused"


# ── relay.* ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_relay_add_registers_and_reports_group_id():
    rm = FakeRelayManager()
    node = _make_fabric_node(relay_manager=rm)
    data = (await _run(node, "relay.add", {
        "url": "http://new.lan:8080", "name": "new",
    }))["data"]
    assert data["status"] == "added"
    assert data["relay"]["group_id"] == "grp_new"
    assert rm.added[0]["url"] == "http://new.lan:8080"


@pytest.mark.asyncio
async def test_relay_add_resolves_a_secret_reference_on_the_target_node():
    """So a fleet command can name a secret instead of carrying one."""
    rm = FakeRelayManager()
    node = _make_fabric_node(relay_manager=rm)
    await _run(node, "relay.add", {
        "url": "http://new.lan:8080", "api_key": "secret:openai",
    })
    assert rm.added[0]["api_key"] == "RESOLVED_SECRET"


@pytest.mark.asyncio
async def test_relay_add_without_url_fails_loudly():
    node = _make_fabric_node(relay_manager=FakeRelayManager())
    payload = await _run(node, "relay.add", {"name": "no-url"})
    assert payload["success"] is False
    assert "url required" in payload["error"]


@pytest.mark.asyncio
async def test_relay_remove_reports_not_found_without_claiming_success():
    rm = FakeRelayManager()
    node = _make_fabric_node(relay_manager=rm)
    hit = (await _run(node, "relay.remove",
                      {"url": "http://aurora.lan:8080"}))["data"]
    miss = (await _run(node, "relay.remove",
                       {"url": "http://nope.lan:8080"}))["data"]
    assert hit["status"] == "removed"
    assert miss["status"] == "not_found"


@pytest.mark.asyncio
async def test_relay_refresh_targets_one_relay_or_all():
    rm = FakeRelayManager()
    node = _make_fabric_node(relay_manager=rm)
    one = (await _run(node, "relay.refresh",
                      {"url": "http://aurora.lan:8080"}))["data"]
    every = (await _run(node, "relay.refresh"))["data"]
    assert one["models_discovered"] == 1
    assert every["models_discovered"] == 2
    assert rm.refreshed == ["http://aurora.lan:8080", "*"]


@pytest.mark.asyncio
async def test_relay_commands_fail_cleanly_without_a_relay_manager():
    node = _make_fabric_node(relay_manager=None)
    for cmd in ("relay.add", "relay.remove", "relay.refresh"):
        payload = await _run(node, cmd, {"url": "http://x.lan"})
        assert payload["success"] is False
        assert "Relay manager not initialized" in payload["error"]
    # relay.list is a read: an absent manager means "no relays", not an error.
    assert (await _run(node, "relay.list"))["data"] == {"relays": []}
