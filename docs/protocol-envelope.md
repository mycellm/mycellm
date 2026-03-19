# Protocol Envelope Specification

## Overview

Every peer-to-peer message in mycellm uses a standardized envelope format, serialized as CBOR. The envelope provides versioning, message typing, request correlation, and sender identification.

## Envelope Format

```cbor
{
  "v":       uint,      // Protocol version (currently 1)
  "type":    string,    // Message type enum
  "id":      string,    // Request ID (16 hex chars) for correlation
  "ts":      float,     // Unix timestamp
  "from":    string,    // Sender's PeerId
  "payload": map,       // Type-specific data
}
```

## Message Types

### Handshake
- `node_hello` — Initial identity binding after QUIC connection
- `node_hello_ack` — Server's reciprocal identity binding

### Discovery
- `peer_announce` — Announce presence (via DHT or direct)
- `peer_query` — Query for peers serving a model
- `peer_response` — Response to peer query

### Inference
- `inference_req` — Request inference from a peer
- `inference_resp` — Complete inference response
- `inference_stream` — Streaming inference chunk
- `inference_done` — End of streaming inference

### Health
- `ping` — Health check request
- `pong` — Health check response

### Accounting
- `credit_receipt` — Bilateral signed credit receipt

### Error
- `error` — Error response with error code

## Error Codes

| Code | Description |
|------|-------------|
| `auth_failed` | Authentication/authorization failure |
| `cert_expired` | Device certificate has expired |
| `cert_revoked` | Device certificate is revoked |
| `peer_unreachable` | Cannot reach target peer |
| `model_unavailable` | Requested model not available |
| `overloaded` | Peer at capacity |
| `timeout` | Request timed out |
| `backend_error` | Inference backend error |
| `insufficient_credit` | Not enough credits |
| `protocol_version_mismatch` | Incompatible protocol version |
| `invalid_message` | Malformed message |

## Transport Framing

Messages are framed with a 4-byte big-endian length prefix:

```
[4 bytes: payload length][N bytes: CBOR-encoded envelope]
```

Maximum frame size: 10 MB (sanity limit).

For QUIC streams, messages can alternatively be sent as the complete stream data (using end_stream=True) without length framing.

## Versioning

The `v` field enables protocol evolution:
- Nodes MUST reject messages with `v > supported_version`
- Nodes SHOULD accept messages with `v <= supported_version`
- Version mismatches produce `protocol_version_mismatch` error

## Request Correlation

The `id` field correlates requests with responses. When responding to a request, the response MUST use the same `id` as the request. This enables:
- Matching responses to pending futures
- Request timeout tracking
- Deduplication
