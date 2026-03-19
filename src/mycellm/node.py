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
from mycellm.transport.tls import generate_self_signed_cert
from mycellm.transport.auth import build_node_hello, build_hello_ack, verify_hello_message
from mycellm.transport.messages import (
    inference_response,
    error_message,
    pong_message,
    inference_stream_chunk,
    inference_done,
)
from mycellm.transport.connection import PeerConnection, PeerState

logger = logging.getLogger("mycellm")
console = Console()


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
        self.chain_builder = ChainBuilder(self.registry)
        self.health_checker = HealthChecker(self.registry)
        self.ledger = None  # initialized in run()

        # Transport state
        self._quic_server = None
        self._tls_cert_path: Path | None = None
        self._tls_key_path: Path | None = None
        self._peer_connections: dict[str, PeerConnection] = {}
        self._dht_node = None

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
            handlers=[RichHandler(console=console, show_time=True, show_path=False)],
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
        """Detect GPU hardware."""
        try:
            import subprocess
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

        except Exception as e:
            logger.error(f"{styled_tag('INFER')} Inference failed: {e}")
            err = error_message(self.peer_id, msg.id, ErrorCode.BACKEND_ERROR, str(e))
            await protocol.reply_on_stream(stream_id, err)

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

    async def _connect_to_bootstrap_peers(self) -> None:
        """Connect to known bootstrap peers via QUIC."""
        from mycellm.transport.quic import connect_to_peer

        peers = self._settings.get_bootstrap_list()
        for host, port in peers:
            asyncio.create_task(self._dial_peer(host, port))

    async def _dial_peer(self, host: str, port: int) -> None:
        """Dial a specific peer, perform NodeHello handshake."""
        from mycellm.transport.quic import connect_to_peer

        try:
            async with connect_to_peer(host, port) as protocol:
                # Send NodeHello
                hello_msg = build_node_hello(self.device_key, self.device_cert, self.capabilities)
                ack = await protocol.send_and_wait(hello_msg, timeout=10.0)

                if ack.type == MessageType.ERROR:
                    logger.warning(f"{styled_tag('P2P')} Peer rejected: {ack.payload}")
                    return

                if ack.type == MessageType.NODE_HELLO_ACK:
                    from mycellm.protocol.node_hello import NodeHello
                    hello_data = ack.payload.get("hello")
                    if hello_data:
                        peer_hello = NodeHello.from_cbor(hello_data)
                        conn = PeerConnection(
                            peer_id=peer_hello.peer_id,
                            protocol=protocol,
                            hello=peer_hello,
                            state=PeerState.ROUTABLE,
                        )
                        self._peer_connections[peer_hello.peer_id] = conn
                        self.registry.register(
                            peer_hello.peer_id,
                            connection=conn,
                            capabilities=peer_hello.capabilities,
                        )
                        logger.info(
                            f"{styled_tag('P2P')} Connected to {host}:{port} "
                            f"(peer: {peer_hello.peer_id[:16]}...)"
                        )

                        # Keep connection alive until it closes
                        while not protocol._is_closed:
                            await asyncio.sleep(1)

        except Exception as e:
            logger.debug(f"{styled_tag('P2P')} Failed to dial {host}:{port}: {e}")

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

    async def run(self) -> None:
        """Start the node and all subsystems."""
        self._setup_logging()
        self._load_identity()

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

        # Init subsystems
        await self._init_accounting()
        await self._start_transport()

        if self.enable_dht:
            await self._start_dht()

        # Connect to bootstrap peers in background
        asyncio.create_task(self._connect_to_bootstrap_peers())

        # Start health checker
        await self.health_checker.start()

        logger.info(f"{styled_tag('NODE')} Swarm connected. Awaiting inference tasks.")

        # Start API server (blocks)
        await self._start_api()

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        if not self._running:
            return
        self._running = False
        logger.info(f"{styled_tag('NODE')} Shutting down gracefully...")

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
        """Route inference — local if model loaded, otherwise to peer."""
        model_name = self.inference.resolve_model_name(model)

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

        # Try routing to a peer
        from mycellm.transport.messages import inference_request
        targets = self.chain_builder.route(model)
        if not targets:
            return None

        target = targets[0]
        if target.entry.connection is None:
            return None

        req_msg = inference_request(
            self.peer_id, model, messages,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 2048),
        )
        resp = await target.entry.connection.request(req_msg)

        if resp.type == MessageType.ERROR:
            return None

        # Debit consumer
        if self.ledger:
            tokens = resp.payload.get("completion_tokens", 0)
            from mycellm.accounting.pricing import compute_cost
            cost = compute_cost(max(tokens, 1))
            await self.ledger.debit(self.peer_id, cost, "inference_consumed",
                                    counterparty_id=target.peer_id)

        return resp.payload

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
            "node_name": self._settings.node_name or self.device_name,
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
