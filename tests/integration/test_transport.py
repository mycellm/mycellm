"""Integration test: QUIC transport between two nodes on localhost."""

import asyncio


from mycellm.identity.certs import create_device_cert
from mycellm.identity.keys import generate_account_key, generate_device_key
from mycellm.identity.peer_id import peer_id_from_public_key
from mycellm.protocol.capabilities import Capabilities, ModelCapability
from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.transport.auth import build_node_hello, verify_hello_message
from mycellm.transport.messages import ping_message, pong_message
from mycellm.transport.tls import generate_self_signed_cert


def _make_node_identity(name="test"):
    account = generate_account_key()
    device = generate_device_key()
    cert = create_device_cert(account, device, device_name=name)
    peer_id = peer_id_from_public_key(device.public_key)
    caps = Capabilities(models=[ModelCapability(name="test-model")])
    return device, cert, peer_id, caps


async def test_quic_connect_and_hello():
    """Two nodes connect via QUIC, exchange NodeHello, send ping/pong."""
    from mycellm.transport.quic import create_quic_server, connect_to_peer

    server_device, server_cert, server_pid, server_caps = _make_node_identity("server")
    client_device, client_cert, client_pid, client_caps = _make_node_identity("client")

    cert_path, key_path = generate_self_signed_cert()
    received_messages = []
    auth_done = asyncio.Event()

    async def server_handler(protocol, msg: MessageEnvelope, stream_id: int):
        received_messages.append(msg)

        if msg.type == MessageType.NODE_HELLO:
            # Verify and send ack
            hello, _ = verify_hello_message(msg)
            from mycellm.transport.auth import build_hello_ack
            ack = build_hello_ack(server_device, server_cert, server_caps, request_id=msg.id)
            await protocol.reply_on_stream(stream_id, ack)
            auth_done.set()

        elif msg.type == MessageType.PING:
            reply = pong_message(server_pid, msg.id)
            await protocol.reply_on_stream(stream_id, reply)

    server = await create_quic_server(
        "127.0.0.1", 18521, cert_path, key_path, server_handler
    )

    try:
        async with connect_to_peer("127.0.0.1", 18521) as client_proto:
            # Send NodeHello
            hello_msg = build_node_hello(client_device, client_cert, client_caps)
            ack = await client_proto.send_and_wait(hello_msg, timeout=5.0)
            assert ack.type == MessageType.NODE_HELLO_ACK

            # Verify server's hello ack
            from mycellm.protocol.node_hello import NodeHello as NH
            ack_hello = NH.from_cbor(ack.payload["hello"])
            assert ack_hello.peer_id == server_pid

            # Send ping
            ping = ping_message(client_pid)
            pong = await client_proto.send_and_wait(ping, timeout=5.0)
            assert pong.type == MessageType.PONG
            assert pong.id == ping.id

    finally:
        server.close()


async def test_quic_bad_hello_rejected():
    """Connection with invalid NodeHello is rejected."""
    from mycellm.transport.quic import create_quic_server, connect_to_peer

    server_device, server_cert, server_pid, server_caps = _make_node_identity("server")
    cert_path, key_path = generate_self_signed_cert()

    async def server_handler(protocol, msg: MessageEnvelope, stream_id: int):
        if msg.type == MessageType.NODE_HELLO:
            try:
                verify_hello_message(msg)
            except Exception:
                from mycellm.transport.messages import error_message
                from mycellm.protocol.errors import ErrorCode
                err = error_message(server_pid, msg.id, ErrorCode.AUTH_FAILED)
                await protocol.reply_on_stream(stream_id, err)

    server = await create_quic_server(
        "127.0.0.1", 18522, cert_path, key_path, server_handler
    )

    try:
        async with connect_to_peer("127.0.0.1", 18522) as client_proto:
            # Send a hello with wrong peer_id
            bad_hello = MessageEnvelope(
                type=MessageType.NODE_HELLO,
                from_peer="bad",
                payload={"hello": b"invalid"},
            )
            resp = await client_proto.send_and_wait(bad_hello, timeout=5.0)
            assert resp.type == MessageType.ERROR
            assert resp.payload["error_code"] == "auth_failed"
    finally:
        server.close()
