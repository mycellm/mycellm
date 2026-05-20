"""Tests for streaming inference types and parameters."""

from mycellm.inference.base import InferenceRequest


def test_inference_request_stop():
    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="test",
        stop=["\n", "END"],
    )
    assert req.stop == ["\n", "END"]


def test_inference_request_penalties():
    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="test",
        frequency_penalty=0.5,
        presence_penalty=0.3,
    )
    assert req.frequency_penalty == 0.5
    assert req.presence_penalty == 0.3


def test_inference_request_seed():
    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="test",
        seed=42,
    )
    assert req.seed == 42


def test_inference_request_response_format():
    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="test",
        response_format={"type": "json_object"},
    )
    assert req.response_format == {"type": "json_object"}


def test_inference_request_defaults():
    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="test",
    )
    assert req.stop is None
    assert req.frequency_penalty == 0
    assert req.presence_penalty == 0
    assert req.seed is None
    assert req.response_format is None
