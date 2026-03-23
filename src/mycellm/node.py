"""MycellmNode — daemon entrypoint composing all subsystems."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from mycellm.cli.banner import styled_tag
from mycellm.config import get_settings, MycellmSettings
from mycellm.identity.certs import DeviceCert
from mycellm.identity.keys import AccountKey, DeviceKey
from mycellm.identity.peer_id import peer_id_from_public_key
from mycellm.inference.manager import InferenceManager
from mycellm.protocol.capabilities import Capabilities, HardwareInfo, ModelCapability
from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.protocol.errors import ErrorCode
from mycellm.router.registry import PeerRegistry
from mycellm.router.chain import ChainBuilder
from mycellm.router.health import HealthChecker
from mycellm.router.model_resolver import ModelResolver
from mycellm.transport.tls import generate_self_signed_cert
from mycellm.transport.auth import build_node_hello, build_hello_ack, verify_hello_message
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
        self.registry = PeerRegistry()
        self.health_checker = HealthChecker(self.registry)
        self.chain_builder = ChainBuilder(self.registry, health_checker=self.health_checker)
        self.model_resolver = ModelResolver(self.registry)
        self.ledger = None  # initialized in run()
        self.reputation = ReputationTracker()
        self.receipt_validator = ReceiptValidator()

        # Peer manager
        self.peer_manager = PeerManager(self)

        # Transport state
        self._quic_server = None
        self._tls_cert_path: Path | None = None
        self._tls_key_path: Path | None = None
        self._peer_connections: dict[str, PeerConnection] = {}
        self._dht_node = None

        # Log broadcaster for dashboard SSE
        self.log_broadcaster = LogBroadcaster()

        # Activity tracker
        self.activity = ActivityTracker()

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
        from mycellm.transport.quic import MycellmQuicProtocol
        from mycellm.inference.base import InferenceRequest

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
                self.registry.register(
                    hello.peer_id,
                    connection=conn,
                    capabilities=hello.capabilities,
                )
                conn.state = PeerState.ROUTABLE

                # Set network membership info
                reg_entry = self.registry.get(hello.peer_id)
                if reg_entry:
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
            self.registry.register(
                msg.from_peer,
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
            err = error_message(self.peer_id, msg.id, ErrorCode.MODEL_UNAVAILABLE)
            await protocol.reply_on_stream(stream_id, err)
            return

        if self.inference.is_overloaded:
            err = error_message(self.peer_id, msg.id, ErrorCode.OVERLOADED)
            await protocol.reply_on_stream(stream_id, err)
            return

        req = InferenceRequest(
            messages=messages,
            model=model_name,
            temperature=payload.get("temperature", 0.7),
            max_tokens=payload.get("max_tokens", 2048),
        )

        try:
            if stream:
                async for chunk in self.inference.generate_stream(req):
                    chunk_msg = inference_stream_chunk(
                        self.peer_id, msg.id, chunk.text, chunk.finish_reason
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

            # Credit the seeder (with rate limiting)
            if self.ledger:
                tokens = result.completion_tokens if not stream else 0
                from mycellm.accounting.pricing import compute_reward
                reward = compute_reward(max(tokens, 1))

                # Generate signed receipt
                sig = ""
                ts = time.time()
                if not stream:
                    receipt_data = build_receipt_data(
                        consumer_id=msg.from_peer,
                        seeder_id=self.peer_id,
                        model=model_name,
                        tokens=result.completion_tokens,
                        cost=reward,
                        request_id=msg.id,
                        timestamp=ts,
                    )
                    sig = sign_receipt(self.device_key, receipt_data)

                if self.receipt_validator.check_credit_rate(self.peer_id):
                    await self.ledger.credit(self.peer_id, reward, "inference_served",
                                             counterparty_id=msg.from_peer,
                                             receipt_signature=sig)
                else:
                    logger.warning(f"Credit rate limit reached, skipping self-credit")

                # Send signed receipt to consumer
                if sig:
                    from mycellm.transport.messages import signed_credit_receipt
                    receipt_msg = signed_credit_receipt(
                        self.peer_id, msg.from_peer, self.peer_id,
                        model_name, result.completion_tokens, reward,
                        ts, sig,
                    )
                    # Include request_id for replay protection
                    receipt_msg.payload["request_id"] = msg.id
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

        # Verify Ed25519 signature
        if signature and conn.hello and conn.hello.cert:
            receipt_data = build_receipt_data(
                consumer_id=payload.get("consumer_id", ""),
                seeder_id=payload.get("seeder_id", ""),
                model=payload.get("model", ""),
                tokens=payload.get("tokens", 0),
                cost=payload.get("cost", 0.0),
                request_id=request_id,
                timestamp=payload.get("timestamp", 0.0),
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

    def _handle_peer_exchange(self, msg: MessageEnvelope) -> None:
        """Handle peer exchange -- learn about peers from connected peer."""
        peers = msg.payload.get("peers", [])
        for p in peers:
            peer_id = p.get("peer_id", "")
            if peer_id and peer_id != self.peer_id and peer_id not in self._peer_connections:
                addrs = p.get("addresses", [])
                if addrs:
                    caps = Capabilities.from_dict(p.get("capabilities", {}))
                    self.registry.register(peer_id, capabilities=caps, addresses=addrs)
                    # Try to connect to newly discovered peers
                    for addr in addrs:
                        if ":" in addr:
                            host, port_str = addr.rsplit(":", 1)
                            try:
                                self.peer_manager.add_peer(host, int(port_str))
                            except (ValueError, AttributeError):
                                pass

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
        # 1. Restore persisted models (can take minutes for large GGUF files)
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

        # Handle signals
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(self.shutdown()))

        # Init subsystems (DB engine, ledger, repositories)
        await self._init_accounting()

        # Load persisted node registry from DB
        self.node_registry = await self.node_registry_repo.load_as_dict()
        await self._start_transport()

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
        # Public name: peer_id prefix, not hostname (privacy + dedup)
        public_name = f"node-{self.peer_id[:8]}"

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
                # LAN IPs: use http://host:api_port, send real hostname
                # Public domains: use https://host, send anonymized name
                is_lan = host.startswith("10.") or host.startswith("192.168.") or host.startswith("172.") or host.startswith("127.") or host == "localhost"
                payload = {**base_payload}
                if is_lan:
                    api_port = port if port != 8421 else 8420
                    url = f"http://{host}:{api_port}/v1/admin/nodes/announce"
                    payload["node_name"] = self._settings.node_name
                    payload["system"] = sys_info
                else:
                    url = f"https://{host}/v1/admin/nodes/announce"
                    payload["node_name"] = public_name
                    # Public: only share GPU type + model count, not full system info
                    payload["system"] = {
                        "gpu": sys_info.get("gpu", {}),
                    }
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
            return any_ok

        # Initial announce
        await _do_announce()

        # Re-announce loop — never exits, never crashes
        interval = 15
        while self._running:
            try:
                await asyncio.sleep(interval)
                ok = await _do_announce()
                interval = 60 if ok else min(interval + 10, 60)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"{styled_tag('NODE')} Announce loop error: {e}")
                interval = 30

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

    async def announce_capabilities(self) -> None:
        """Re-announce capabilities to all connected peers (e.g. after model load)."""
        from mycellm.transport.messages import peer_announce

        msg = peer_announce(
            self.peer_id,
            [f"{self.api_host}:{self.quic_port}"],
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

    async def route_inference(self, model: str, messages: list[dict], **kwargs):
        """Route inference — local if model loaded, otherwise to peer.

        Uses ModelResolver for empty model requests to find the best candidate.
        Supports failover across multiple QUIC peers.
        """
        # Resolve empty model via ModelResolver
        effective_model = model
        if not model and self.model_resolver:
            resolved = self.model_resolver.resolve(
                "", self.inference.loaded_models,
                fleet_registry=self.node_registry,
            )
            if resolved:
                best = resolved[0]
                if best.source == "local":
                    effective_model = best.model_name
                else:
                    effective_model = best.model_name

        model_name = self.inference.resolve_model_name(effective_model)

        # Try local inference first
        if model_name:
            from mycellm.inference.base import InferenceRequest
            req = InferenceRequest(
                messages=messages,
                model=model_name,
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 2048),
            )
            return await self.inference.generate(req)

        # Try routing to a peer (with failover)
        from mycellm.transport.messages import inference_request
        targets = self.chain_builder.route(effective_model)
        if not targets:
            return None

        last_error = None
        for target in targets:
            if target.entry.connection is None:
                continue

            req_msg = inference_request(
                self.peer_id, effective_model, messages,
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 2048),
            )

            try:
                resp = await target.entry.connection.request(req_msg)

                if resp.type == MessageType.ERROR:
                    target.entry.failure_count += 1
                    last_error = resp
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
                last_error = e
                continue

        return None

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
        """Get credit info from ledger."""
        if not self.ledger:
            return {"balance": 0.0, "earned": 0.0, "spent": 0.0}
        account = await self.ledger.get_account(self.peer_id)
        if account:
            return {
                "balance": account["balance"],
                "earned": account["total_earned"],
                "spent": account["total_spent"],
            }
        return {"balance": 0.0, "earned": 0.0, "spent": 0.0}
