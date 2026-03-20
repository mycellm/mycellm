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
from mycellm.accounting.reputation import ReputationTracker
from mycellm.transport.messages import (
    inference_response,
    error_message,
    pong_message,
    inference_stream_chunk,
    inference_done,
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

        # API server ref for shutdown
        self._api_server = None

    @property
    def uptime(self) -> float:
        if self._start_time == 0:
            return 0.0
        return time.time() - self._start_time

    def _setup_logging(self) -> None:
        logging.basicConfig(
            level=logging.INFO,
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
        """Initialize the credit accounting database."""
        from mycellm.accounting.schema import init_db
        from mycellm.accounting.local_ledger import LocalLedger

        db_path = str(self._settings.db_path)
        await init_db(db_path)
        self.ledger = LocalLedger(db_path)
        await self.ledger.ensure_account(self.peer_id, self._settings.initial_credits)
        logger.info(f"{styled_tag('CREDIT')} Ledger initialized (balance: {self._settings.initial_credits:.2f})")

    async def _start_transport(self) -> None:
        """Start the QUIC transport server."""
        from mycellm.transport.quic import create_quic_server

        self._tls_cert_path, self._tls_key_path = generate_self_signed_cert(
            cert_path=self._settings.data_dir / "tls" / "cert.pem",
            key_path=self._settings.data_dir / "tls" / "key.pem",
        )

        self._quic_server = await create_quic_server(
            host=self._settings.quic_host,
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

        elif msg.type in (
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

    async def _handle_inference_request(self, protocol, msg: MessageEnvelope, stream_id: int) -> None:
        """Handle an incoming inference request from a peer."""
        from mycellm.inference.base import InferenceRequest

        payload = msg.payload
        model = payload.get("model", "")
        messages = payload.get("messages", [])
        stream = payload.get("stream", False)

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

            # Credit the seeder
            if self.ledger:
                tokens = result.completion_tokens if not stream else 0
                from mycellm.accounting.pricing import compute_reward
                reward = compute_reward(max(tokens, 1))
                await self.ledger.credit(self.peer_id, reward, "inference_served",
                                         counterparty_id=msg.from_peer)

                # Send signed credit receipt to consumer
                if not stream:
                    import cbor2
                    receipt_data = cbor2.dumps({
                        "consumer": msg.from_peer,
                        "seeder": self.peer_id,
                        "model": model_name,
                        "tokens": result.completion_tokens,
                        "cost": reward,
                        "ts": time.time(),
                    })
                    sig = self.device_key.sign(receipt_data).hex()
                    from mycellm.transport.messages import signed_credit_receipt
                    receipt_msg = signed_credit_receipt(
                        self.peer_id, msg.from_peer, self.peer_id,
                        model_name, result.completion_tokens, reward,
                        time.time(), sig,
                    )
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
        """Handle a signed credit receipt from a peer."""
        payload = msg.payload
        signature = payload.get("signature", "")

        # Verify signature (receipt signed by seeder's device key)
        # For now, store if from authenticated peer
        if msg.from_peer in self._peer_connections:
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
            logger.debug(f"Receipt from {msg.from_peer[:8]}: {payload.get('tokens', 0)} tokens")

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
                [f"{self._settings.quic_host}:{self.quic_port}"],
                self.capabilities.to_dict(),
            )
        except Exception as e:
            logger.warning(f"{styled_tag('DHT')} Failed to start: {e}")
            self._dht_node = None

    async def _start_api(self) -> None:
        """Start the FastAPI server."""
        import uvicorn
        from mycellm.api.app import create_app

        app = create_app(self)
        config = uvicorn.Config(
            app, host=self.api_host, port=self.api_port, log_level="warning",
        )
        self._api_server = uvicorn.Server(config)
        logger.info(f"{styled_tag('API')} http://{self.api_host}:{self.api_port}")
        await self._api_server.serve()

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
            cache_path.write_text(json.dumps(cache, indent=2))
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
            bootstrap_addrs=[f"{self._settings.quic_host}:{self.quic_port}"],
        )

        # Load cached peers
        self._load_peer_cache()

        # Restore persisted models
        restored = await self.inference.restore_models(self._settings.data_dir)
        if restored:
            self.capabilities.models = self.inference.loaded_models
            logger.info(f"{styled_tag('BOOT')} Restored {restored} model(s)")

        hw = self._detect_hardware()
        self.capabilities = Capabilities(
            models=self.inference.loaded_models,
            hardware=hw,
            role=self.device_cert.role if self.device_cert else "seeder",
            version="0.1.0",
        )

        self._running = True
        self._start_time = time.time()

        # Handle signals
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(self.shutdown()))

        # Load persisted node registry (survives restarts)
        from mycellm.api.admin import _load_registry
        _load_registry(self)

        # Init subsystems
        await self._init_accounting()
        await self._start_transport()

        if self.enable_dht:
            await self._start_dht()

        # Connect to bootstrap peers via PeerManager
        peers = self._settings.get_bootstrap_list()
        await self.peer_manager.start(peers)

        # Announce to bootstrap nodes via HTTP
        self._announce_task = asyncio.create_task(self._announce_to_bootstrap())

        # Start health checker
        await self.health_checker.start()

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
        payload = {
            "peer_id": self.peer_id,
            "node_name": self._settings.node_name,
            "api_addr": f"{self.api_host}:{self.api_port}",
            "role": self.capabilities.role,
            "capabilities": self.capabilities.to_dict(),
            "system": sys_info,
        }

        async def _do_announce():
            payload["capabilities"] = self.capabilities.to_dict()
            for host, port in peers:
                api_port = port - 1
                url = f"http://{host}:{api_port}/v1/admin/nodes/announce"
                try:
                    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
                    async with httpx.AsyncClient(timeout=10, transport=transport) as client:
                        resp = await client.post(url, json=payload, headers=headers)
                        if resp.status_code == 200:
                            logger.info(f"{styled_tag('NODE')} Announced to bootstrap {host}:{api_port}")
                            self.activity.record(EventType.ANNOUNCE_OK, bootstrap=f"{host}:{api_port}")
                            return True
                        elif resp.status_code == 401:
                            logger.warning(f"{styled_tag('SECURITY')} Bootstrap rejected announce (bad API key)")
                            self.activity.record(EventType.ANNOUNCE_FAILED, bootstrap=f"{host}:{api_port}", reason="auth_rejected")
                except Exception as e:
                    logger.warning(f"{styled_tag('NODE')} Announce to {host}:{api_port} failed: {e}")
                    self.activity.record(EventType.ANNOUNCE_FAILED, bootstrap=f"{host}:{api_port}", reason=str(e))
            return False

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
                        [f"{self._settings.quic_host}:{self.quic_port}"],
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
