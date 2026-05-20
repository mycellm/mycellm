"""Tests for credit balance enforcement."""

import pytest
from mycellm.accounting.local_ledger import LocalLedger
from mycellm.accounting.schema import init_db


@pytest.fixture
async def ledger(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    ledger_instance = LocalLedger(db_path)
    await ledger_instance.ensure_account("peer1", initial_balance=10.0)
    return ledger_instance


async def test_debit_within_balance(ledger):
    tx = await ledger.debit("peer1", 5.0, "test")
    assert tx
    balance = await ledger.balance("peer1")
    assert balance == 5.0


async def test_debit_exceeds_balance(ledger):
    with pytest.raises(ValueError, match="Insufficient credits"):
        await ledger.debit("peer1", 15.0, "test")
    # Balance unchanged
    assert await ledger.balance("peer1") == 10.0


async def test_debit_exact_balance(ledger):
    tx = await ledger.debit("peer1", 10.0, "test")
    assert tx
    assert await ledger.balance("peer1") == 0.0


async def test_credit_then_debit(ledger):
    await ledger.credit("peer1", 5.0, "earned")
    await ledger.debit("peer1", 12.0, "spent")
    assert await ledger.balance("peer1") == 3.0


async def test_multiple_debits_drain(ledger):
    await ledger.debit("peer1", 3.0, "a")
    await ledger.debit("peer1", 3.0, "b")
    await ledger.debit("peer1", 3.0, "c")
    with pytest.raises(ValueError):
        await ledger.debit("peer1", 3.0, "d")  # only 1.0 left
