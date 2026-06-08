"""SQLite schema for local credit accounting."""

from __future__ import annotations

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    peer_id TEXT NOT NULL,
    network_id TEXT NOT NULL DEFAULT '',
    balance REAL NOT NULL DEFAULT 0.0,
    total_earned REAL NOT NULL DEFAULT 0.0,
    total_spent REAL NOT NULL DEFAULT 0.0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (peer_id, network_id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    peer_id TEXT NOT NULL,
    counterparty_id TEXT,
    amount REAL NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('credit', 'debit')),
    reason TEXT NOT NULL,
    receipt_signature TEXT,
    network_id TEXT DEFAULT '',
    timestamp REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transactions_peer ON transactions(peer_id);
CREATE INDEX IF NOT EXISTS idx_transactions_ts ON transactions(timestamp);
CREATE INDEX IF NOT EXISTS idx_transactions_network ON transactions(network_id);

CREATE TABLE IF NOT EXISTS receipts (
    tx_id TEXT PRIMARY KEY,
    consumer_id TEXT NOT NULL,
    seeder_id TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    cost REAL NOT NULL,
    signature TEXT NOT NULL,
    timestamp REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_receipts_consumer ON receipts(consumer_id);
CREATE INDEX IF NOT EXISTS idx_receipts_seeder ON receipts(seeder_id);

CREATE TABLE IF NOT EXISTS growth_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    total_nodes INTEGER NOT NULL DEFAULT 0,
    online_nodes INTEGER NOT NULL DEFAULT 0,
    total_models INTEGER NOT NULL DEFAULT 0,
    total_requests INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    total_tps REAL NOT NULL DEFAULT 0.0,
    total_vram_gb REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_growth_ts ON growth_snapshots(timestamp);
"""


async def init_db(db_path: str) -> None:
    """Initialize the accounting database."""
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        await _migrate_accounts_per_net(db)
        await db.executescript(SCHEMA_SQL)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.commit()


async def _migrate_accounts_per_net(db) -> None:
    """Migrate a pre-per-net accounts table (peer_id PRIMARY KEY) to the
    composite (peer_id, network_id) key. Existing balances land in the
    default network ('') so nothing is lost. No-op on fresh DBs or
    already-migrated ones."""
    cursor = await db.execute("PRAGMA table_info(accounts)")
    cols = [row[1] for row in await cursor.fetchall()]
    if not cols or "network_id" in cols:
        return  # fresh DB (SCHEMA_SQL will create it) or already migrated
    await db.executescript("""
        ALTER TABLE accounts RENAME TO accounts_legacy;
        CREATE TABLE accounts (
            peer_id TEXT NOT NULL,
            network_id TEXT NOT NULL DEFAULT '',
            balance REAL NOT NULL DEFAULT 0.0,
            total_earned REAL NOT NULL DEFAULT 0.0,
            total_spent REAL NOT NULL DEFAULT 0.0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (peer_id, network_id)
        );
        INSERT INTO accounts (peer_id, network_id, balance, total_earned, total_spent, created_at, updated_at)
            SELECT peer_id, '', balance, total_earned, total_spent, created_at, updated_at FROM accounts_legacy;
        DROP TABLE accounts_legacy;
    """)
    await db.commit()
