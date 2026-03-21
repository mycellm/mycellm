"""Tests for receipt verification, replay protection, and credit rate limiting."""

import pytest
import time

from mycellm.identity.keys import generate_account_key, generate_device_key
from mycellm.identity.certs import create_device_cert
from mycellm.accounting.receipts import (
    build_receipt_data,
    sign_receipt,
    verify_receipt_signature,
    ReceiptValidator,
)


@pytest.fixture
def seeder_keys():
    account = generate_account_key()
    device = generate_device_key()
    cert = create_device_cert(account, device, device_name="seeder")
    return account, device, cert


@pytest.fixture
def consumer_keys():
    account = generate_account_key()
    device = generate_device_key()
    cert = create_device_cert(account, device, device_name="consumer")
    return account, device, cert


# --- Receipt signing + verification ---

def test_sign_and_verify_receipt(seeder_keys):
    _, device, cert = seeder_keys
    receipt_data = build_receipt_data(
        consumer_id="consumer123",
        seeder_id="seeder456",
        model="llama-7b",
        tokens=100,
        cost=0.1,
        request_id="req-001",
    )
    sig = sign_receipt(device, receipt_data)
    assert verify_receipt_signature(receipt_data, sig, cert.device_pubkey)


def test_verify_rejects_wrong_key(seeder_keys, consumer_keys):
    _, seeder_device, _ = seeder_keys
    _, _, consumer_cert = consumer_keys

    receipt_data = build_receipt_data(
        consumer_id="c", seeder_id="s", model="m", tokens=1, cost=0.001,
    )
    sig = sign_receipt(seeder_device, receipt_data)
    # Verify with consumer's key should fail
    assert not verify_receipt_signature(receipt_data, sig, consumer_cert.device_pubkey)


def test_verify_rejects_tampered_data(seeder_keys):
    _, device, cert = seeder_keys
    receipt_data = build_receipt_data(
        consumer_id="c", seeder_id="s", model="m", tokens=100, cost=0.1,
    )
    sig = sign_receipt(device, receipt_data)

    # Tamper with data (different token count)
    tampered = build_receipt_data(
        consumer_id="c", seeder_id="s", model="m", tokens=999, cost=0.1,
    )
    assert not verify_receipt_signature(tampered, sig, cert.device_pubkey)


def test_verify_rejects_invalid_signature(seeder_keys):
    _, _, cert = seeder_keys
    receipt_data = build_receipt_data(
        consumer_id="c", seeder_id="s", model="m", tokens=1, cost=0.001,
    )
    assert not verify_receipt_signature(receipt_data, "deadbeef" * 16, cert.device_pubkey)


def test_receipt_includes_request_id():
    """Request ID is bound into the signed data."""
    data1 = build_receipt_data(consumer_id="c", seeder_id="s", model="m", tokens=1, cost=0.001, request_id="req-001")
    data2 = build_receipt_data(consumer_id="c", seeder_id="s", model="m", tokens=1, cost=0.001, request_id="req-002")
    assert data1 != data2  # different request_ids produce different data


# --- Replay protection ---

def test_replay_protection_allows_new():
    v = ReceiptValidator()
    assert v.check_replay("req-001") is True


def test_replay_protection_blocks_duplicate():
    v = ReceiptValidator()
    v.check_replay("req-001")
    assert v.check_replay("req-001") is False


def test_replay_protection_different_ids():
    v = ReceiptValidator()
    v.check_replay("req-001")
    assert v.check_replay("req-002") is True


def test_replay_protection_empty_id_allowed():
    """Empty request_id = legacy receipt, always allowed."""
    v = ReceiptValidator()
    assert v.check_replay("") is True
    assert v.check_replay("") is True


# --- Credit rate limiting ---

def test_credit_rate_allows_normal():
    v = ReceiptValidator(max_receipts_per_minute=5)
    for _ in range(5):
        assert v.check_credit_rate("peer1") is True


def test_credit_rate_blocks_flood():
    v = ReceiptValidator(max_receipts_per_minute=3)
    for _ in range(3):
        v.check_credit_rate("peer1")
    assert v.check_credit_rate("peer1") is False


