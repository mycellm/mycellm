"""Protocol error taxonomy."""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """Explicit error codes for protocol responses and health tracking."""

    AUTH_FAILED = "auth_failed"
    CERT_EXPIRED = "cert_expired"
    CERT_REVOKED = "cert_revoked"
    PEER_UNREACHABLE = "peer_unreachable"
    MODEL_UNAVAILABLE = "model_unavailable"
    OVERLOADED = "overloaded"
    TIMEOUT = "timeout"
    BACKEND_ERROR = "backend_error"
    INSUFFICIENT_CREDIT = "insufficient_credit"
    PROTOCOL_VERSION_MISMATCH = "protocol_version_mismatch"
    INVALID_MESSAGE = "invalid_message"
    FLEET_KEY_DENIED = "fleet_key_denied"
    UNKNOWN = "unknown"


class ProtocolError(Exception):
    """Protocol-level error with an error code."""

    def __init__(self, code: ErrorCode, message: str = ""):
        self.code = code
        self.message = message or code.value
        super().__init__(f"[{code.value}] {self.message}")

    def to_payload(self) -> dict:
        return {"error_code": self.code.value, "error_message": self.message}
