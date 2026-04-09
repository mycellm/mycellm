"""E2E test harness — spawn N subprocess nodes, manage ports/data dirs, wait for readiness."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx


@dataclass
class NodeInstance:
    """A spawned mycellm node subprocess."""

    name: str
    data_dir: Path
    api_port: int
    quic_port: int
    dht_port: int
    process: Optional[subprocess.Popen] = None
    log_file: Optional[str] = None

    @property
    def api_url(self) -> str:
        return f"http://localhost:{self.api_port}"

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None


class E2EHarness:
    """Manages a set of mycellm node subprocesses for testing."""

    def __init__(self, base_port: int = 18420, node_count: int = 3):
        self.base_port = base_port
        self.node_count = node_count
        self.nodes: list[NodeInstance] = []
        self._tmpdir: Optional[str] = None

    def setup(self) -> list[NodeInstance]:
        """Create node configurations without starting them."""
        self._tmpdir = tempfile.mkdtemp(prefix="mycellm-e2e-")

        for i in range(self.node_count):
            name = f"node-{i}"
            data_dir = Path(self._tmpdir) / name
            data_dir.mkdir()

            node = NodeInstance(
                name=name,
                data_dir=data_dir,
                api_port=self.base_port + i * 10,
                quic_port=self.base_port + i * 10 + 1,
                dht_port=self.base_port + i * 10 + 2,
            )
            self.nodes.append(node)

        return self.nodes

    def provision_identity(self, node: NodeInstance) -> None:
        """Create account + device identity for a node."""
        from mycellm.identity.keys import generate_account_key, generate_device_key
        from mycellm.identity.certs import create_device_cert

        keys_dir = node.data_dir / "keys"
        certs_dir = node.data_dir / "certs"
        keys_dir.mkdir(exist_ok=True)
        certs_dir.mkdir(exist_ok=True)

        account = generate_account_key()
        account.save(keys_dir)

        device = generate_device_key()
        device.save(keys_dir, device_name="default")

        cert = create_device_cert(account, device, device_name="default", role="seeder")
        cert.save(certs_dir)

    def start_node(self, node: NodeInstance) -> None:
        """Start a node subprocess."""
        env = os.environ.copy()
        env["MYCELLM_DATA_DIR"] = str(node.data_dir)
        env["MYCELLM_CONFIG_DIR"] = str(node.data_dir / "config")
        env["MYCELLM_API_PORT"] = str(node.api_port)
        env["MYCELLM_QUIC_PORT"] = str(node.quic_port)
        env["MYCELLM_DHT_PORT"] = str(node.dht_port)

        # Build bootstrap peers from other nodes
        peers = []
        for other in self.nodes:
            if other.name != node.name:
                peers.append(f"localhost:{other.quic_port}")
        env["MYCELLM_BOOTSTRAP_PEERS"] = ",".join(peers)

        log_path = node.data_dir / "node.log"
        log_fh = open(log_path, "w")
        node.log_file = str(log_path)

        node.process = subprocess.Popen(
            [sys.executable, "-m", "mycellm.cli.main", "serve",
             "--host", "127.0.0.1",
             "--port", str(node.api_port),
             "--quic-port", str(node.quic_port),
             "--dht-port", str(node.dht_port),
             "--no-dht"],
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )

    async def wait_ready(self, node: NodeInstance, timeout: float = 30.0) -> bool:
        """Wait for a node's API to become healthy."""
        deadline = time.time() + timeout
        async with httpx.AsyncClient() as client:
            while time.time() < deadline:
                try:
                    resp = await client.get(f"{node.api_url}/health", timeout=2.0)
                    if resp.status_code == 200:
                        return True
                except (httpx.ConnectError, httpx.ReadTimeout):
                    pass
                await asyncio.sleep(0.5)
        return False

    async def start_all(self) -> None:
        """Provision identities, start all nodes, wait for readiness."""
        for node in self.nodes:
            self.provision_identity(node)

        for node in self.nodes:
            self.start_node(node)

        for node in self.nodes:
            ready = await self.wait_ready(node)
            if not ready:
                raise RuntimeError(f"Node {node.name} failed to start (port {node.api_port})")

    def stop_node(self, node: NodeInstance) -> None:
        """Stop a node subprocess."""
        if node.process and node.process.poll() is None:
            node.process.send_signal(signal.SIGTERM)
            try:
                node.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                node.process.kill()
                node.process.wait()

    def teardown(self) -> None:
        """Stop all nodes and clean up."""
        for node in self.nodes:
            self.stop_node(node)
        if self._tmpdir and os.path.exists(self._tmpdir):
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        self.nodes.clear()
