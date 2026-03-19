"""SQLite schema for local credit accounting."""

from __future__ import annotations

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    peer_id TEXT PRIMARY KEY,
    balance REAL NOT NULL DEFAULT 0.0,
    total_earned REAL NOT NULL DEFAULT 0.0,
    total_spent REAL NOT NULL DEFAULT 0.0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    peer_id TEXT NOT NULL,
    counterparty_id TEXT,
    amount REAL NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('credit', 'debit')),
    reason TEXT NOT NULL,
    receipt_signature TEXT,
    timestamp REAL NOT NULL,
    FOREIGN KEY (peer_id) REFERENCES accounts(peer_id)
);

CREATE INDEX IF NOT EXISTS idx_transactions_peer ON transactions(peer_id);
CREATE INDEX IF NOT EXISTS idx_transactions_ts ON transactions(timestamp);
"""


async def init_db(db_path: str) -> None:
    """Initialize the accounting database."""
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
