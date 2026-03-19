"""Tests for credit accounting."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from mycellm.accounting.schema import init_db
from mycellm.accounting.local_ledger import LocalLedger
from mycellm.accounting.pricing import compute_cost, compute_reward


@pytest.fixture
async def ledger(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    l = LocalLedger(db_path)
    await l.ensure_account("peer1", initial_balance=100.0)
    return l


async def test_initial_balance(ledger):
    bal = await ledger.balance("peer1")
    assert bal == 100.0


async def test_credit(ledger):
    tx_id = await ledger.credit("peer1", 10.0, "test_credit")
    assert tx_id
    bal = await ledger.balance("peer1")
    assert bal == 110.0


async def test_debit(ledger):
    await ledger.debit("peer1", 25.0, "test_debit")
    bal = await ledger.balance("peer1")
    assert bal == 75.0


async def test_history(ledger):
    await ledger.credit("peer1", 5.0, "earn1")
    await ledger.debit("peer1", 2.0, "spend1")
    hist = await ledger.history("peer1")
    assert len(hist) == 2
    assert hist[0]["direction"] == "debit"  # Most recent first
    assert hist[1]["direction"] == "credit"


async def test_account_totals(ledger):
    await ledger.credit("peer1", 20.0, "earn")
    await ledger.debit("peer1", 5.0, "spend")
    acct = await ledger.get_account("peer1")
    assert acct["total_earned"] == 20.0
    assert acct["total_spent"] == 5.0
    assert acct["balance"] == 115.0


def test_pricing_cost():
    cost = compute_cost(1000, model_size_b=7.0)
    assert cost == 1.0  # 1000 * 0.001 * 1.0

    cost_70b = compute_cost(1000, model_size_b=70.0)
    assert cost_70b == 10.0  # 1000 * 0.001 * 10.0


def test_pricing_reward_equals_cost():
    cost = compute_cost(500)
    reward = compute_reward(500)
    assert cost == reward
