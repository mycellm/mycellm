"""CBOR-safe serialization for adapters exchanged over the training protocol.

An adapter is a name -> ndarray map; CBOR has no native tensor type, so each
tensor becomes a small map {dtype, shape, data} with the raw little-endian
buffer as a byte string. Round-trips are exact for the dtypes LoRA adapters use
(float16/float32/bfloat16-as-float32). Kept deliberately tiny and dependency-
free (numpy only) so both the coordinator and a participant node share it.
"""

from __future__ import annotations

import numpy as np

# Adapters are usually shipped fp16 to halve wire size; the aggregation math
# upcasts to float64 internally, so precision is set by what participants send.
_SUPPORTED = {"float16", "float32", "float64", "int8", "uint8"}


def encode_tensor(arr: np.ndarray) -> dict:
    """Serialize one ndarray to a CBOR-friendly dict."""
    dtype = str(arr.dtype)
    if dtype not in _SUPPORTED:
        raise ValueError(f"unsupported tensor dtype for wire: {dtype}")
    # Force little-endian, C-contiguous so the buffer is portable + decodable
    # identically on any peer regardless of host endianness.
    contiguous = np.ascontiguousarray(arr.astype(arr.dtype.newbyteorder("<")))
    return {
        "dtype": dtype,
        "shape": list(arr.shape),
        "data": contiguous.tobytes(),
    }


def decode_tensor(obj: dict) -> np.ndarray:
    """Inverse of encode_tensor."""
    dtype = obj["dtype"]
    if dtype not in _SUPPORTED:
        raise ValueError(f"unsupported tensor dtype from wire: {dtype}")
    shape = tuple(obj["shape"])
    arr = np.frombuffer(obj["data"], dtype=np.dtype(dtype).newbyteorder("<"))
    expected = int(np.prod(shape)) if shape else 1
    if arr.size != expected:
        raise ValueError(
            f"tensor buffer size {arr.size} != product of shape {shape} ({expected})"
        )
    # Copy so the returned array owns writable memory (frombuffer is read-only).
    return arr.reshape(shape).astype(np.dtype(dtype)).copy()


def encode_adapter(adapter: dict[str, np.ndarray]) -> dict[str, dict]:
    """Serialize a full name -> ndarray adapter map."""
    return {name: encode_tensor(t) for name, t in adapter.items()}


def decode_adapter(obj: dict[str, dict]) -> dict[str, np.ndarray]:
    """Inverse of encode_adapter."""
    return {name: decode_tensor(t) for name, t in obj.items()}


def adapter_nbytes(adapter: dict[str, np.ndarray]) -> int:
    """Total raw tensor bytes — used to decide chunking + log wire cost."""
    return sum(int(t.nbytes) for t in adapter.values())