def test_credit_rate_per_peer():
    v = ReceiptValidator(max_receipts_per_minute=2)
    v.check_credit_rate("peer1")
    v.check_credit_rate("peer1")
    assert v.check_credit_rate("peer1") is False
    assert v.check_credit_rate("peer2") is True


# --- Priority routing by balance ---

def test_balance_filter_low_balance():
    """Low balance restricts to tiny + fast models."""
    from mycellm.router.model_resolver import ModelResolver, ResolvedModel
    from mycellm.router.registry import PeerRegistry

    resolver = ModelResolver(PeerRegistry())
    candidates = [
        ResolvedModel(model_name="tiny-1b", peer_id="", source="local", tier="tiny", score=10),
        ResolvedModel(model_name="fast-7b", peer_id="", source="local", tier="fast", score=30),
        ResolvedModel(model_name="capable-13b", peer_id="", source="local", tier="capable", score=60),
        ResolvedModel(model_name="frontier-70b", peer_id="", source="local", tier="frontier", score=100),
    ]
    filtered = resolver._apply_balance_filter(candidates, balance=5.0)
    names = [c.model_name for c in filtered]
    assert "tiny-1b" in names
    assert "fast-7b" in names
    assert "capable-13b" not in names
    assert "frontier-70b" not in names


def test_balance_filter_medium_balance():
    """Medium balance allows up to capable."""
    from mycellm.router.model_resolver import ModelResolver, ResolvedModel
    from mycellm.router.registry import PeerRegistry

    resolver = ModelResolver(PeerRegistry())
    candidates = [
        ResolvedModel(model_name="fast-7b", peer_id="", source="local", tier="fast", score=30),
        ResolvedModel(model_name="capable-13b", peer_id="", source="local", tier="capable", score=60),
        ResolvedModel(model_name="frontier-70b", peer_id="", source="local", tier="frontier", score=100),
    ]
    filtered = resolver._apply_balance_filter(candidates, balance=25.0)
    names = [c.model_name for c in filtered]
    assert "capable-13b" in names
    assert "frontier-70b" not in names


def test_balance_filter_high_balance():
    """High balance allows all tiers."""
    from mycellm.router.model_resolver import ModelResolver, ResolvedModel
    from mycellm.router.registry import PeerRegistry

    resolver = ModelResolver(PeerRegistry())
    candidates = [
        ResolvedModel(model_name="frontier-70b", peer_id="", source="local", tier="frontier", score=100),
    ]
    filtered = resolver._apply_balance_filter(candidates, balance=100.0)
    assert len(filtered) == 1
    assert filtered[0].model_name == "frontier-70b"


def test_balance_filter_never_blocks_completely():
    """Even with 0 balance, at least some models are returned."""
    from mycellm.router.model_resolver import ModelResolver, ResolvedModel
    from mycellm.router.registry import PeerRegistry

    resolver = ModelResolver(PeerRegistry())
    candidates = [
        ResolvedModel(model_name="frontier-70b", peer_id="", source="local", tier="frontier", score=100),
    ]
    # Only frontier available, balance is 0 — should still return it (fallback)
    filtered = resolver._apply_balance_filter(candidates, balance=0.0)
    assert len(filtered) == 1


# --- E2E receipt round-trip ---

def test_e2e_receipt_round_trip(seeder_keys):
    """Full receipt lifecycle: build, sign, verify, replay-check."""
    _, device, cert = seeder_keys
    validator = ReceiptValidator()

    # Build receipt
    request_id = "req-e2e-001"
    receipt_data = build_receipt_data(
        consumer_id="consumer-abc",
        seeder_id="seeder-xyz",
        model="llama-8b",
        tokens=500,
        cost=0.5,
        request_id=request_id,
        timestamp=time.time(),
    )

    # Sign
    sig = sign_receipt(device, receipt_data)

    # Verify
    assert verify_receipt_signature(receipt_data, sig, cert.device_pubkey)

    # Replay check
    assert validator.check_replay(request_id) is True
    assert validator.check_replay(request_id) is False  # second time = replay

    # Rate limit
    assert validator.check_credit_rate("seeder-xyz") is True
