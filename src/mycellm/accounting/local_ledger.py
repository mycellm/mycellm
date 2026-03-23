"""Local credit ledger — not a distributed ledger."""

from __future__ import annotations

import time
import uuid

import aiosqlite


class LocalLedger:
    """Local credit accounting with signed receipts."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def ensure_account(self, peer_id: str, initial_balance: float = 0.0) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            now = time.time()
            await db.execute(
                """INSERT OR IGNORE INTO accounts (peer_id, balance, total_earned, total_spent, created_at, updated_at)
                   VALUES (?, ?, 0.0, 0.0, ?, ?)""",
                (peer_id, initial_balance, now, now),
            )
            await db.commit()

    async def balance(self, peer_id: str) -> float:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT balance FROM accounts WHERE peer_id = ?", (peer_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0.0

    async def credit(
        self,
        peer_id: str,
        amount: float,
        reason: str,
        counterparty_id: str = "",
        receipt_signature: str = "",
        network_id: str = "",
    ) -> str:
        """Add credits to an account. Returns transaction ID.

        Args:
            network_id: Optional network identifier for per-network credit isolation.
                         When set, the transaction is tagged with this network so credits
                         can be tracked and queried per-network.
        """
        tx_id = uuid.uuid4().hex[:16]
        now = time.time()

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE accounts SET balance = balance + ?, total_earned = total_earned + ?, updated_at = ? WHERE peer_id = ?",
                (amount, amount, now, peer_id),
            )
            await db.execute(
                "INSERT INTO transactions (id, peer_id, counterparty_id, amount, direction, reason, receipt_signature, network_id, timestamp) VALUES (?, ?, ?, ?, 'credit', ?, ?, ?, ?)",
                (tx_id, peer_id, counterparty_id, amount, reason, receipt_signature, network_id, now),
            )
            await db.commit()
        return tx_id

    async def debit(
        self,
        peer_id: str,
        amount: float,
        reason: str,
        counterparty_id: str = "",
        receipt_signature: str = "",
        network_id: str = "",
    ) -> str:
        """Deduct credits from an account. Returns transaction ID.

        Args:
            network_id: Optional network identifier for per-network credit isolation.
                         When set, the transaction is tagged with this network so credits
                         can be tracked and queried per-network.

        Raises ValueError if balance would go negative.
        """
        tx_id = uuid.uuid4().hex[:16]
        now = time.time()

        async with aiosqlite.connect(self._db_path) as db:
            # Check balance first
            cursor = await db.execute(
                "SELECT balance FROM accounts WHERE peer_id = ?", (peer_id,)
            )
            row = await cursor.fetchone()
            if row and row[0] < amount:
                raise ValueError(f"Insufficient credits: balance={row[0]:.4f}, cost={amount:.4f}")

            await db.execute(
                "UPDATE accounts SET balance = balance - ?, total_spent = total_spent + ?, updated_at = ? WHERE peer_id = ?",
                (amount, amount, now, peer_id),
            )
            await db.execute(
                "INSERT INTO transactions (id, peer_id, counterparty_id, amount, direction, reason, receipt_signature, network_id, timestamp) VALUES (?, ?, ?, ?, 'debit', ?, ?, ?, ?)",
                (tx_id, peer_id, counterparty_id, amount, reason, receipt_signature, network_id, now),
            )
            await db.commit()
        return tx_id

    async def history(self, peer_id: str, limit: int = 50) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM transactions WHERE peer_id = ? ORDER BY timestamp DESC LIMIT ?",
                (peer_id, limit),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_account(self, peer_id: str) -> dict | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM accounts WHERE peer_id = ?", (peer_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def store_receipt(
        self,
        tx_id: str,
        consumer_id: str,
        seeder_id: str,
        model: str,
        tokens: int,
        cost: float,
        signature: str,
    ) -> None:
        """Store a verified credit receipt."""
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO receipts
                   (tx_id, consumer_id, seeder_id, model, tokens, cost, signature, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (tx_id, consumer_id, seeder_id, model, tokens, cost, signature, now),
            )
            await db.commit()

    async def get_receipts(self, peer_id: str, limit: int = 50) -> list[dict]:
        """Get receipts involving a peer (as consumer or seeder)."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM receipts
                   WHERE consumer_id = ? OR seeder_id = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (peer_id, peer_id, limit),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
