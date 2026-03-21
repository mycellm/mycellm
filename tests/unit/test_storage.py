"""Tests for the SQLAlchemy storage abstraction layer.

Runs every DB-dependent test against both SQLite and PostgreSQL 17.
Set MYCELLM_TEST_PG_URL to enable PostgreSQL tests, e.g.:
  MYCELLM_TEST_PG_URL="postgresql+asyncpg://yapp:pass@localhost/mycellm_test" pytest
"""

import os
import pytest
import time

from mycellm.storage.engine import (
    create_engine_from_url,
    get_database_url,
    init_database,
    close_database,
    get_session,
)
from mycellm.storage.models import Base, Account, Transaction, Receipt, GrowthSnapshot, NodeRegistryEntry
from mycellm.storage.repositories import LedgerRepository, NodeRegistryRepository, GrowthRepository

PG_URL = os.environ.get("MYCELLM_TEST_PG_URL", "")

_db_params = ["sqlite"]
if PG_URL:
    _db_params.append("postgres")


@pytest.fixture(params=_db_params)
async def db(request, tmp_path):
    """Initialize a fresh database for each test (SQLite or PostgreSQL)."""
    if request.param == "postgres":
        engine = await init_database(db_url=PG_URL)
        yield engine
        # Drop all tables after each test so the next run starts clean
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await close_database()
    else:
        db_path = str(tmp_path / "test.db")
        engine = await init_database(db_path=db_path)
        yield engine
        await close_database()


# --- Engine tests ---

def test_get_database_url_explicit():
    url = get_database_url(db_url="postgresql+asyncpg://user:pass@host/db")
    assert url == "postgresql+asyncpg://user:pass@host/db"


def test_get_database_url_from_path():
    url = get_database_url(db_path="/tmp/test.db")
    assert url == "sqlite+aiosqlite:////tmp/test.db"


def test_get_database_url_fallback():
    url = get_database_url()
    assert url == "sqlite+aiosqlite:///mycellm.db"


def test_get_database_url_explicit_wins():
    """Explicit db_url takes precedence over db_path."""
    url = get_database_url(db_url="postgresql+asyncpg://x", db_path="/tmp/y.db")
    assert url == "postgresql+asyncpg://x"


async def test_init_creates_tables(db):
    """init_database creates all ORM tables."""
    from sqlalchemy import inspect

    async with db.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "accounts" in tables
    assert "transactions" in tables
    assert "receipts" in tables
    assert "growth_snapshots" in tables
    assert "node_registry" in tables


async def test_init_idempotent(db, tmp_path):
    """Calling init_database twice doesn't fail."""
    db_path = str(tmp_path / "test.db")
    engine2 = await init_database(db_path=db_path)
    # Should not raise
    assert engine2 is not None
    await close_database()


# --- LedgerRepository tests ---

async def test_ledger_ensure_account(db):
    ledger = LedgerRepository()
    await ledger.ensure_account("peer1", 100.0)
    bal = await ledger.balance("peer1")
    assert bal == 100.0


async def test_ledger_ensure_account_idempotent(db):
    ledger = LedgerRepository()
    await ledger.ensure_account("peer1", 100.0)
    await ledger.ensure_account("peer1", 200.0)  # should not overwrite
    bal = await ledger.balance("peer1")
    assert bal == 100.0


async def test_ledger_credit(db):
    ledger = LedgerRepository()
    await ledger.ensure_account("peer1", 100.0)
    tx_id = await ledger.credit("peer1", 10.0, "test_credit")
    assert tx_id
    bal = await ledger.balance("peer1")
    assert bal == 110.0


async def test_ledger_debit(db):
    ledger = LedgerRepository()
    await ledger.ensure_account("peer1", 100.0)
    await ledger.debit("peer1", 25.0, "test_debit")
    bal = await ledger.balance("peer1")
    assert bal == 75.0


