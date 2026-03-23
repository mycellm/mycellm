# mycellm — Known Issues

*Last updated: March 23, 2026*

## Security & Privacy

### Prompts are visible to seeder nodes
When you send a message on the public network, the seeder node that runs inference sees your full prompt. This is inherent to the distributed architecture — the node needs the prompt to generate a response.

**Mitigation**: The [Sensitive Data Guard](https://docs.mycellm.dev/config/privacy-guard/) scans for API keys, passwords, and PII before sending. Use on-device inference for sensitive content.

### Credit system is not Sybil-resistant
New node identities receive 100 seed credits. An attacker could generate unlimited credits by creating new identities. Receipt validation is enforced locally — the bootstrap does not verify receipt signatures server-side.

**Impact**: Low for beta. Credits have no monetary value. Future: require proof-of-work or reputation-based credit issuance.

### No TLS certificate pinning on QUIC
Both iOS and Python disable TLS certificate verification on QUIC connections. Identity is verified at the application layer via Ed25519-signed NodeHello. A MitM attacker could observe (but not forge) authenticated traffic.

**Future**: Pin the bootstrap's public key or implement TLS channel binding with NodeHello.

## Infrastructure

### Single bootstrap node
The public network has one bootstrap server at `bootstrap.mycellm.dev`. If it goes down, new nodes cannot join and the public gateway is unavailable. Existing P2P connections on LAN continue working.

**Future**: Multiple bootstrap servers with DHT-based discovery fallback.

### Fleet management is partially implemented
Fleet commands `node.status`, `model.list`, and `model.scope` work. `model.load`, `model.unload`, and `set_mode` are not yet implemented on the iOS app.

## iOS App

### Foreground-only operation
iOS suspends apps after ~30 seconds in background. The QUIC connection drops, and the node goes offline. It reconnects when the app returns to foreground. For always-on nodes, use a Mac/Linux server.

### TLSConfig stub
`generateSelfSignedIdentity()` returns nil — the QUIC client TLS identity is not set. Transport encryption still works (aioquic generates ephemeral certs), but there's no persistent client certificate.

### Model sharding not implemented
The About page mentions model sharding across GPUs. This is a roadmap item — currently one model per node, loaded entirely into memory.

## Protocol

### DHT discovery is optional
The Kademlia DHT is not tested at scale. Use `--no-dht` if you experience issues. The bootstrap's HTTP peer registry is the primary discovery mechanism.

### STUN/ICE hole punching is new
NAT traversal via UDP hole punching is implemented but not extensively tested across NAT types. The relay fallback always works.
