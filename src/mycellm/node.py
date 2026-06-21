"""MycellmNode — daemon entrypoint composing all subsystems."""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import time
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from mycellm.cli.banner import styled_tag
from mycellm.config import get_settings
from mycellm.identity.certs import DeviceCert
from mycellm.identity.keys import AccountKey, DeviceKey
from mycellm.identity.peer_id import peer_id_from_public_key
from mycellm.inference.manager import InferenceManager
from mycellm.protocol.capabilities import Capabilities, HardwareInfo
from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.protocol.errors import ErrorCode
from mycellm.router.registry import PeerRegistry
from mycellm.router.chain import ChainBuilder
from mycellm.router.health import HealthChecker
from mycellm.router.model_resolver import ModelResolver
from mycellm.transport.tls import generate_self_signed_cert
from mycellm.transport.auth import build_hello_ack, verify_hello_message
from mycellm.accounting.reputation import AdmissionResult, ReputationTracker
from mycellm.accounting.receipts import (
    ReceiptValidator,
    build_receipt_data,
    sign_receipt,
    verify_receipt_signature,
)
from mycellm.transport.messages import (
    inference_response,
    error_message,
    pong_message,
    inference_stream_chunk,
    inference_done,
    fleet_response,
)
from mycellm.transport.connection import PeerConnection, PeerState
from mycellm.transport.peer_manager import PeerManager
from mycellm.activity import ActivityTracker, EventType
from mycellm.federation import FederationManager

logger = logging.getLogger("mycellm")
console = Console()


class LogBroadcaster(logging.Handler):
    """Captures log records and broadcasts to SSE subscribers."""

    def __init__(self, maxlen: int = 200):
        super().__init__()
        self._buffer: list[dict] = []
        self._maxlen = maxlen
        self._subscribers: list[asyncio.Queue] = []

    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        self._buffer.append(entry)
        if len(self._buffer) > self._maxlen:
            self._buffer = self._buffer[-self._maxlen:]
        for q in self._subscribers:
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                pass  # drop if subscriber is slow

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    @property
    def recent(self) -> list[dict]:
        return list(self._buffer)


