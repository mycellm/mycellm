"""Protocol fundamentals: message envelope, error codes, capabilities."""

from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.protocol.errors import ErrorCode, ProtocolError
from mycellm.protocol.capabilities import Capabilities, ModelCapability, HardwareInfo

__all__ = [
    "MessageEnvelope",
    "MessageType",
    "ErrorCode",
    "ProtocolError",
    "Capabilities",
    "ModelCapability",
    "HardwareInfo",
]