async def test_ledger_debit_insufficient(db):
    ledger = LedgerRepository()
    await ledger.ensure_account("peer1", 10.0)
    with pytest.raises(ValueError, match="Insufficient credits"):
        await ledger.debit("peer1", 20.0, "too_much")


async def test_ledger_history(db):
    ledger = LedgerRepository()
    await ledger.ensure_account("peer1", 100.0)
    await ledger.credit("peer1", 5.0, "earn1")
    await ledger.debit("peer1", 2.0, "spend1")
    hist = await ledger.history("peer1")
    assert len(hist) == 2
    assert hist[0]["direction"] == "debit"  # most recent first
    assert hist[1]["direction"] == "credit"


async def test_ledger_account_totals(db):
    ledger = LedgerRepository()
    await ledger.ensure_account("peer1", 100.0)
    await ledger.credit("peer1", 20.0, "earn")
    await ledger.debit("peer1", 5.0, "spend")
    acct = await ledger.get_account("peer1")
    assert acct["total_earned"] == 20.0
    assert acct["total_spent"] == 5.0
    assert acct["balance"] == 115.0


async def test_ledger_store_receipt(db):
    ledger = LedgerRepository()
    await ledger.store_receipt("tx1", "consumer1", "seeder1", "llama-7b", 100, 0.1, "sig123")
    receipts = await ledger.get_receipts("consumer1")
    assert len(receipts) == 1
    assert receipts[0]["model"] == "llama-7b"
    assert receipts[0]["tokens"] == 100


async def test_ledger_receipt_as_seeder(db):
    ledger = LedgerRepository()
    await ledger.store_receipt("tx2", "consumer1", "seeder1", "llama-7b", 50, 0.05, "sig456")
    receipts = await ledger.get_receipts("seeder1")
    assert len(receipts) == 1


async def test_ledger_balance_nonexistent(db):
    ledger = LedgerRepository()
    bal = await ledger.balance("nobody")
    assert bal == 0.0


async def test_ledger_get_account_nonexistent(db):
    ledger = LedgerRepository()
    acct = await ledger.get_account("nobody")
    assert acct is None


# --- NodeRegistryRepository tests ---

async def test_registry_upsert_and_get(db):
    repo = NodeRegistryRepository()
    await repo.upsert("peer1", {
        "node_name": "test-node",
        "api_addr": "10.0.0.1:8420",
        "role": "seeder",
        "capabilities": {"hardware": {"gpu": "RTX 4090"}},
        "status": "approved",
    })
    entry = await repo.get("peer1")
    assert entry is not None
    assert entry["node_name"] == "test-node"
    assert entry["status"] == "approved"
    assert entry["capabilities"]["hardware"]["gpu"] == "RTX 4090"


async def test_registry_upsert_updates(db):
    repo = NodeRegistryRepository()
    await repo.upsert("peer1", {"node_name": "v1", "status": "pending"})
    await repo.upsert("peer1", {"node_name": "v2", "status": "approved"})
    entry = await repo.get("peer1")
    assert entry["node_name"] == "v2"
    assert entry["status"] == "approved"


async def test_registry_remove(db):
    repo = NodeRegistryRepository()
    await repo.upsert("peer1", {"node_name": "gone"})
    assert await repo.remove("peer1") is True
    assert await repo.get("peer1") is None


async def test_registry_remove_nonexistent(db):
    repo = NodeRegistryRepository()
    assert await repo.remove("nobody") is False


async def test_registry_list_all(db):
    repo = NodeRegistryRepository()
    await repo.upsert("p1", {"node_name": "node1", "last_seen": time.time()})
    await repo.upsert("p2", {"node_name": "node2", "last_seen": time.time() - 200})
    nodes = await repo.list_all()
    assert len(nodes) == 2
    online = [n for n in nodes if n["online"]]
    offline = [n for n in nodes if not n["online"]]
    assert len(online) == 1
    assert len(offline) == 1


