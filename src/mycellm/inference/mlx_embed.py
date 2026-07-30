"""Embedding-only MLX backend (mlx-embeddings: MiniLM / BERT / XLM-R family).

Fills the gap where the MLX text backends raise EmbeddingsNotSupportedError —
Apple Silicon nodes previously needed a GGUF embedding model via llama.cpp.

Batching is length-grouped: inputs are sorted by token length and encoded in
buckets, so short texts never pay a long text's padding (oMLX measured ~4×
on heterogeneous embedding batches vs one naively padded batch). Original
input order is restored in the result.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator

from mycellm.inference.base import (
    EmbeddingRequest,
    EmbeddingResult,
    InferenceBackend,
    InferenceChunk,
    InferenceRequest,
    InferenceResult,
)

logger = logging.getLogger("mycellm.inference")


def length_buckets(lengths: list[int], batch_size: int) -> list[list[int]]:
    """Index buckets, grouped by ascending length, each at most batch_size."""
    order = sorted(range(len(lengths)), key=lambda i: lengths[i])
    return [order[i:i + batch_size] for i in range(0, len(order), batch_size)]


class MLXEmbeddingsBackend(InferenceBackend):
    """Serves /v1/embeddings from an mlx-embeddings model. Not a chat model."""

    def __init__(self, batch_size: int = 16):
        self._model = None
        self._processor = None
        self._model_name = ""
        self._batch_size = batch_size

    async def load_model(self, model_path: str, **kwargs) -> None:
        try:
            from mlx_embeddings.utils import load as emb_load
        except ImportError as e:
            raise RuntimeError(
                "mlx-embeddings is not installed — pip install mlx-embeddings "
                "(or use a GGUF embedding model on the llama.cpp backend)"
            ) from e
        self._model_name = kwargs.get("name") or Path(model_path).name
        logger.info(f"Loading MLX embeddings model {self._model_name} from {model_path}")
        self._model, self._processor = await asyncio.to_thread(emb_load, model_path)
        logger.info(f"MLX embeddings model {self._model_name} ready")

    async def unload_model(self, model_name: str) -> None:
        self._model = None
        self._processor = None
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:
            pass
        logger.info(f"MLX embeddings model {model_name} unloaded")

    async def generate(self, request: InferenceRequest) -> InferenceResult:
        raise RuntimeError(
            f"'{self._model_name}' is an embedding model — it cannot chat. "
            "Use /v1/embeddings."
        )

    async def generate_stream(
        self, request: InferenceRequest
    ) -> AsyncIterator[InferenceChunk]:
        raise RuntimeError(
            f"'{self._model_name}' is an embedding model — it cannot chat. "
            "Use /v1/embeddings."
        )
        yield  # pragma: no cover — makes this an async generator

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        if self._model is None:
            raise RuntimeError("No embedding model loaded")
        texts = [request.input] if isinstance(request.input, str) else list(request.input)
        if not texts:
            return EmbeddingResult(embeddings=[])
        return await asyncio.to_thread(self._embed_sync, texts)

    def _embed_sync(self, texts: list[str]) -> EmbeddingResult:
        proc, model = self._processor, self._model
        # Token lengths without padding, for bucketing + usage accounting.
        lengths = [len(proc.encode(t, truncation=True)) for t in texts]
        out: list[list[float] | None] = [None] * len(texts)
        for bucket in length_buckets(lengths, self._batch_size):
            enc = proc.batch_encode_plus(
                [texts[i] for i in bucket],
                return_tensors="mlx", padding=True, truncation=True,
            )
            res = model(enc["input_ids"], attention_mask=enc.get("attention_mask"))
            vecs = res.text_embeds.tolist()
            for i, vec in zip(bucket, vecs):
                out[i] = vec
        return EmbeddingResult(
            embeddings=out,  # order restored via index assignment
            total_tokens=sum(lengths),
        )

    def get_loaded_models(self) -> list[str]:
        return [self._model_name] if self._model is not None else []

    def get_capabilities(self) -> dict:
        return {"backend": "mlx-embeddings", "embeddings": True, "chat": False}