class MycellmNode:
    """Main daemon that composes all subsystems."""

    def __init__(
        self,
        api_host: str = "0.0.0.0",
        api_port: int = 8420,
        quic_port: int = 8421,
        dht_port: int = 8422,
        device_name: str = "default",
        enable_dht: bool = True,
    ):
        self.api_host = api_host
        self.api_port = api_port
        self.quic_port = quic_port
        self.dht_port = dht_port
        self._quic_host = api_host if api_host != "127.0.0.1" else "127.0.0.1"
        self.device_name = device_name
        self.enable_dht = enable_dht
        self._running = False
        self._start_time = 0.0
        self._settings = get_settings()

        # Identity
        self.account_key: AccountKey | None = None
        self.device_key: DeviceKey | None = None
        self.device_cert: DeviceCert | None = None
        self.peer_id: str = ""
        self.capabilities = Capabilities()

        # Subsystems
        self.inference = InferenceManager(
            max_concurrent=self._settings.max_concurrent_inferences
        )
        self.activity = ActivityTracker()
        self.registry = PeerRegistry()
        self.health_checker = HealthChecker(self.registry, activity=self.activity)
        self.chain_builder = ChainBuilder(self.registry, health_checker=self.health_checker)
        self.model_resolver = ModelResolver(self.registry)
        self.ledger = None  # initialized in run()
        self.reputation = ReputationTracker()
        self.receipt_validator = ReceiptValidator()
        # Separate validator for the tracker role (settling co-signed receipts):
        # keeps its replay/rate window independent of the consumer-side one, so
        # a node that is both consumer and tracker doesn't double-burn request_ids.
        self.tracker_validator = ReceiptValidator()

        # Peer manager
        self.peer_manager = PeerManager(self)

        # Transport state
        self._quic_server = None
        self._tls_cert_path: Path | None = None
        self._tls_key_path: Path | None = None
        self._peer_connections: dict[str, PeerConnection] = {}
        self._dht_node = None
        self._peer_exchange_task = None

        # Log broadcaster for dashboard SSE
        self.log_broadcaster = LogBroadcaster()

        # Federation
        self.federation: FederationManager | None = None

        # Managed node registry (bootstrap/admin node tracks announced nodes)
        self.node_registry: dict[str, dict] = {}  # peer_id -> node info

        # Encrypted secret store (initialized after identity load)
        self.secret_store = None

        # Relay manager — auto-discovers models from external OpenAI-compatible APIs
        self.relay_manager = None

        # API server ref for shutdown
        self._api_server = None

        # Resilience — heartbeat watchdog + network self-heal (created in run())
        self._watchdog = None
        self._heartbeat_task = None
        self._selfheal_task = None
        self._force_announce: asyncio.Event | None = None
        self._addr_sig: tuple | None = None

    @property
    def uptime(self) -> float:
        if self._start_time == 0:
            return 0.0
        return time.time() - self._start_time

    def _setup_logging(self) -> None:
        level = getattr(logging, self._settings.log_level.upper(), logging.INFO)
        logging.basicConfig(
            level=level,
            format="%(message)s",
            handlers=[
                RichHandler(console=console, show_time=True, show_path=False),
                self.log_broadcaster,
            ],
        )

    def _load_identity(self) -> None:
        """Load account + device keys and certificate."""
        self._settings.ensure_dirs()

        if not (self._settings.keys_dir / "account.key").exists():
            raise RuntimeError("No account found. Run 'mycellm account create' first.")

        self.account_key = AccountKey.load(self._settings.keys_dir)
        logger.info(f"{styled_tag('BOOT')} Account loaded")

        if not (self._settings.keys_dir / f"device-{self.device_name}.key").exists():
            raise RuntimeError(
                f"No device '{self.device_name}' found. Run 'mycellm device create' first."
            )

        self.device_key = DeviceKey.load(self._settings.keys_dir, self.device_name)
        self.device_cert = DeviceCert.load(self._settings.certs_dir, self.device_name)
        self.peer_id = peer_id_from_public_key(self.device_key.public_key)

        logger.info(
            f"{styled_tag('BOOT')} Device '{self.device_name}' loaded "
            f"(peer: {self.peer_id[:16]}...)"
        )

        # Initialize encrypted secret store
        from mycellm.secrets import SecretStore
        self.secret_store = SecretStore(
            self._settings.data_dir / "secrets.json",
            self.account_key,
        )
        n_secrets = len(self.secret_store.list_names())
        if n_secrets:
            logger.info(f"{styled_tag('SECURITY')} Secret store loaded ({n_secrets} key(s))")

    def _detect_hardware(self) -> HardwareInfo:
        """Detect GPU hardware (CUDA, Metal, or CPU)."""
        import platform
        import subprocess

        # NVIDIA CUDA
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                line = result.stdout.strip().split("\n")[0]
                name, vram = line.split(",")
                return HardwareInfo(
                    gpu=name.strip(), vram_gb=float(vram.strip()) / 1024, backend="cuda"
                )
        except Exception:
            pass

        # Apple Metal (macOS ARM64)
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            try:
                result = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    total_ram_gb = int(result.stdout.strip()) / (1024 ** 3)
                    # Metal uses unified memory — report total RAM as available
                    chip = "Apple Silicon"
                    brand = subprocess.run(
                        ["sysctl", "-n", "machdep.cpu.brand_string"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if brand.returncode == 0:
                        chip = brand.stdout.strip()
                    return HardwareInfo(
                        gpu=chip, vram_gb=round(total_ram_gb, 1), backend="metal"
                    )
            except Exception:
                pass

        return HardwareInfo(gpu="CPU", vram_gb=0.0, backend="cpu")

    async def _init_accounting(self) -> None:
        """Initialize the database and credit ledger."""
        from mycellm.storage import init_database, LedgerRepository, NodeRegistryRepository, GrowthRepository

        await init_database(
            db_url=self._settings.db_url,
            db_path=str(self._settings.db_path),
        )
        self.ledger = LedgerRepository()
        self.node_registry_repo = NodeRegistryRepository()
        self.growth_repo = GrowthRepository()
        await self.ledger.ensure_account(self.peer_id, self._settings.initial_credits)

        # Tracker treasury: if this node is a network's source of truth (a
        # public bootstrap is the authority for the public net), designate
        # itself effectively-unlimited credit so it never runs out spending as
        # the consumer for demo traffic. Chat-client rate limits are enforced
        # separately at the gateway, independent of this ledger.
        if getattr(self._settings, "public", False):
            try:
                from mycellm.storage.repositories import NetworkLedgerRepository
                from mycellm.accounting.tracker import TRACKER_TREASURY
                treasury_repo = NetworkLedgerRepository()
                for _net in ("public", ""):
                    await treasury_repo.grant_treasury(self.peer_id, _net, TRACKER_TREASURY)
                logger.info(f"{styled_tag('CREDIT')} Tracker treasury designated for public net")
            except Exception as e:
                logger.warning(f"Tracker treasury grant failed: {e}")

        # Migrate JSON registry to DB if it exists
        await self._migrate_json_registry()

        logger.info(f"{styled_tag('CREDIT')} Ledger initialized (balance: {self._settings.initial_credits:.2f})")

    async def _migrate_json_registry(self) -> None:
        """One-time migration: import node_registry.json into DB if it exists."""
        import json
        path = self._settings.data_dir / "node_registry.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data, dict) and data:
                    count = await self.node_registry_repo.import_from_dict(data)
                    # Rename so we don't re-import
                    path.rename(path.with_suffix(".json.migrated"))
                    logger.info(f"{styled_tag('BOOT')} Migrated {count} node(s) from JSON registry to DB")
            except Exception as e:
                logger.debug(f"JSON registry migration skipped: {e}")

    async def _start_transport(self) -> None:
        """Start the QUIC transport server."""
        from mycellm.transport.quic import create_quic_server

        self._tls_cert_path, self._tls_key_path = generate_self_signed_cert(
            cert_path=self._settings.data_dir / "tls" / "cert.pem",
            key_path=self._settings.data_dir / "tls" / "key.pem",
        )

        # QUIC binds to same host as API (0.0.0.0 for network-accessible nodes)
        quic_host = self.api_host if self.api_host != "127.0.0.1" else self._settings.quic_host
        self._quic_host = quic_host
        self._quic_server = await create_quic_server(
            host=quic_host,
            port=self.quic_port,
            cert_path=self._tls_cert_path,
            key_path=self._tls_key_path,
            message_handler=self._handle_peer_message,
            on_connection=self._on_peer_connected,
        )
        logger.info(f"{styled_tag('P2P')} QUIC transport listening on :{self.quic_port}")

    async def _on_peer_connected(self, protocol) -> None:
        """Handle a new inbound QUIC connection."""
        self.activity.record(EventType.PEER_CONNECTED, source="inbound")
        logger.debug("New inbound QUIC connection")

    async def _handle_peer_message(self, protocol, msg: MessageEnvelope, stream_id: int) -> None:
        """Handle incoming messages from peers."""

        if msg.type == MessageType.NODE_HELLO:
            try:
                hello, _ = verify_hello_message(msg)
                ack = build_hello_ack(self.device_key, self.device_cert, self.capabilities, request_id=msg.id)
                await protocol.reply_on_stream(stream_id, ack)

                conn = PeerConnection(
                    peer_id=hello.peer_id,
                    protocol=protocol,
                    hello=hello,
                    state=PeerState.AUTHENTICATED,
                )
                self._peer_connections[hello.peer_id] = conn
                # Promote to ROUTABLE *before* registering so the registry
                # entry copies the right state — peers_for_model() filters
                # by state and we don't want a window where a freshly-
                # authenticated peer is invisible to routing.
                conn.state = PeerState.ROUTABLE
                self.registry.register(
                    hello.peer_id,
                    connection=conn,
                    capabilities=hello.capabilities,
                )

                # Capture peer's QUIC source address for peer exchange
                reg_entry = self.registry.get(hello.peer_id)
                if reg_entry:
                    if protocol._peer_addr and not reg_entry.addresses:
                        host = protocol._peer_addr[0]
                        reg_entry.addresses = [f"{host}:8421"]
                    reg_entry.network_ids = hello.network_ids

                self.activity.record(
                    EventType.PEER_CONNECTED,
                    peer_id=hello.peer_id,
                    role=hello.cert.role,
                )
                logger.info(
                    f"{styled_tag('P2P')} Peer authenticated: {hello.peer_id[:16]}... "
                    f"(role={hello.cert.role})"
                )
            except Exception as e:
                logger.warning(f"{styled_tag('SECURITY')} Auth failed: {e}")
                err = error_message(self.peer_id, msg.id, ErrorCode.AUTH_FAILED, str(e))
                await protocol.reply_on_stream(stream_id, err)

        elif msg.type == MessageType.PING:
            reply = pong_message(self.peer_id, msg.id)
            await protocol.reply_on_stream(stream_id, reply)
            # Liveness signal: if a DISCONNECTED peer is still sending PINGs,
            # the QUIC session is alive — restore it to ROUTABLE immediately
            # rather than waiting for the next HealthChecker cycle.
            _ping_peer_id = msg.from_peer
            if _ping_peer_id and _ping_peer_id in self._peer_connections:
                _conn = self._peer_connections[_ping_peer_id]
                if not getattr(getattr(_conn, 'protocol', None), '_is_closed', True):
                    _entry = self.registry.get(_ping_peer_id)
                    if _entry and _entry.state == PeerState.DISCONNECTED:
                        _entry.state = PeerState.ROUTABLE
                        _conn.state = PeerState.ROUTABLE
                        _entry.last_seen = time.time()
                        logger.debug(
                            f"{styled_tag('P2P')} Peer {_ping_peer_id[:8]} restored "
                            f"ROUTABLE via incoming PING"
                        )

        elif msg.type == MessageType.INFERENCE_REQ:
            await self._handle_inference_request(protocol, msg, stream_id)

        elif msg.type == MessageType.INFERENCE_RELAY:
            await self._handle_relay(protocol, msg, stream_id)

        elif msg.type == MessageType.CREDIT_RECEIPT:
            await self._handle_credit_receipt(msg)

        elif msg.type == MessageType.PEER_EXCHANGE:
            self._handle_peer_exchange(msg)

        elif msg.type == MessageType.PEER_ANNOUNCE:
            caps = msg.payload.get("capabilities", {})
            addrs = msg.payload.get("addresses", [])
            from mycellm.protocol.capabilities import Capabilities
            _ann_conn = self._peer_connections.get(msg.from_peer)
            # If the QUIC session for this peer is alive, ensure its state is
            # restored to ROUTABLE before registering — otherwise the
            # connection= parameter has no effect on a DISCONNECTED entry.
            if _ann_conn and not getattr(getattr(_ann_conn, 'protocol', None), '_is_closed', True):
                _ann_conn.state = PeerState.ROUTABLE
            self.registry.register(
                msg.from_peer,
                connection=_ann_conn,
                capabilities=Capabilities.from_dict(caps),
                addresses=addrs,
            )
            logger.info(f"{styled_tag('DHT')} Peer announced: {msg.from_peer[:16]}...")

        elif msg.type == MessageType.FLEET_COMMAND:
            await self._handle_fleet_command(protocol, msg, stream_id)

        elif msg.type in (
            MessageType.FLEET_RESPONSE,
            MessageType.INFERENCE_RESP,
            MessageType.INFERENCE_STREAM,
            MessageType.INFERENCE_DONE,
            MessageType.PONG,
            MessageType.ERROR,
        ):
            # These are responses — should be handled by send_and_wait
            conn = self._peer_connections.get(msg.from_peer)
            if conn:
                conn.handle_response(msg)

    # ── Fleet admin command handling ──

    # Allowlisted fleet commands (restricted scope — no secrets, no key changes)
    _FLEET_COMMANDS = {
        "node.status", "node.config",
        "model.list", "model.load", "model.unload", "model.scope",
    }

    async def _handle_fleet_command(self, protocol, msg: MessageEnvelope, stream_id: int) -> None:
        """Handle a fleet management command relayed via bootstrap."""
        import hmac

        command = msg.payload.get("command", "")
        params = msg.payload.get("params", {})
        incoming_key = msg.payload.get("fleet_admin_key", "")

        # Reject if no fleet admin key configured on this node
        if not self._settings.fleet_admin_key:
            logger.warning(f"{styled_tag('FLEET')} Fleet command rejected: no fleet_admin_key configured")
            reply = fleet_response(self.peer_id, msg.id, False, error="Fleet admin key not configured on this node")
            await protocol.reply_on_stream(stream_id, reply)
            return

        # Constant-time key comparison
        if not hmac.compare_digest(incoming_key, self._settings.fleet_admin_key):
            logger.warning(f"{styled_tag('FLEET')} Fleet command rejected: invalid key")
            reply = fleet_response(self.peer_id, msg.id, False, error="Invalid fleet admin key")
            await protocol.reply_on_stream(stream_id, reply)
            return

        # Check command allowlist
        if command not in self._FLEET_COMMANDS:
            logger.warning(f"{styled_tag('FLEET')} Fleet command rejected: disallowed command '{command}'")
            reply = fleet_response(self.peer_id, msg.id, False, error=f"Command not allowed: {command}")
            await protocol.reply_on_stream(stream_id, reply)
            return

        try:
            data = await self._execute_fleet_command(command, params)
            reply = fleet_response(self.peer_id, msg.id, True, data=data)
            logger.info(f"{styled_tag('FLEET')} Fleet command executed: {command}")
        except Exception as e:
            logger.error(f"{styled_tag('FLEET')} Fleet command failed: {command}: {e}")
            reply = fleet_response(self.peer_id, msg.id, False, error=str(e))
        await protocol.reply_on_stream(stream_id, reply)

    async def _execute_fleet_command(self, command: str, params: dict) -> dict:
        """Execute an allowlisted fleet command and return result data."""
        if command == "node.status":
            status = self.get_status()
            status["credits"] = await self.get_credits()
            return status

        elif command == "node.config":
            # Redacted config — no secrets, no keys
            return {
                "node_name": self._settings.node_name,
                "bootstrap_peers": self._settings.bootstrap_peers,
                "log_level": self._settings.log_level,
                "no_log_inference": self._settings.no_log_inference,
                "telemetry": self._settings.telemetry,
                "max_public_requests_per_hour": self._settings.max_public_requests_per_hour,
                "relay_backends": bool(self._settings.relay_backends),
                "api_key_set": bool(self._settings.api_key),
                "hf_token_set": bool(self._settings.hf_token),
            }

        elif command == "model.list":
            models = []
            for m in self.inference.loaded_models:
                d = m.to_dict()
                # Always include scope in fleet responses (to_dict omits it when "home")
                d["scope"] = m.scope
                models.append(d)
            return {"models": models}

        elif command == "model.load":
            model_path = params.get("model_path", "")
            name = params.get("name")
            backend_type = params.get("backend", "llama.cpp")
            if backend_type == "llama.cpp" and not model_path:
                raise ValueError("model_path required for llama.cpp backend")
            loaded_name = await self.inference.load_model(
                model_path, name=name, backend_type=backend_type,
                ctx_len=params.get("ctx_len", 4096),
                timeout=params.get("timeout", 120),
                api_base=params.get("api_base", ""),
                api_key=params.get("api_key", ""),
                api_model=params.get("api_model", ""),
            )
            scope = params.get("scope", "home")
            info = self.inference._model_info.get(loaded_name)
            if info:
                info.scope = scope
            self.capabilities.models = self.inference.loaded_models
            self.capabilities.role = "seeder" if self.inference.loaded_models else "consumer"
            await self.announce_capabilities()
            return {"status": "loaded", "model": loaded_name}

        elif command == "model.unload":
            model_name = params.get("model", "")
            if not model_name:
                raise ValueError("model name required")
            await self.inference.unload_model(model_name)
            self.capabilities.models = self.inference.loaded_models
            self.capabilities.role = "seeder" if self.inference.loaded_models else "consumer"
            await self.announce_capabilities()
            return {"status": "unloaded", "model": model_name}

        elif command == "model.scope":
            model_name = params.get("model", "")
            scope = params.get("scope", "home")
            if not model_name:
                raise ValueError("model name required")
            if scope not in ("home", "public"):
                raise ValueError("scope must be 'home' or 'public'")
            info = self.inference._model_info.get(model_name)
            if not info:
                raise ValueError(f"Model not found: {model_name}")
            info.scope = scope
            self.capabilities.models = self.inference.loaded_models
            self.capabilities.role = "seeder" if self.inference.loaded_models else "consumer"
            await self.announce_capabilities()
            return {"status": "updated", "model": model_name, "scope": scope}

        raise ValueError(f"Unknown command: {command}")

    def _resolve_peer_trust(self, peer_id: str) -> str:
        """Determine the highest trust level for a peer based on shared networks.

        Checks which networks the peer belongs to (from their NodeHello),
        finds networks we share, and returns the highest trust level:
          "full"      — org network, IT-managed (skip all checks)
          "trusted"   — private network, vetted members
          "untrusted" — public network or unknown peer

        Returns:
            Trust level string.
        """
        if not self.federation:
            return "untrusted"

        # Get peer's network IDs from their authenticated connection
        conn = self._peer_connections.get(peer_id)
        if not conn or not conn.hello:
            # Also check the registry
            reg_entry = self.registry.get(peer_id)
            peer_networks = set(getattr(reg_entry, 'network_ids', []) if reg_entry else [])
        else:
            peer_networks = set(getattr(conn.hello, 'network_ids', []))

        if not peer_networks:
            return "untrusted"

        # Our networks and their trust levels
        trust_order = {"full": 3, "trusted": 2, "untrusted": 1}
        best_trust = "untrusted"

        # Check home network
        if self.federation.identity:
            if self.federation.identity.network_id in peer_networks:
                level = self.federation.identity.trust_level
                if trust_order.get(level, 0) > trust_order.get(best_trust, 0):
                    best_trust = level

        # Check joined networks
        for membership in self.federation.memberships:
            if membership.network_id in peer_networks:
                # Memberships don't have trust_level yet — infer from home identity
                # If we joined their network, treat as at least "trusted"
                if trust_order.get("trusted", 0) > trust_order.get(best_trust, 0):
                    best_trust = "trusted"

        return best_trust

    async def _handle_inference_request(self, protocol, msg: MessageEnvelope, stream_id: int) -> None:
        """Handle an incoming inference request from a peer."""
        from mycellm.inference.base import InferenceRequest

        payload = msg.payload
        model = payload.get("model", "")
        messages = payload.get("messages", [])
        stream = payload.get("stream", False)
        tools = payload.get("tools") or None
        tool_choice = payload.get("tool_choice")

        # Determine trust level based on shared network memberships
        peer_trust = self._resolve_peer_trust(msg.from_peer)

        # Admission control — policy depends on trust level
        if peer_trust == "full":
            # Org network — IT-managed trust, skip all checks
            admission = AdmissionResult(True, "org_trust", 1.0)
        elif peer_trust == "trusted":
            # Private network — check reputation only, no receipt requirement
            admission = self.reputation.check_admission(
                msg.from_peer,
                min_score=self._settings.admission_min_score,
                require_receipts=False,
                grace_requests=self._settings.admission_grace_requests,
            )
        else:
            # Public/untrusted — full admission check
            admission = self.reputation.check_admission(
                msg.from_peer,
                min_score=self._settings.admission_min_score,
                require_receipts=self._settings.admission_require_receipts,
                grace_requests=self._settings.admission_grace_requests,
            )
        try:
            from mycellm.metrics import admission_checks_total
            admission_checks_total.labels(result="allowed" if admission.allowed else "denied").inc()
        except ImportError:
            pass

        if not admission.allowed:
            logger.info(
                f"{styled_tag('SECURITY')} Refused inference to {msg.from_peer[:16]}... "
                f"({admission.reason})"
            )
            err = error_message(
                self.peer_id, msg.id, ErrorCode.INSUFFICIENT_CREDIT,
                f"Admission denied: {admission.reason}",
            )
            await protocol.reply_on_stream(stream_id, err)
            self.activity.record(
                EventType.INFERENCE_FAILED, model=model, source="peer",
                peer=msg.from_peer[:16], reason=f"admission:{admission.reason}",
            )
            return

        self.activity.record(EventType.INFERENCE_START, model=model, source="peer", peer=msg.from_peer[:16])
        _infer_start = time.time()

        model_name = self.inference.resolve_model_name(model)
        if not model_name:
            # Model not loaded locally — try relaying to a peer that has it
            if stream:
                try:
                    relayed = False
                    async for chunk in self.route_inference_stream(model, messages, **{
                        "temperature": payload.get("temperature", 0.7),
                        "max_tokens": payload.get("max_tokens", 2048),
                        "tools": tools,
                    }):
                        text = chunk.get("text", "")
                        tc = chunk.get("tool_calls")
                        if text or tc:
                            chunk_msg = inference_stream_chunk(self.peer_id, msg.id, text or "", chunk.get("finish_reason"), tool_calls=tc)
                            await protocol.send_message(chunk_msg)
                            relayed = True
                    if relayed:
                        done_msg = inference_done(self.peer_id, msg.id)
                        await protocol.send_message(done_msg)
                        return
                except Exception as e:
                    logger.debug(f"Relay stream failed: {e}")
            else:
                result = await self.route_inference(model, messages,
                    temperature=payload.get("temperature", 0.7),
                    max_tokens=payload.get("max_tokens", 2048),
                )
                if result:
                    text = result.get("text", "") if isinstance(result, dict) else ""
                    resp = inference_response(
                        self.peer_id, msg.id, text, model,
                        result.get("prompt_tokens", 0) if isinstance(result, dict) else 0,
                        result.get("completion_tokens", 0) if isinstance(result, dict) else 0,
                    )
                    await protocol.reply_on_stream(stream_id, resp)
                    return

            err = error_message(self.peer_id, msg.id, ErrorCode.MODEL_UNAVAILABLE)
            await protocol.reply_on_stream(stream_id, err)
            return

        if self.inference.is_overloaded:
            err = error_message(self.peer_id, msg.id, ErrorCode.OVERLOADED)
            await protocol.reply_on_stream(stream_id, err)
            return

        # Apply Qwen single-tool workaround: force tool_choice when exactly one
        # tool is defined and no choice is set, to reliably get JSON tool_calls.
        effective_tool_choice = tool_choice
        if tools and effective_tool_choice in (None, "auto", "none") and len(tools) == 1:
            try:
                fn_name = tools[0]["function"]["name"]
                effective_tool_choice = {"type": "function", "function": {"name": fn_name}}
            except (KeyError, TypeError, IndexError):
                pass

        # reasoning_exclude propagates from the originating client through
        # the gateway and across the QUIC relay. None = honor this peer's
        # local default (MYCELLM_HIDE_REASONING_BY_DEFAULT). When set, it
        # tells the chat-template layer to pass enable_thinking=False so
        # hybrid models like Qwen3 don't burn the token budget on thinking
        # that the upstream gateway is just going to strip.
        peer_reasoning_exclude = payload.get("reasoning_exclude")
        if peer_reasoning_exclude is None:
            peer_reasoning_exclude = self._settings.hide_reasoning_by_default

        req = InferenceRequest(
            messages=messages,
            model=model_name,
            temperature=payload.get("temperature", 0.7),
            max_tokens=payload.get("max_tokens", 2048),
            tools=tools,
            tool_choice=effective_tool_choice,
            reasoning_exclude=peer_reasoning_exclude,
        )

        try:
            completion_tokens = 0
            if stream and not tools:
                async for chunk in self.inference.generate_stream(req):
                    chunk_msg = inference_stream_chunk(
                        self.peer_id, msg.id, chunk.text, chunk.finish_reason,
                        tool_calls=chunk.tool_calls,
                    )
                    await protocol.send_message(chunk_msg)
                    if chunk.text:
                        completion_tokens += 1  # ~1 token per streamed chunk
                done_msg = inference_done(self.peer_id, msg.id)
                await protocol.send_message(done_msg)
            elif stream and tools:
                # Use non-streaming generate() for tool requests — streaming tool_call
                # deltas are unreliable; emit the full result as a single stream chunk.
                result = await self.inference.generate(req)
                if not result.tool_calls and result.text and "<tool_call>" in result.text:
                    from mycellm.api.openai import _parse_tool_call_xml
                    parsed = _parse_tool_call_xml(result.text)
                    if parsed:
                        result.tool_calls = parsed
                        result.text = ""
                        result.finish_reason = "tool_calls"
                chunk_msg = inference_stream_chunk(
                    self.peer_id, msg.id, result.text, result.finish_reason,
                    tool_calls=result.tool_calls,
                )
                await protocol.send_message(chunk_msg)
                done_msg = inference_done(self.peer_id, msg.id)
                await protocol.send_message(done_msg)
            else:
                result = await self.inference.generate(req)
                resp = inference_response(
                    self.peer_id, msg.id, result.text, model_name,
                    result.prompt_tokens, result.completion_tokens, result.finish_reason,
                )
                await protocol.reply_on_stream(stream_id, resp)

            # Non-streamed (incl. tool) paths report the token count on the
            # result; the streamed path counted chunks above.
            if not (stream and not tools):
                completion_tokens = result.completion_tokens

            # Credit the seeder + emit a co-signable receipt for BOTH streamed
            # and non-streamed completions. Streaming is the common chat case,
            # so it must earn too — previously only non-streamed served earned.
            if self.ledger:
                from mycellm.accounting.pricing import compute_reward
                reward = compute_reward(max(completion_tokens, 1))

                # network_id settles the receipt into the right per-net tracker
                # ledger; v1 stamps the public net for the public fleet (older
                # peers omit it -> the consumer reads it back from the message,
                # so cross-version verification still matches).
                receipt_network_id = "public"
                ts = time.time()
                receipt_data = build_receipt_data(
                    consumer_id=msg.from_peer,
                    seeder_id=self.peer_id,
                    model=model_name,
                    tokens=completion_tokens,
                    cost=reward,
                    request_id=msg.id,
                    timestamp=ts,
                    network_id=receipt_network_id,
                )
                sig = sign_receipt(self.device_key, receipt_data)

                if self.receipt_validator.check_credit_rate(self.peer_id):
                    await self.ledger.credit(self.peer_id, reward, "inference_served",
                                             counterparty_id=msg.from_peer,
                                             receipt_signature=sig)
                else:
                    logger.warning("Credit rate limit reached, skipping self-credit")

                # Send signed receipt to the consumer (who co-signs + settles).
                from mycellm.transport.messages import signed_credit_receipt
                receipt_msg = signed_credit_receipt(
                    self.peer_id, msg.from_peer, self.peer_id,
                    model_name, completion_tokens, reward, ts, sig,
                )
                receipt_msg.payload["request_id"] = msg.id
                receipt_msg.payload["network_id"] = receipt_network_id
                await protocol.send_message(receipt_msg)

                self.reputation.record_success(msg.from_peer, result.completion_tokens if not stream else 0, 0.0)

            _infer_tokens = result.completion_tokens + result.prompt_tokens if not stream else 0
            self.activity.record(
                EventType.INFERENCE_COMPLETE,
                model=model_name,
                source="peer",
                tokens=_infer_tokens,
                latency_ms=round((time.time() - _infer_start) * 1000),
            )

        except Exception as e:
            logger.error(f"{styled_tag('INFER')} Inference failed: {e}")
            self.activity.record(EventType.INFERENCE_FAILED, model=model, error=str(e), source="peer")
            err = error_message(self.peer_id, msg.id, ErrorCode.BACKEND_ERROR, str(e))
            await protocol.reply_on_stream(stream_id, err)

    async def _handle_relay(self, protocol, msg: MessageEnvelope, stream_id: int) -> None:
        """Handle a relay request -- forward to target peer."""
        payload = msg.payload
        target_peer = payload.get("target_peer", "")
        via = payload.get("via", [])

        # Prevent loops
        if self.peer_id in via:
            err = error_message(self.peer_id, msg.id, ErrorCode.PEER_UNREACHABLE, "Relay loop detected")
            await protocol.reply_on_stream(stream_id, err)
            return

        # Find target connection
        target_conn = self._peer_connections.get(target_peer)
        if not target_conn:
            # Try routing through chain builder
            targets = self.chain_builder.route(payload.get("model", ""))
            if targets:
                target_conn = targets[0].entry.connection
                target_peer = targets[0].peer_id

        if not target_conn:
            err = error_message(self.peer_id, msg.id, ErrorCode.PEER_UNREACHABLE)
            await protocol.reply_on_stream(stream_id, err)
            return

        # Forward the request
        from mycellm.transport.messages import inference_request
        fwd = inference_request(
            self.peer_id,
            payload.get("model", ""),
            payload.get("messages", []),
            temperature=payload.get("temperature", 0.7),
            max_tokens=payload.get("max_tokens", 2048),
            stream=payload.get("stream", False),
        )

        try:
            resp = await target_conn.request(fwd, timeout=60.0)
            # Forward response back to originator
            resp.id = msg.id  # preserve original request ID
            await protocol.reply_on_stream(stream_id, resp)

            # Earn relay fee (10%)
            if self.ledger and resp.type == MessageType.INFERENCE_RESP:
                tokens = resp.payload.get("completion_tokens", 0)
                from mycellm.accounting.pricing import compute_cost
                relay_fee = compute_cost(max(tokens, 1)) * 0.1
                await self.ledger.credit(self.peer_id, relay_fee, "relay_fee",
                                         counterparty_id=msg.from_peer)
        except Exception as e:
            logger.error(f"Relay to {target_peer[:8]} failed: {e}")
            err = error_message(self.peer_id, msg.id, ErrorCode.BACKEND_ERROR, str(e))
            await protocol.reply_on_stream(stream_id, err)

    async def _handle_credit_receipt(self, msg: MessageEnvelope) -> None:
        """Handle a signed credit receipt from a peer.

        Verifies:
          1. Sender is an authenticated peer
          2. Ed25519 signature is valid against seeder's public key
          3. Request ID hasn't been seen before (replay protection)
        """
        payload = msg.payload
        signature = payload.get("signature", "")
        request_id = payload.get("request_id", "")

        # Must be from authenticated peer
        conn = self._peer_connections.get(msg.from_peer)
        if not conn:
            logger.warning(f"Receipt from unauthenticated peer {msg.from_peer[:16]}")
            try:
                from mycellm.metrics import receipts_received_total
                receipts_received_total.labels(status="rejected").inc()
            except ImportError:
                pass
            return

        # Replay protection
        if not self.receipt_validator.check_replay(request_id):
            logger.warning(f"Replay receipt rejected from {msg.from_peer[:16]}")
            try:
                from mycellm.metrics import receipts_received_total
                receipts_received_total.labels(status="rejected").inc()
            except ImportError:
                pass
            return

        # Verify Ed25519 signature. network_id is read back from the message
        # (not assumed) so receipts from older peers that omit it still verify.
        receipt_network_id = payload.get("network_id", "")
        if signature and conn.hello and conn.hello.cert:
            receipt_data = build_receipt_data(
                consumer_id=payload.get("consumer_id", ""),
                seeder_id=payload.get("seeder_id", ""),
                model=payload.get("model", ""),
                tokens=payload.get("tokens", 0),
                cost=payload.get("cost", 0.0),
                request_id=request_id,
                timestamp=payload.get("timestamp", 0.0),
                network_id=receipt_network_id,
            )
            seeder_pubkey = conn.hello.cert.device_pubkey
            if not verify_receipt_signature(receipt_data, signature, seeder_pubkey):
                logger.warning(f"Invalid receipt signature from {msg.from_peer[:16]}")
                try:
                    from mycellm.metrics import receipts_received_total
                    receipts_received_total.labels(status="rejected").inc()
                except ImportError:
                    pass
                return

            # Stage 3: co-sign the verified receipt and settle it into the
            # network tracker (the source of truth). Best-effort — never let
            # accounting affect the receipt-handling path.
            try:
                await self._cosign_and_settle_receipt(
                    payload, receipt_data, signature, seeder_pubkey, receipt_network_id
                )
            except Exception as e:
                logger.debug(f"Co-sign/settle failed for {request_id}: {e}")

        # Store verified receipt
        if self.ledger:
            await self.ledger.store_receipt(
                tx_id=msg.id,
                consumer_id=payload.get("consumer_id", ""),
                seeder_id=payload.get("seeder_id", ""),
                model=payload.get("model", ""),
                tokens=payload.get("tokens", 0),
                cost=payload.get("cost", 0.0),
                signature=signature,
            )
        self.reputation.record_receipt(msg.from_peer)
        logger.debug(f"Verified receipt from {msg.from_peer[:8]}: {payload.get('tokens', 0)} tokens")
        try:
            from mycellm.metrics import receipts_received_total
            receipts_received_total.labels(status="verified").inc()
        except ImportError:
            pass

    async def _cosign_and_settle_receipt(
        self, payload: dict, receipt_data: bytes, seeder_sig: str,
        seeder_pubkey: bytes, network_id: str,
    ) -> None:
        """Co-sign a verified seeder receipt and settle it into the network
        tracker (the source of truth for balances).

        If this node IS the tracker for the net (a public bootstrap), settle
        in-process against our own ledger — no HTTP round-trip to ourselves.
        Otherwise submit the co-signed receipt to the public tracker over HTTP.
        """
        if not (self.device_key and self.device_cert):
            return
        consumer_sig = sign_receipt(self.device_key, receipt_data)
        cosigned = {
            "consumer": payload.get("consumer_id", ""),
            "seeder": payload.get("seeder_id", ""),
            "model": payload.get("model", ""),
            "tokens": payload.get("tokens", 0),
            "cost": payload.get("cost", 0.0),
            "request_id": payload.get("request_id", ""),
            "ts": payload.get("timestamp", 0.0),
            "network_id": network_id,
            "seeder_signature": seeder_sig,
            "consumer_signature": consumer_sig,
            "seeder_pubkey": seeder_pubkey.hex(),
            "consumer_pubkey": self.device_cert.device_pubkey.hex(),
        }

        # We are the tracker for this net (a public bootstrap): settle directly.
        if getattr(self._settings, "public", False) and self.ledger:
            from mycellm.accounting.tracker import settle_cosigned_receipt
            from mycellm.storage.repositories import NetworkLedgerRepository
            res = await settle_cosigned_receipt(
                NetworkLedgerRepository(), self.tracker_validator, cosigned
            )
            if not res.get("ok"):
                logger.debug(f"Tracker settle skipped: {res.get('reason')} ({cosigned['request_id']})")
            return

        # Remote consumer: submit to the public tracker over HTTP (best-effort).
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.mycellm.dev/v1/public/receipts",
                json={"receipts": [cosigned]},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"Receipt submit returned HTTP {resp.status}")

    def _handle_peer_exchange(self, msg: MessageEnvelope) -> None:
        """Handle peer exchange -- learn about peers from connected peer."""
        peers = msg.payload.get("peers", [])
        new_peers = 0
        for p in peers:
            peer_id = p.get("peer_id", "")
            if peer_id and peer_id != self.peer_id and peer_id not in self._peer_connections:
                addrs = p.get("addresses", [])
                if addrs:
                    caps = Capabilities.from_dict(p.get("capabilities", {}))
                    self.registry.register(peer_id, capabilities=caps, addresses=addrs)
                    new_peers += 1
                    # Try to connect — prefer previously successful addresses
                    reg_entry = self.registry.get(peer_id)
                    sorted_addrs = reg_entry.sorted_addresses() if reg_entry else addrs
                    for addr in sorted_addrs:
                        if ":" in addr:
                            host, port_str = addr.rsplit(":", 1)
                            try:
                                self.peer_manager.add_peer(host, int(port_str), peer_id=peer_id)
                            except (ValueError, AttributeError):
                                pass
        if new_peers:
            self.activity.record(
                EventType.PEER_EXCHANGE_RECEIVED,
                peers_discovered=new_peers,
                from_peer=msg.from_peer[:16],
            )

    @staticmethod
    def _is_private_addr(addr: str) -> bool:
        """Check if an address uses a private/link-local IP."""
        host = addr.split(":")[0]
        return (host.startswith("10.") or host.startswith("192.168.") or
                host.startswith("172.16.") or host.startswith("172.17.") or
                host.startswith("172.18.") or host.startswith("172.19.") or
                host.startswith("172.2") or host.startswith("172.3") or
                host.startswith("100.") or host.startswith("169.254.") or
                host.startswith("127.") or host.startswith("fd") or host.startswith("fe80"))

    def _build_peer_exchange_list(self, exclude_peer_id: str = "", recipient_addr: str = "") -> list[dict]:
        """Build a list of known peers for peer exchange, excluding a specific peer.

        If the recipient is on a different public IP (not same NAT), private
        addresses are stripped since they'd be unreachable anyway.
        """
        seen: set[str] = {self.peer_id}
        if exclude_peer_id:
            seen.add(exclude_peer_id)

        # Determine if recipient shares our NAT (same public IP = likely same LAN)
        # If so, include private addresses; otherwise filter them out
        recipient_is_local = MycellmNode._is_private_addr(recipient_addr) if recipient_addr else False

        peers: list[dict] = []

        # Source 1: Registry entries (QUIC-connected peers with addresses)
        for entry in self.registry.connected_peers():
            if entry.peer_id in seen or not entry.addresses:
                continue
            addrs = entry.addresses
            if not recipient_is_local:
                addrs = [a for a in addrs if not MycellmNode._is_private_addr(a)]
            if not addrs:
                continue
            seen.add(entry.peer_id)
            peers.append({
                "peer_id": entry.peer_id,
                "addresses": addrs,
                "capabilities": entry.capabilities.to_dict(),
            })

        # Source 2: node_registry (HTTP-announced peers — may have api_addr)
        for peer_id, info in self.node_registry.items():
            if peer_id in seen or info.get("status") != "approved":
                continue
            api_addr = info.get("api_addr", "")
            if not api_addr:
                continue
            host = api_addr.split(":")[0]
            addr = f"{host}:8421"
            if not recipient_is_local and MycellmNode._is_private_addr(addr):
                continue
            seen.add(peer_id)
            caps = info.get("capabilities", {})
            peers.append({
                "peer_id": peer_id,
                "addresses": [addr],
                "capabilities": caps if isinstance(caps, dict) else {},
            })

        return peers

    async def _peer_exchange_broadcast_loop(self) -> None:
        """Broadcast known peer list when the connected peer set changes.

        Checks periodically; only sends if the peer set differs from last broadcast.
        Uses jittered fast start for responsiveness, then settles to configured interval.
        """
        import random
        from mycellm.transport.messages import peer_exchange

        max_interval = self._settings.peer_exchange_interval
        interval = 5 + random.random() * 5  # first check: 5-10s
        last_peer_set: set[str] = set()

        while self._running:
            try:
                await asyncio.sleep(interval)

                # Check if peer set changed
                current_peers = set(self._peer_connections.keys())
                for p in self.peer_manager.managed_peers.values():
                    if p.peer_id and p.connection:
                        current_peers.add(p.peer_id)

                if current_peers == last_peer_set and len(current_peers) > 0:
                    # No change — extend interval, skip broadcast
                    interval = min(interval * 1.5, max_interval)
                    continue

                last_peer_set = current_peers.copy()

                if len(current_peers) < 2:
                    # Need at least 2 peers to exchange
                    interval = min(interval * 1.5 + random.random() * 5, max_interval)
                    continue

                sent_to: set[str] = set()

                for peer_id, conn in list(self._peer_connections.items()):
                    if peer_id in sent_to:
                        continue
                    recip_addr = ""
                    if conn.protocol and conn.protocol._peer_addr:
                        recip_addr = f"{conn.protocol._peer_addr[0]}:8421"
                    peer_list = self._build_peer_exchange_list(exclude_peer_id=peer_id, recipient_addr=recip_addr)
                    if not peer_list:
                        continue
                    try:
                        msg = peer_exchange(self.peer_id, peer_list)
                        await conn.send(msg)
                        sent_to.add(peer_id)
                    except Exception as e:
                        logger.debug(f"Peer exchange to {peer_id[:16]} failed: {e}")

                for peer in self.peer_manager.managed_peers.values():
                    if peer.connection and peer.peer_id and peer.peer_id not in sent_to:
                        peer_list = self._build_peer_exchange_list(exclude_peer_id=peer.peer_id)
                        if not peer_list:
                            continue
                        try:
                            msg = peer_exchange(self.peer_id, peer_list)
                            await peer.connection.send(msg)
                            sent_to.add(peer.peer_id)
                        except Exception as e:
                            logger.debug(f"Peer exchange to managed {peer.addr} failed: {e}")

                if sent_to:
                    logger.info(
                        f"{styled_tag('P2P')} Peer exchange broadcast to {len(sent_to)} peers"
                    )

                # After a change, reset to fast interval for responsive follow-up
                interval = 10 + random.random() * 5

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug(f"Peer exchange broadcast error: {e}")
                interval = min(interval * 1.5, max_interval)

    async def _model_prewarm_loop(self) -> None:
        """Periodically ping loaded models to keep them paged into memory.

        On machines with limited RAM, the OS pages out idle model weights.
        A lightweight warmup every 10 minutes prevents cold-start latency.
        Skips if model is already busy (active inference) to avoid contention.
        """
        from mycellm.inference.base import InferenceRequest

        await asyncio.sleep(120)  # let models finish loading first

        while self._running:
            try:
                if self.inference.is_overloaded:
                    await asyncio.sleep(600)
                    continue
                for model in self.inference.loaded_models:
                    if not self._running:
                        return
                    try:
                        # Minimal inference — 1 token, no queue contention check
                        req = InferenceRequest(
                            messages=[{"role": "user", "content": "hi"}],
                            model=model.name,
                            temperature=0.0,
                            max_tokens=1,
                        )
                        await self.inference.generate(req)
                        logger.debug(f"Prewarm: {model.name} OK")
                    except Exception:
                        pass  # model busy or errored — skip
                await asyncio.sleep(600)  # every 10 minutes
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(600)

    async def _start_dht(self) -> None:
        """Start the DHT discovery node."""
        if not self.enable_dht:
            return
        from mycellm.dht.node import DHTNode
        from mycellm.dht.bootstrap import load_bootstrap_peers

        bootstrap = self._settings.get_bootstrap_list()
        file_bootstrap = load_bootstrap_peers(self._settings.config_dir)
        all_bootstrap = list(set(bootstrap + file_bootstrap))

        self._dht_node = DHTNode(port=self.dht_port)
        try:
            await self._dht_node.start(all_bootstrap or None)
            logger.info(f"{styled_tag('DHT')} Discovery on :{self.dht_port}")

            # Announce ourselves
            await self._dht_node.announce(
                self.peer_id,
                [f"{self._quic_host}:{self.quic_port}"],
                self.capabilities.to_dict(),
            )
        except Exception as e:
            logger.warning(f"{styled_tag('DHT')} Failed to start: {e}")
            self._dht_node = None

    async def _start_api(self) -> None:
        """Start the FastAPI server, then restore models in background."""
        import uvicorn
        from mycellm.api.app import create_app

        app = create_app(self)

        # Schedule model restore + relay init AFTER API is accepting connections
        @app.on_event("startup")
        async def _on_api_ready():
            logger.info(f"{styled_tag('BOOT')} API ready — loading models in background")
            asyncio.ensure_future(self._restore_models_bg())

        config = uvicorn.Config(
            app, host=self.api_host, port=self.api_port, log_level="warning",
        )
        self._api_server = uvicorn.Server(config)
        logger.info(f"{styled_tag('API')} http://{self.api_host}:{self.api_port}")
        await self._api_server.serve()

    async def _restore_models_bg(self) -> None:
        """Restore saved models + connect relays in background. Never blocks API/transport."""
        # 1. Restore persisted models. Loads now run off the event loop (weights
        # via to_thread, preflight via to_thread) so the loop stays responsive —
        # measured p95 ~4ms with only a brief GIL blip during native weight
        # construction. Keep a modest defer to cover a large model's GIL stretch
        # without masking a genuine restore-time wedge for too long.
        if self._watchdog is not None:
            self._watchdog.defer(180.0)
        try:
            restored = await self.inference.restore_models(self._settings.data_dir)
            if restored:
                self.capabilities.models = self.inference.loaded_models
                self.capabilities.role = "seeder" if self.inference.loaded_models else "consumer"
                logger.info(f"{styled_tag('BOOT')} Restored {restored} model(s)")
                await self.announce_capabilities()
        except Exception as e:
            logger.warning(f"{styled_tag('BOOT')} Model restore failed: {e} — load models via dashboard")

        # 2. Connect configured relay backends
        try:
            from mycellm.inference.relay import parse_relay_backends
            relay_urls = parse_relay_backends(self._settings.relay_backends)
            for url in relay_urls:
                try:
                    relay = await self.relay_manager.add(url)
                    if relay.online:
                        logger.info(f"{styled_tag('RELAY')} Connected: {relay.name} ({len(relay.models)} models)")
                    else:
                        logger.warning(f"{styled_tag('RELAY')} Offline: {url} — {relay.error}")
                except Exception as e:
                    logger.warning(f"{styled_tag('RELAY')} Failed to add {url}: {e}")
            if relay_urls and self.relay_manager.relays:
                self.capabilities.models = self.inference.loaded_models
                self.capabilities.role = "seeder" if self.inference.loaded_models else "consumer"
                await self.announce_capabilities()
                self.relay_manager.start_polling(interval=60)
        except Exception as e:
            logger.warning(f"{styled_tag('RELAY')} Relay init failed: {e}")

    def _save_peer_cache(self) -> None:
        """Persist known peers to disk."""
        import json
        cache = {}
        for entry in self.registry.all_peers():
            cache[entry.peer_id] = {
                "peer_id": entry.peer_id,
                "addresses": entry.addresses,
                "capabilities": entry.capabilities.to_dict(),
                "last_seen": entry.last_seen,
            }
        cache_path = self._settings.data_dir / "node_state.json"
        try:
            tmp = cache_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(cache, indent=2))
            tmp.rename(cache_path)  # atomic on POSIX
        except Exception as e:
            logger.debug(f"Failed to save peer cache: {e}")

    def _load_peer_cache(self) -> None:
        """Load cached peers and pre-populate registry."""
        import json
        cache_path = self._settings.data_dir / "node_state.json"
        if not cache_path.exists():
            return
        try:
            cache = json.loads(cache_path.read_text())
            for peer_id, info in cache.items():
                caps = Capabilities.from_dict(info.get("capabilities", {}))
                self.registry.register(
                    peer_id,
                    capabilities=caps,
                    addresses=info.get("addresses", []),
                )
            if cache:
                logger.info(f"{styled_tag('BOOT')} Loaded {len(cache)} cached peers")
        except Exception as e:
            logger.debug(f"Failed to load peer cache: {e}")

    async def run(self) -> None:
        """Start the node and all subsystems."""
        self._setup_logging()
        self._load_identity()

        # Initialize federation
        self.federation = FederationManager(self._settings.data_dir)
        self.federation.init_network(
            self.account_key.public_bytes,
            bootstrap_addrs=[f"{self._quic_host}:{self.quic_port}"],
        )

        # Load cached peers
        self._load_peer_cache()

        # Restore persisted models — truly non-blocking.
        # Model loading (especially llama.cpp) can take minutes and hold the GIL.
        # We schedule it as a task that runs AFTER the API server starts accepting.
        self._model_restore_task = None  # set below after API is ready

        hw = self._detect_hardware()
        self.capabilities = Capabilities(
            models=self.inference.loaded_models,
            hardware=hw,
            role="seeder" if self.inference.loaded_models else "consumer",
            version="0.1.0",
            network_ids=self.federation.network_ids if self.federation else [],
        )

        self._running = True
        self._start_time = time.time()
        self._force_announce = asyncio.Event()

        # Handle signals
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(self.shutdown()))

        # Arm the heartbeat watchdog (out-of-loop OS thread that os._exit()s on a
        # wedged loop or fd exhaustion, so launchd/systemd/docker restart us) and
        # start the loop-side heartbeat that proves the loop is alive.
        try:
            from mycellm.resilience import HeartbeatWatchdog

            log_path = str(Path(self._settings.data_dir) / "watchdog.log")
            self._watchdog = HeartbeatWatchdog(
                enabled=self._settings.watchdog_enabled,
                stall_seconds=self._settings.watchdog_stall_seconds,
                fd_pct=self._settings.watchdog_fd_pct,
                check_interval=self._settings.watchdog_check_interval,
                log_path=log_path,
            )
            self._watchdog.start()
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        except Exception as e:
            logger.debug(f"Watchdog not started: {e}")
            self._watchdog = None

        # Init subsystems (DB engine, ledger, repositories)
        await self._init_accounting()

        # Load persisted node registry from DB
        self.node_registry = await self.node_registry_repo.load_as_dict()
        await self._start_transport()

        # Start NAT discovery (non-blocking background)
        try:
            from mycellm.nat.discovery import NATDiscovery
            self.nat_discovery = NATDiscovery()
            await self.nat_discovery.start(local_port=self.quic_port)
            nat_info = self.nat_discovery.info
            logger.info(f"{styled_tag('NAT')} Discovery started ({nat_info.nat_type.value})")
            self.activity.record(
                EventType.NAT_DISCOVERED,
                nat_type=nat_info.nat_type.value,
                public_ip=nat_info.external_ip,
                hole_punch="yes" if nat_info.nat_type.can_hole_punch else "no",
            )
        except Exception as e:
            logger.debug(f"NAT discovery failed to start: {e}")
            self.nat_discovery = None

        if self.enable_dht:
            await self._start_dht()

        # Connect to bootstrap peers via PeerManager (QUIC on port 8421)
        raw_peers = self._settings.get_bootstrap_list()
        quic_peers = [(h, 8421 if p == 8420 else p) for h, p in raw_peers]
        await self.peer_manager.start(quic_peers)

        # Announce to bootstrap nodes via HTTP
        self._announce_task = asyncio.create_task(self._announce_to_bootstrap())

        # Start health checker
        await self.health_checker.start()

        # Start growth snapshot task (hourly)
        self._growth_task = asyncio.create_task(self._growth_snapshot_loop())
        self._growth_snapshots: dict = {}

        # Start registry TTL sweep (every 5 min) — evicts dead seeders
        # and warns loudly when a public bootstrap has no one online.
        self._registry_sweep_task = asyncio.create_task(self._registry_ttl_sweep_loop())

        # Start peer exchange broadcast (shares connected peer list for P2P discovery)
        self._peer_exchange_task = asyncio.create_task(self._peer_exchange_broadcast_loop())

        # Start model prewarm (keeps weights in RAM on memory-constrained devices)
        self._prewarm_task = asyncio.create_task(self._model_prewarm_loop())

        # Start network self-heal (re-announce/reconnect on address change or fall-off)
        if self._settings.selfheal_enabled:
            self._selfheal_task = asyncio.create_task(self._network_selfheal_loop())

        # Initialize Prometheus metrics
        try:
            from mycellm.metrics import set_node_info
            set_node_info(self.peer_id, self._settings.node_name, "0.1.0")
        except ImportError:
            pass

        # Initialize relay manager (relay connections happen in _restore_models_bg after API ready)
        from mycellm.inference.relay import RelayManager
        self.relay_manager = RelayManager(self.inference)

        logger.info(f"{styled_tag('NODE')} Swarm connected. Awaiting inference tasks.")
        self.activity.record(EventType.NODE_STARTED, node_name=self._settings.node_name, peer_id=self.peer_id[:16])

        # Start API server (blocks)
        await self._start_api()

    async def _announce_to_bootstrap(self) -> None:
        """Announce this node to bootstrap peers via HTTP API.

        Runs as a long-lived background task. Must never crash.
        """
        import httpx

        peers = self._settings.get_bootstrap_list()
        if not peers:
            return

        headers = {}
        if self._settings.api_key:
            headers["Authorization"] = f"Bearer {self._settings.api_key}"

        sys_info = self.get_system_info()

        base_payload = {
            "peer_id": self.peer_id,
            "api_addr": f"{self.api_host}:{self.api_port}",
            "role": self.capabilities.role,
            "system": sys_info,
        }
        if self._settings.external_host:
            base_payload["external_host"] = self._settings.external_host

        async def _do_announce():
            base_payload["capabilities"] = self.capabilities.to_dict()
            # Include telemetry if opted in
            if self._settings.telemetry:
                stats = self.activity.stats() if hasattr(self, "activity") else {}
                base_payload["telemetry"] = {
                    "requests_total": stats.get("total_requests", 0),
                    "tokens_total": stats.get("total_tokens", 0),
                    "tps": self.activity.tps if hasattr(self, "activity") else 0,
                    "models_loaded": [m.name for m in self.inference.loaded_models],
                    "uptime_seconds": round(self.uptime),
                    "credits_earned": stats.get("credits_earned", 0),
                }
            any_ok = False
            for host, port in peers:
                # Announce to every bootstrap — LAN and public.
                #
                # Previously this was gated on `is_lan` with the assumption
                # that public bootstraps would discover peers exclusively via
                # QUIC NodeHello. In practice home seeders behind symmetric
                # NAT cannot always rely on QUIC session establishment, and
                # even when they can, the HTTP announce is still the canonical
                # way the bootstrap learns which *models* a seeder is serving
                # (QUIC capabilities exchange is best-effort). Let the
                # bootstrap decide what to do with the announcement — public
                # bootstraps auto-approve (see api/admin.py::_is_public_network).
                payload = {**base_payload}
                # Port 443 → announce via HTTPS through the public reverse proxy
                # (e.g. Caddy fronting api.mycellm.dev → the prime's localhost-only
                # API on 8420). Home seeders reach the public bootstrap this way
                # without the prime having to expose 8420 publicly. Otherwise plain
                # HTTP to the API port (a QUIC :8421 entry maps to the :8420 API).
                if port == 443:
                    url = f"https://{host}/v1/admin/nodes/announce"
                else:
                    api_port = port if port != 8421 else 8420
                    url = f"http://{host}:{api_port}/v1/admin/nodes/announce"
                payload["node_name"] = self._settings.node_name
                payload["system"] = sys_info
                try:
                    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
                    async with httpx.AsyncClient(timeout=10, transport=transport) as client:
                        resp = await client.post(url, json=payload, headers=headers)
                        if resp.status_code == 200:
                            logger.info(f"{styled_tag('NODE')} Announced to bootstrap {url}")
                            self.activity.record(EventType.ANNOUNCE_OK, bootstrap=url)
                            any_ok = True
                        elif resp.status_code == 401:
                            logger.warning(f"{styled_tag('SECURITY')} Bootstrap {url} rejected announce (bad API key)")
                        else:
                            logger.warning(f"{styled_tag('NODE')} Announce to {url}: HTTP {resp.status_code}")
                except Exception as e:
                    logger.warning(f"{styled_tag('NODE')} Announce to {url} failed: {e}")
                    self.activity.record(EventType.ANNOUNCE_FAILED, bootstrap=url, reason=str(e))
            # Also send capabilities via QUIC to any live managed bootstrap connections.
            # This ensures the bootstrap's PeerRegistry stays current even when the
            # HTTP endpoint is unreachable (e.g. port bound to 127.0.0.1 inside Docker).
            try:
                await self.announce_capabilities()
            except Exception as e:
                logger.debug(f"{styled_tag('NODE')} QUIC capability announce failed: {e}")
            return any_ok

        # Initial announce
        await _do_announce()

        # Re-announce loop — never exits, never crashes. Wakes early when the
        # self-heal loop fires _force_announce (e.g. our address just changed).
        interval = 15
        while self._running:
            try:
                await self._wait_or_forced(interval)
                ok = await _do_announce()
                interval = 60 if ok else min(interval + 10, 60)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"{styled_tag('NODE')} Announce loop error: {e}")
                interval = 30

    async def _wait_or_forced(self, timeout: float) -> None:
        """Sleep up to ``timeout`` seconds, or wake immediately if a forced
        re-announce was requested (and consume the request)."""
        ev = self._force_announce
        if ev is None:
            await asyncio.sleep(timeout)
            return
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return
        finally:
            ev.clear()

    async def _heartbeat_loop(self) -> None:
        """Prove the event loop is alive to the out-of-loop watchdog thread.

        If the loop wedges, this stops running, the watchdog's heartbeat goes
        stale, and it restarts the process. Runs ~3x more often than the
        watchdog's check interval so a single missed tick is not a false alarm.
        """
        wd = self._watchdog
        if wd is None:
            return
        period = max(1.0, float(self._settings.watchdog_check_interval) / 3.0)
        while self._running:
            try:
                wd.beat()
                await asyncio.sleep(period)
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(period)

    def _network_signature(self) -> tuple:
        """A cheap fingerprint of where this node sits on the network. Changes
        when the machine moves networks / gets a new DHCP lease / NAT rebinds."""
        local_ip = ""
        try:
            from mycellm.nat.discovery import _get_local_ip

            local_ip = _get_local_ip()
        except Exception:
            pass
        ext_ip = ext_port = ""
        nat = getattr(self, "nat_discovery", None)
        if nat is not None and getattr(nat, "info", None) is not None:
            ext_ip = getattr(nat.info, "public_ip", "") or getattr(nat.info, "external_ip", "") or ""
            ext_port = getattr(nat.info, "public_port", "") or ""
        return (local_ip, ext_ip, ext_port)

    def _ensure_membership_bootstraps(self) -> None:
        """Make sure every configured network's bootstrap(s) are managed peers.

        Joined-network bootstraps (federation memberships) are otherwise never
        dialed — only the settings bootstrap list is. PeerManager.add_peer is
        idempotent and its reconnect loop handles the actual (re)connection."""
        fed = self.federation
        if fed is None:
            return
        addrs: list[str] = []
        try:
            if getattr(fed, "identity", None) and fed.identity:
                addrs.extend(fed.identity.bootstrap_addrs or [])
            for m in fed.memberships:
                addrs.extend(getattr(m, "bootstrap_addrs", []) or [])
        except Exception:
            return
        managed = self.peer_manager.managed_peers
        for addr in addrs:
            host, _, port_str = addr.rpartition(":")
            if not host or not port_str.isdigit():
                continue
            port = 8421 if int(port_str) == 8420 else int(port_str)
            # Skip our own advertised bootstrap address.
            if host in ("127.0.0.1", self._quic_host) and port == self.quic_port:
                continue
            if f"{host}:{port}" in managed:
                continue
            try:
                self.peer_manager.add_peer(host, port)
                logger.info(f"{styled_tag('NET')} Self-heal: managing network bootstrap {host}:{port}")
            except Exception:
                pass

    async def _network_selfheal_loop(self) -> None:
        """Keep the node attached to its configured networks.

        Detects a local/public address change and immediately re-announces
        (re-probe NAT, force the HTTP announce, refresh addresses) instead of
        waiting out the normal 15-60s announce cycle, and ensures every network's
        bootstrap is being dialed. Idempotent and best-effort; never crashes.
        """
        interval = max(10.0, float(self._settings.selfheal_interval))
        # Settle before first check so NAT discovery has a chance to populate.
        await asyncio.sleep(min(interval, 15.0))
        self._addr_sig = self._network_signature()
        while self._running:
            try:
                await asyncio.sleep(interval)
                sig = self._network_signature()
                if self._addr_sig is not None and sig != self._addr_sig:
                    logger.warning(
                        f"{styled_tag('NET')} Address change {self._addr_sig} → {sig}; "
                        "re-announcing and reconnecting"
                    )
                    self.activity.record(
                        EventType.ADDRESS_CHANGED, old=str(self._addr_sig), new=str(sig)
                    )
                    # Invalidate the 5-min local-address cache so re-announce uses
                    # the new address.
                    self._cached_addresses_at = 0.0
                    # Re-probe NAT to refresh the public mapping.
                    nat = getattr(self, "nat_discovery", None)
                    if nat is not None:
                        asyncio.ensure_future(self._safe_nat_reprobe(nat))
                    # Force an immediate re-announce.
                    if self._force_announce is not None:
                        self._force_announce.set()
                self._addr_sig = sig
                # Keep all configured networks' bootstraps connected.
                self._ensure_membership_bootstraps()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug(f"{styled_tag('NET')} Self-heal loop error: {e}")

    async def _safe_nat_reprobe(self, nat) -> None:
        try:
            await nat.probe_once()
            self.activity.record(EventType.NETWORK_SELFHEAL, action="nat_reprobe")
        except Exception as e:
            logger.debug(f"{styled_tag('NET')} NAT re-probe failed: {e}")

    async def _registry_ttl_sweep_loop(self) -> None:
        """Evict node_registry entries that haven't been seen in a long time.

        Public bootstraps accumulate seeders as they come and go. Anything
        not heard from in over an hour is considered gone — drop it from
        memory and the database to keep the registry honest. Routing already
        ignores entries with last_seen > 120s; this just keeps the index
        from growing forever and surfaces a clean "who's currently online"
        view in /v1/admin/nodes.
        """
        sweep_interval = 300  # every 5 min
        eviction_age = 3600   # 1 hour
        while self._running:
            try:
                await asyncio.sleep(sweep_interval)
                now = time.time()
                stale = [
                    pid for pid, e in self.node_registry.items()
                    if now - e.get("last_seen", 0) > eviction_age
                ]
                for pid in stale:
                    self.node_registry.pop(pid, None)
                    if hasattr(self, "node_registry_repo") and self.node_registry_repo:
                        try:
                            await self.node_registry_repo.remove(pid)
                        except Exception:
                            pass
                if stale:
                    logger.info(
                        f"{styled_tag('NODE')} Evicted {len(stale)} stale registry entries "
                        f"(last seen >{eviction_age}s ago)"
                    )

                # Loud warning if a public bootstrap has zero seeders.
                # "Seeders" here means: HTTP-announced fleet entries that
                # are recently alive PLUS live QUIC peer connections (the
                # latter is the dominant path now that home seeders rely
                # on outbound QUIC).
                if self._settings.public:
                    online = sum(
                        1 for e in self.node_registry.values()
                        if now - e.get("last_seen", 0) < 120
                        and e.get("status") == "approved"
                    )
                    quic_seeders = sum(
                        1 for p in self.registry.connected_peers()
                        if p.capabilities.role == "seeder"
                    )
                    if (online + quic_seeders) == 0 and len(self.inference.loaded_models) == 0:
                        logger.warning(
                            f"{styled_tag('NODE')} Public bootstrap has 0 online seeders "
                            f"and 0 local models — gateway is empty. Check seeder QUIC "
                            f"sessions and recent /v1/admin/nodes/announce traffic."
                        )
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug(f"Registry TTL sweep error: {e}")

    async def _growth_snapshot_loop(self) -> None:
        """Record network growth snapshots every hour."""
        while self._running:
            try:
                await asyncio.sleep(3600)  # 1 hour

                approved = [n for n in self.node_registry.values() if n.get("status") == "approved"]
                online = [n for n in approved if time.time() - n.get("last_seen", 0) < 120]
                stats = self.activity.stats() if hasattr(self, "activity") else {}

                model_names = set(m.name for m in self.inference.loaded_models)
                for entry in approved:
                    for m in entry.get("capabilities", {}).get("models", []):
                        name = m.get("name", m) if isinstance(m, dict) else m
                        model_names.add(name)

                total_vram = self.capabilities.hardware.vram_gb
                for entry in approved:
                    hw = entry.get("system", {}).get("gpu", entry.get("capabilities", {}).get("hardware", {}))
                    total_vram += hw.get("vram_gb", 0)

                total_nodes = 1 + len(approved)
                online_nodes = 1 + len(online)
                total_models = len(model_names)
                total_requests = stats.get("total_requests", 0)
                total_tokens = stats.get("total_tokens", 0)
                total_tps = self.activity.tps if hasattr(self, "activity") else 0

                await self.growth_repo.record(
                    total_nodes=total_nodes,
                    online_nodes=online_nodes,
                    total_models=total_models,
                    total_requests=total_requests,
                    total_tokens=total_tokens,
                    total_tps=total_tps,
                    total_vram_gb=round(total_vram, 1),
                )

                deltas = await self.growth_repo.get_deltas()
                history = await self.growth_repo.get_history()

                self._growth_snapshots = {
                    "deltas": deltas,
                    "history": history,
                    "last_snapshot": {
                        "total_nodes": total_nodes,
                        "online_nodes": online_nodes,
                        "total_models": total_models,
                        "total_requests": total_requests,
                        "total_tokens": total_tokens,
                        "total_tps": total_tps,
                        "total_vram_gb": round(total_vram, 1),
                    },
                }

                logger.debug(f"{styled_tag('NODE')} Growth snapshot recorded: {total_nodes} nodes")

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug(f"Growth snapshot error: {e}")

    def _discover_local_addresses(self) -> list[str]:
        """Discover all routable LAN addresses. Cached for 5 minutes."""
        now = time.time()
        if hasattr(self, "_cached_addresses") and now - self._cached_addresses_at < 300:
            return self._cached_addresses

        from mycellm.nat.discovery import _get_local_ip
        addresses = set()
        addresses.add(f"{_get_local_ip()}:{self.quic_port}")
        try:
            import subprocess
            out = subprocess.check_output(["hostname", "-I"], timeout=2, text=True, stderr=subprocess.DEVNULL).strip()
            for ip in out.split():
                if ":" not in ip and not ip.startswith("127."):
                    addresses.add(f"{ip}:{self.quic_port}")
        except Exception:
            pass
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith("127."):
                    addresses.add(f"{ip}:{self.quic_port}")
        except Exception:
            pass
        if self._settings.external_host:
            addresses.add(f"{self._settings.external_host}:{self.quic_port}")

        result = list(addresses)
        self._cached_addresses = result
        self._cached_addresses_at = now
        return result

    async def announce_capabilities(self) -> None:
        """Re-announce capabilities to all connected peers (e.g. after model load)."""
        from mycellm.transport.messages import peer_announce
        addresses = self._discover_local_addresses()

        msg = peer_announce(
            self.peer_id,
            addresses,
            self.capabilities.to_dict(),
        )
        for conn in self._peer_connections.values():
            try:
                await conn.send(msg)
            except Exception as e:
                logger.debug(f"Failed to announce to {conn.peer_id[:16]}: {e}")

        # Also announce via peer_manager managed connections
        for peer in self.peer_manager.managed_peers.values():
            if peer.connection and peer.peer_id not in self._peer_connections:
                try:
                    await peer.connection.send(msg)
                except Exception as e:
                    logger.debug(f"Failed to announce to managed peer {peer.addr}: {e}")

        # DHT model announcements
        if self._dht_node:
            for m in self.capabilities.models:
                try:
                    await self._dht_node.announce_model(
                        m.name, self.peer_id,
                        [f"{self._quic_host}:{self.quic_port}"],
                    )
                except Exception:
                    pass

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        if not self._running:
            return
        self._running = False
        logger.info(f"{styled_tag('NODE')} Shutting down gracefully...")

        # Cancel peer exchange broadcast
        if self._peer_exchange_task and not self._peer_exchange_task.done():
            self._peer_exchange_task.cancel()

        # Stop resilience loops + watchdog (so it doesn't fire mid-shutdown)
        for _t in (self._heartbeat_task, self._selfheal_task):
            if _t and not _t.done():
                _t.cancel()
        if self._watchdog is not None:
            self._watchdog.stop()

        # Save peer cache
        self._save_peer_cache()

        # Stop peer manager
        await self.peer_manager.stop()

        # Stop health checker
        await self.health_checker.stop()

        # Close peer connections
        for conn in self._peer_connections.values():
            conn.close()
        self._peer_connections.clear()

        # Stop DHT
        if self._dht_node:
            await self._dht_node.stop()

        # Stop API
        if self._api_server:
            self._api_server.should_exit = True

        await asyncio.sleep(0.5)
        raise SystemExit(0)

    def _candidate_models(self, model: str) -> list[str]:
        """Ordered, de-duplicated list of model names to try for a request.

        For a concrete model -> just that name. For ""/"auto" -> every model
        the ModelResolver can currently reach, best-first. Returning the whole
        ranked list (not just resolved[0]) is what lets routing fail over
        across *models*: if the top pick's only seeder is down, the request
        falls through to the next-best model instead of failing the whole
        request. The resolver already excludes peers without a live connection,
        so dead/zombie seeders never appear here.
        """
        if model not in ("", "auto") or not self.model_resolver:
            return [model]
        resolved = self.model_resolver.resolve(
            "", self.inference.loaded_models,
            fleet_registry=self.node_registry,
        )
        seen: set[str] = set()
        names: list[str] = []
        for r in resolved:
            if r.model_name not in seen:
                seen.add(r.model_name)
                names.append(r.model_name)
        return names

    async def route_inference(self, model: str, messages: list[dict], **kwargs):
        """Route inference — local if model loaded, otherwise to peer.

        Uses ModelResolver for empty/"auto" requests. Fails over both across
        QUIC peers serving a model AND across candidate models (so a downed
        seeder degrades to the next-best model rather than failing the call).

        kwargs forwarded to InferenceRequest / peer message:
          temperature, max_tokens, reasoning_exclude
        """
        reasoning_exclude = kwargs.get("reasoning_exclude")
        candidates = self._candidate_models(model)
        if not candidates:
            return None

        from mycellm.inference.base import InferenceRequest
        from mycellm.transport.messages import inference_request

        for effective_model in candidates:
            model_name = self.inference.resolve_model_name(effective_model)

            # Local inference for this candidate, if loaded here.
            if model_name:
                try:
                    req = InferenceRequest(
                        messages=messages,
                        model=model_name,
                        temperature=kwargs.get("temperature", 0.7),
                        max_tokens=kwargs.get("max_tokens", 2048),
                        reasoning_exclude=reasoning_exclude if reasoning_exclude is not None else False,
                    )
                    return await self.inference.generate(req)
                except Exception as e:
                    logger.debug(f"Local inference for '{model_name}' failed: {e}; trying next candidate")
                    continue

            # Peer routing with per-peer failover for this candidate model.
            targets = self.chain_builder.route(effective_model)
            for target in targets:
                if target.entry.connection is None:
                    continue

                req_msg = inference_request(
                    self.peer_id, effective_model, messages,
                    temperature=kwargs.get("temperature", 0.7),
                    max_tokens=kwargs.get("max_tokens", 2048),
                    reasoning_exclude=reasoning_exclude,
                )

                try:
                    resp = await target.entry.connection.request(req_msg)

                    if resp.type == MessageType.ERROR:
                        target.entry.failure_count += 1
                        continue

                    # Success — reduce failure count
                    target.entry.failure_count = max(0, target.entry.failure_count - 1)

                    # Debit consumer
                    if self.ledger:
                        tokens = resp.payload.get("completion_tokens", 0)
                        from mycellm.accounting.pricing import compute_cost
                        cost = compute_cost(max(tokens, 1))
                        await self.ledger.debit(self.peer_id, cost, "inference_consumed",
                                                counterparty_id=target.peer_id)

                    return resp.payload
                except Exception as e:
                    target.entry.failure_count += 1
                    logger.debug(f"Peer {target.peer_id[:16]} routing failed: {e}")
                    continue
            # No peer served this candidate model — fall through to the next.

        return None

    async def route_inference_stream(self, model: str, messages: list[dict], **kwargs):
        """Route streaming inference to a peer. Yields text chunks.

        Fails over across peers and across candidate models, like
        ``route_inference``. Fail-over is only safe *before* any chunk has been
        handed to the caller — once a peer has streamed real output we are
        committed to it (re-routing would duplicate tokens), so a mid-stream
        error ends the stream rather than restarting on another model.
        """
        from mycellm.transport.messages import inference_request
        candidates = self._candidate_models(model)

        for effective_model in candidates:
            targets = self.chain_builder.route(effective_model)

            for target in targets:
                if target.entry.connection is None:
                    continue

                req_msg = inference_request(
                    self.peer_id, effective_model, messages,
                    temperature=kwargs.get("temperature", 0.7),
                    max_tokens=kwargs.get("max_tokens", 2048),
                    stream=True,
                    tools=kwargs.get("tools"),
                    tool_choice=kwargs.get("tool_choice"),
                    reasoning_exclude=kwargs.get("reasoning_exclude"),
                )

                produced = False
                try:
                    async for resp in target.entry.connection.request_stream(req_msg):
                        text = resp.payload.get("text", "")
                        finish_reason = resp.payload.get("finish_reason")
                        tool_calls = resp.payload.get("tool_calls")
                        if text or tool_calls:
                            produced = True
                            yield {"text": text, "finish_reason": finish_reason, "tool_calls": tool_calls, "peer_id": target.peer_id}
                    target.entry.failure_count = max(0, target.entry.failure_count - 1)
                    return
                except Exception as e:
                    target.entry.failure_count += 1
                    logger.debug(f"Peer {target.peer_id[:16]} stream routing failed: {e}")
                    if produced:
                        # Already streamed real tokens downstream — cannot
                        # transparently re-route without duplicating output.
                        return
                    continue
            # No peer streamed this candidate model — try the next-best model.

        return

    def get_operational_mode(self) -> str:
        """Auto-detect operational mode from node state."""
        has_fleet = len(self.node_registry) > 0
        has_bootstrap = bool(self._settings.bootstrap_peers)
        has_models = len(self.inference.loaded_models) > 0
        has_multi_network = self.federation and len(self.federation.network_ids) > 1

        if has_multi_network:
            return "federated"
        if has_fleet:
            return "root"
        if has_bootstrap and has_models:
            return "seeder"
        if has_bootstrap:
            return "consumer"
        if has_models:
            return "standalone"
        return "standalone"

    def get_status(self) -> dict:
        """Return current node status for the API."""
        from mycellm import __version__

        peers = []
        for entry in self.registry.connected_peers():
            peers.append({
                "peer_id": entry.peer_id,
                "role": entry.capabilities.role,
                "models": [m.name for m in entry.capabilities.models],
                "status": entry.state.value,
            })

        credits = {"balance": 0.0, "earned": 0.0, "spent": 0.0}

        return {
            "node_name": self._settings.node_name,
            "version": __version__,
            "peer_id": self.peer_id,
            "uptime_seconds": self.uptime,
            "role": self.capabilities.role,
            "mode": self.get_operational_mode(),
            "tps": self.activity.tps if hasattr(self, 'activity') else 0,
            "hardware": self.capabilities.hardware.to_dict(),
            "credits": credits,
            "peers": peers,
            "models": [m.to_dict() for m in self.inference.loaded_models],
            "inference": {
                "active": self.inference.active_count,
                "max_concurrent": self.inference._max_concurrent,
            },
            "nat": self.nat_discovery.info.to_dict() if hasattr(self, 'nat_discovery') and self.nat_discovery else {},
        }

    def get_system_info(self) -> dict:
        """Return detailed system hardware and software info."""
        import os
        import platform
        import shutil
        import sys

        # CPU info
        cpu_info = {
            "arch": platform.machine(),
            "cores_physical": os.cpu_count(),
            "processor": platform.processor() or "unknown",
        }
        # Try to get a better CPU name
        if platform.system() == "Darwin":
            try:
                import subprocess
                brand = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True, text=True, timeout=5,
                )
                if brand.returncode == 0:
                    cpu_info["name"] = brand.stdout.strip()
                cores = subprocess.run(
                    ["sysctl", "-n", "hw.perflevel0.logicalcpu", "hw.perflevel1.logicalcpu"],
                    capture_output=True, text=True, timeout=5,
                )
                if cores.returncode == 0:
                    parts = cores.stdout.strip().split("\n")
                    if len(parts) == 2:
                        cpu_info["cores_performance"] = int(parts[0])
                        cpu_info["cores_efficiency"] = int(parts[1])
            except Exception:
                pass
        elif platform.system() == "Linux":
            try:
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if line.startswith("model name"):
                            cpu_info["name"] = line.split(":", 1)[1].strip()
                            break
            except Exception:
                pass

        # Memory
        mem_info = {"total_gb": 0, "available_gb": 0, "used_pct": 0}
        if platform.system() == "Linux":
            try:
                with open("/proc/meminfo") as f:
                    meminfo = {}
                    for line in f:
                        parts = line.split(":")
                        if len(parts) == 2:
                            key = parts[0].strip()
                            val = parts[1].strip().split()[0]
                            meminfo[key] = int(val)
                    total = meminfo.get("MemTotal", 0)
                    avail = meminfo.get("MemAvailable", 0)
                    mem_info["total_gb"] = round(total / 1048576, 1)
                    mem_info["available_gb"] = round(avail / 1048576, 1)
                    if total > 0:
                        mem_info["used_pct"] = round((1 - avail / total) * 100, 1)
            except Exception:
                pass
        elif platform.system() == "Darwin":
            try:
                import subprocess
                result = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    mem_info["total_gb"] = round(int(result.stdout.strip()) / (1024 ** 3), 1)
            except Exception:
                pass

        # Disk
        disk_info = {"total_gb": 0, "free_gb": 0, "used_pct": 0}
        try:
            usage = shutil.disk_usage("/")
            disk_info["total_gb"] = round(usage.total / (1024 ** 3), 1)
            disk_info["free_gb"] = round(usage.free / (1024 ** 3), 1)
            disk_info["used_pct"] = round(usage.used / usage.total * 100, 1)
        except Exception:
            pass

        # OS
        os_info = {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "hostname": platform.node(),
        }
        # Friendlier OS description
        if platform.system() == "Darwin":
            try:
                import subprocess
                result = subprocess.run(
                    ["sw_vers", "-productVersion"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    os_info["macos_version"] = result.stdout.strip()
            except Exception:
                pass
        elif platform.system() == "Linux":
            try:
                import subprocess
                result = subprocess.run(
                    ["lsb_release", "-ds"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    os_info["distro"] = result.stdout.strip().strip('"')
            except Exception:
                try:
                    with open("/etc/os-release") as f:
                        for line in f:
                            if line.startswith("PRETTY_NAME="):
                                os_info["distro"] = line.split("=", 1)[1].strip().strip('"')
                                break
                except Exception:
                    pass

        return {
            "cpu": cpu_info,
            "memory": mem_info,
            "disk": disk_info,
            "gpu": self.capabilities.hardware.to_dict(),
            "os": os_info,
            "python": sys.version.split()[0],
            "mycellm_version": "0.1.0",
        }

    async def get_credits(self) -> dict:
        """Get credit info from ledger.

        Served from a short TTL cache — this sits on the /v1/node/status
        hot path, which the menu bar and dashboard poll every few seconds.
        """
        if not self.ledger:
            return {"balance": 0.0, "earned": 0.0, "spent": 0.0}
        account = await self.ledger.get_account_cached(self.peer_id)
        if account:
            return {
                "balance": account["balance"],
                "earned": account["total_earned"],
                "spent": account["total_spent"],
            }
        return {"balance": 0.0, "earned": 0.0, "spent": 0.0}