async def test_registry_load_as_dict(db):
    repo = NodeRegistryRepository()
    await repo.upsert("p1", {"node_name": "node1"})
    await repo.upsert("p2", {"node_name": "node2"})
    d = await repo.load_as_dict()
    assert isinstance(d, dict)
    assert "p1" in d
    assert "p2" in d


async def test_registry_import_from_dict(db):
    """Import from a JSON-like dict (migration scenario)."""
    repo = NodeRegistryRepository()
    data = {
        "peer1": {"node_name": "migrated1", "status": "approved", "last_seen": time.time()},
        "peer2": {"node_name": "migrated2", "status": "pending", "last_seen": time.time()},
    }
    count = await repo.import_from_dict(data)
    assert count == 2
    entry = await repo.get("peer1")
    assert entry["node_name"] == "migrated1"


async def test_registry_json_capabilities(db):
    """JSON fields store and retrieve correctly."""
    repo = NodeRegistryRepository()
    caps = {
        "hardware": {"gpu": "RTX 4090", "vram_gb": 24, "backend": "cuda"},
        "models": [{"name": "llama-7b"}, {"name": "mistral-7b"}],
    }
    sys_info = {"memory": {"total_gb": 64}, "cpu": {"cores": 16}}
    await repo.upsert("p1", {"capabilities": caps, "system": sys_info})
    entry = await repo.get("p1")
    assert entry["capabilities"]["hardware"]["gpu"] == "RTX 4090"
    assert len(entry["capabilities"]["models"]) == 2
    assert entry["system"]["memory"]["total_gb"] == 64


# --- GrowthRepository tests ---

async def test_growth_record(db):
    repo = GrowthRepository()
    await repo.record(
        total_nodes=5, online_nodes=3, total_models=2,
        total_requests=100, total_tokens=5000,
        total_tps=12.5, total_vram_gb=48.0,
    )
    history = await repo.get_history()
    assert len(history) == 1
    assert history[0]["nodes"] == 5
    assert history[0]["online"] == 3


async def test_growth_history_order(db):
    """History is returned in chronological order (oldest first)."""
    import asyncio
    repo = GrowthRepository()
    await repo.record(total_nodes=1, online_nodes=1, total_models=0,
                      total_requests=0, total_tokens=0, total_tps=0, total_vram_gb=0)
    await asyncio.sleep(0.01)
    await repo.record(total_nodes=2, online_nodes=2, total_models=1,
                      total_requests=10, total_tokens=100, total_tps=5, total_vram_gb=24)
    history = await repo.get_history()
    assert len(history) == 2
    assert history[0]["nodes"] == 1  # oldest first
    assert history[1]["nodes"] == 2  # newest last


async def test_growth_deltas_empty(db):
    """Deltas return empty when no historical data."""
    repo = GrowthRepository()
    deltas = await repo.get_deltas()
    assert deltas == {}


async def test_growth_history_limit(db):
    """History respects limit parameter."""
    repo = GrowthRepository()
    for i in range(10):
        await repo.record(total_nodes=i, online_nodes=i, total_models=0,
                          total_requests=0, total_tokens=0, total_tps=0, total_vram_gb=0)
    history = await repo.get_history(limit=5)
    assert len(history) == 5


# --- Model tests ---

def test_node_registry_entry_to_dict():
    entry = NodeRegistryEntry(
        peer_id="abc123",
        node_name="test",
        api_addr="10.0.0.1:8420",
        role="seeder",
        capabilities={"gpu": "RTX 4090"},
        system={"memory": {"total_gb": 64}},
        status="approved",
        last_seen=1000.0,
        first_seen=900.0,
        ip="10.0.0.1",
        online=True,
    )
    d = entry.to_dict()
    assert d["peer_id"] == "abc123"
    assert d["status"] == "approved"
    assert d["capabilities"]["gpu"] == "RTX 4090"
