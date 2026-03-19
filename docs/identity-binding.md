# Identity Binding Specification

## Overview

Mycellm uses a single canonical Ed25519 keypair per device node. This document defines which key is used where and how identity is verified across protocol layers.

## Key Hierarchy

```
Account Master Key (Ed25519)
  └── Signs Device Certificates
        └── Device Key (Ed25519) — one per node
              ├── PeerId = sha256(device_pubkey)[:32]
              ├── Signs NodeHello messages
              ├── Signs DHT records
              └── Signs credit receipts
```

## Key Types

### Account Key
- **Purpose**: Master identity. Signs device certificates.
- **Generation**: `mycellm account create`
- **Storage**: `$DATA_DIR/keys/account.key` (PEM, mode 0600)
- **Rotation**: Requires re-signing all active device certs.
- **Sharing**: Public key can be exported. Private key never leaves the device that created it.

### Device Key
- **Purpose**: Node identity. Used for all protocol operations.
- **Generation**: `mycellm device create`
- **Storage**: `$DATA_DIR/keys/device-{name}.key` (PEM, mode 0600)
- **Lifetime**: Tied to the device certificate TTL.

## Device Certificate

Signed by account master key. Proves a device belongs to an account.

```cbor
{
  account_pubkey: bytes(32),   // Account public key
  device_pubkey:  bytes(32),   // Device public key
  device_name:    string,      // Human-readable name
  role:           string,      // "seeder" | "consumer" | "relay"
  created_at:     float,       // Unix timestamp
  expires_at:     float,       // 0 = no expiry
  revoked:        bool,
  signature:      bytes(64),   // Ed25519 sig by account key
}
```

## NodeHello (Transport Authentication)

Exchanged after QUIC+TLS connection establishment. Proves the connecting node controls the claimed device key.

```cbor
{
  peer_id:        string,      // sha256(device_pubkey)[:32]
  device_pubkey:  bytes(32),   // Device public key
  cert:           bytes,       // CBOR-encoded DeviceCert
  capabilities:   map,         // Capability advertisement
  nonce:          bytes(32),   // Random nonce
  timestamp:      float,       // Unix timestamp
  signature:      bytes(64),   // Ed25519 sig by device key over (nonce, timestamp, peer_id)
}
```

## Verification Steps

On receiving a NodeHello, verify in order:

1. **Timestamp freshness**: `abs(now - hello.timestamp) < 300s`
2. **Certificate signature**: `verify(cert.signature, cert.payload, cert.account_pubkey)`
3. **Certificate validity**: Not expired, not revoked
4. **Key binding**: `cert.device_pubkey == hello.device_pubkey`
5. **PeerId derivation**: `hello.peer_id == sha256(hello.device_pubkey)[:32]`
6. **Hello signature**: `verify(hello.signature, hello.signable_data, hello.device_pubkey)`

If any check fails, close the connection and log the error.

## Where Identity Is Used

| Layer | Key Used | Purpose |
|-------|----------|---------|
| TLS (QUIC) | Ephemeral ECDSA | Transport encryption only |
| NodeHello | Device Ed25519 | Identity binding, cert presentation |
| DHT Records | Device Ed25519 | Sign announcements (untrusted hints) |
| Inference Requests | Device Ed25519 (via authenticated connection) | Request attribution |
| Credit Receipts | Device Ed25519 | Sign bilateral receipts |

## Why Not Use TLS Certs for Identity?

QUIC requires TLS 1.3, which provides transport encryption. However:

- TLS cert management is complex and not well-suited to P2P scenarios
- We need custom identity semantics (account hierarchy, roles, TTL)
- NodeHello at app layer gives us full control over identity verification
- TLS certs are ephemeral/self-signed — identity verification happens via NodeHello

## Revocation

Local revocation list stored at `$DATA_DIR/revocations.json`:

```json
{
  "revoked": ["device_pubkey_hex_1", "device_pubkey_hex_2"]
}
```

Phase 1: Revocation is local only. Each node maintains its own list.
Phase 2: Distributed revocation via account-signed revocation records.
