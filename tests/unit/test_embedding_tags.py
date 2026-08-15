"""Embedding-model classification from model names.

The same family list is mirrored by ``EmbeddingModels.families`` in the iOS
node, which has no backend type to fall back on — so the cases here are the
shared contract, not a Python-only detail. A model must not be an embedding
model on one node and a chat model on another.
"""

import pytest

from mycellm.router.model_resolver import (
    derive_capability_tags,
    derive_tags,
    is_embedding_model_name,
)


# Models whose names say what they are — the original heuristic caught these.
SELF_DESCRIBING = [
    "nomic-embed-text-v1.5",
    "snowflake-arctic-embed-m",
    "jina-embeddings-v2-base-en",
    "text-embedding-3-small",
]

# Models that name their architecture instead. Every one ships as GGUF for
# embedding work, and every one used to be tagged "chat".
ARCHITECTURE_NAMED = [
    "all-MiniLM-L6-v2",
    "bge-small-en-v1.5",
    "gte-base",
    "multilingual-e5-large",
    "mxbai-embed-large-v1",
    "all-mpnet-base-v2",
    "sentence-t5-base",
    "paraphrase-multilingual-MiniLM-L12-v2",
]

CHAT_MODELS = [
    "Qwen2.5-7B-Instruct",
    "Llama-3.2-3B-Instruct",
    "qwen3-1.7b",
    "Mistral-7B-Instruct-v0.3",
    "DeepSeek-R1-Distill-Qwen-7B",
    "gemma-3-4b-it",
    "phi-3.5-mini-instruct",
]


@pytest.mark.parametrize("name", SELF_DESCRIBING + ARCHITECTURE_NAMED)
def test_embedding_models_are_recognised(name):
    assert is_embedding_model_name(name)
    assert derive_tags(name) == ["embedding"]


@pytest.mark.parametrize("name", CHAT_MODELS)
def test_chat_models_are_not_embedding_models(name):
    assert not is_embedding_model_name(name)
    assert "embedding" not in derive_tags(name)
    assert "chat" in derive_tags(name)


def test_match_ignores_case_quant_suffix_and_path():
    assert is_embedding_model_name("ALL-MINILM-L6-V2")
    assert is_embedding_model_name("bge-small-en-v1.5-Q4_K_M.gguf")
    assert is_embedding_model_name("/var/models/nomic-embed-text.gguf")
    # A directory named "embeddings" must not classify the model inside it.
    assert not is_embedding_model_name("/embeddings/Qwen2.5-7B.gguf")


def test_embedding_tag_overrides_rather_than_appends():
    # An embedding model is not also a chat model — listing both would let
    # auto-routing pick it for a chat request it cannot serve.
    assert derive_tags("bge-small-en") == ["embedding"]
    assert derive_tags("nomic-embed-text") == ["embedding"]


def test_backend_type_still_wins_over_the_name():
    # mlx-embeddings cannot generate regardless of what the model is called,
    # so the backend override must survive the widened name heuristic.
    assert derive_capability_tags("some-unlabelled-model", "mlx-embeddings") == ["embedding"]
    assert derive_capability_tags("all-MiniLM-L6-v2", "llama.cpp") == ["embedding"]
    assert derive_capability_tags("Qwen2.5-7B-Instruct", "llama.cpp") == ["chat"]


def test_other_tags_are_unaffected():
    assert derive_tags("Qwen2.5-Coder-7B") == ["chat", "code"]
    assert "reasoning" in derive_tags("QwQ-32B-Preview")
    assert "vision" in derive_tags("Qwen2.5-VL-7B-Instruct")
