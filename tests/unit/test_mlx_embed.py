"""MLX embeddings backend: length-grouped batching and chat guards.

The bucketing invariant that matters: every input appears exactly once, each
bucket is at most batch_size, and buckets are length-homogeneous (sorted
order), so short texts never pay a long text's padding.
"""

import asyncio

import pytest

from mycellm.inference.base import EmbeddingRequest, InferenceRequest
from mycellm.inference.mlx_embed import MLXEmbeddingsBackend, length_buckets


class TestLengthBuckets:
    def test_partition_is_exact_and_sized(self):
        lengths = [30, 2, 17, 4, 25, 9, 1, 40]
        buckets = length_buckets(lengths, 3)
        flat = [i for b in buckets for i in b]
        assert sorted(flat) == list(range(len(lengths)))
        assert all(len(b) <= 3 for b in buckets)

    def test_buckets_are_length_sorted(self):
        lengths = [30, 2, 17, 4]
        buckets = length_buckets(lengths, 2)
        assert buckets == [[1, 3], [2, 0]]  # (2,4) then (17,30)

    def test_empty(self):
        assert length_buckets([], 8) == []


class _FakeProcessor:
    def encode(self, text, **kw):
        return list(range(len(text.split())))

    def batch_encode_plus(self, texts, **kw):
        return {"input_ids": texts, "attention_mask": None}


class _FakeOut:
    def __init__(self, texts):
        class _E:
            def __init__(self, t):
                self._t = t

            def tolist(self):
                # Deterministic per-text "embedding": word count vector
                return [[float(len(x.split()))] for x in self._t]

        self.text_embeds = _E(texts)


class _FakeModel:
    def __call__(self, input_ids, attention_mask=None):
        return _FakeOut(input_ids)


class TestEmbedBackend:
    def _backend(self, batch_size=2):
        b = MLXEmbeddingsBackend(batch_size=batch_size)
        b._model = _FakeModel()
        b._processor = _FakeProcessor()
        b._model_name = "minilm-test"
        return b

    def test_order_restored_across_buckets(self):
        b = self._backend(batch_size=2)
        texts = ["one two three four", "one", "one two", "one two three"]
        res = asyncio.run(b.embed(EmbeddingRequest(input=texts)))
        assert [v[0] for v in res.embeddings] == [4.0, 1.0, 2.0, 3.0]
        assert res.total_tokens == 10

    def test_single_string_input(self):
        b = self._backend()
        res = asyncio.run(b.embed(EmbeddingRequest(input="just one input")))
        assert len(res.embeddings) == 1

    def test_chat_refused(self):
        b = self._backend()
        with pytest.raises(RuntimeError, match="embedding model"):
            asyncio.run(b.generate(InferenceRequest(messages=[], model="m")))
