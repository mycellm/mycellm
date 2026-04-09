"""Repository classes — data access via SQLAlchemy async sessions."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import desc, or_, select

from mycellm.storage.engine import get_session
from mycellm.storage.models import (
    Account,
    GrowthSnapshot,
    NodeRegistryEntry,
    Receipt,
    Transaction,
)


class LedgerRepository:
    """Credit ledger operations backed by SQLAlchemy.

    Drop-in replacement for LocalLedger — same async method signatures.
    """

    async def ensure_account(self, peer_id: str, initial_balance: float = 0.0) -> None:
        """Create an account if it doesn't exist."""
        now = time.time()
        async with get_session() as session:
            existing = await session.get(Account, peer_id)
            if existing is None:
                session.add(Account(
                    peer_id=peer_id,
                    balance=initial_balance,
                    total_earned=0.0,
                    total_spent=0.0,
                    created_at=now,
                    updated_at=now,
                ))
                await session.commit()

    async def balance(self, peer_id: str) -> float:
        async with get_session() as session:
            account = await session.get(Account, peer_id)
            return account.balance if account else 0.0

    async def credit(
        self,
        peer_id: str,
        amount: float,
        reason: str,
        counterparty_id: str = "",
        receipt_signature: str = "",
    ) -> str:
        """Add credits to an account. Returns transaction ID."""
        tx_id = uuid.uuid4().hex[:16]
        now = time.time()

        async with get_session() as session:
            account = await session.get(Account, peer_id)
            if account:
                account.balance += amount
                account.total_earned += amount
                account.updated_at = now

            session.add(Transaction(
                id=tx_id,
                peer_id=peer_id,
                counterparty_id=counterparty_id,
                amount=amount,
                direction="credit",
                reason=reason,
                receipt_signature=receipt_signature,
                timestamp=now,
            ))
            await session.commit()
        return tx_id

    async def debit(
        self,
        peer_id: str,
        amount: float,
        reason: str,
        counterparty_id: str = "",
        receipt_signature: str = "",
    ) -> str:
        """Deduct credits. Raises ValueError if balance would go negative."""
        tx_id = uuid.uuid4().hex[:16]
        now = time.time()

        async with get_session() as session:
            account = await session.get(Account, peer_id)
            if account and account.balance < amount:
                raise ValueError(
                    f"Insufficient credits: balance={account.balance:.4f}, cost={amount:.4f}"
                )

            if account:
                account.balance -= amount
                account.total_spent += amount
                account.updated_at = now

            session.add(Transaction(
                id=tx_id,
                peer_id=peer_id,
                counterparty_id=counterparty_id,
                amount=amount,
                direction="debit",
                reason=reason,
                receipt_signature=receipt_signature,
                timestamp=now,
            ))
            await session.commit()
        return tx_id

    async def history(self, peer_id: str, limit: int = 50) -> list[dict]:
        async with get_session() as session:
            result = await session.execute(
                select(Transaction)
                .where(Transaction.peer_id == peer_id)
                .order_by(desc(Transaction.timestamp))
                .limit(limit)
            )
            return [
                {
                    "id": t.id, "peer_id": t.peer_id,
                    "counterparty_id": t.counterparty_id, "amount": t.amount,
                    "direction": t.direction, "reason": t.reason,
                    "receipt_signature": t.receipt_signature, "timestamp": t.timestamp,
                }
                for t in result.scalars().all()
            ]

    async def get_account(self, peer_id: str) -> dict | None:
        async with get_session() as session:
            account = await session.get(Account, peer_id)
            if account is None:
                return None
            return {
                "peer_id": account.peer_id,
                "balance": account.balance,
                "total_earned": account.total_earned,
                "total_spent": account.total_spent,
                "created_at": account.created_at,
                "updated_at": account.updated_at,
            }

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
        async with get_session() as session:
            # Upsert
            existing = await session.get(Receipt, tx_id)
            if existing:
                existing.consumer_id = consumer_id
                existing.seeder_id = seeder_id
                existing.model = model
                existing.tokens = tokens
                existing.cost = cost
                existing.signature = signature
                existing.timestamp = now
            else:
                session.add(Receipt(
                    tx_id=tx_id,
                    consumer_id=consumer_id,
                    seeder_id=seeder_id,
                    model=model,
                    tokens=tokens,
                    cost=cost,
                    signature=signature,
                    timestamp=now,
                ))
            await session.commit()

    async def get_receipts(self, peer_id: str, limit: int = 50) -> list[dict]:
        """Get receipts involving a peer (as consumer or seeder)."""
        async with get_session() as session:
            result = await session.execute(
                select(Receipt)
                .where(or_(Receipt.consumer_id == peer_id, Receipt.seeder_id == peer_id))
                .order_by(desc(Receipt.timestamp))
                .limit(limit)
            )
            return [
                {
                    "tx_id": r.tx_id, "consumer_id": r.consumer_id,
                    "seeder_id": r.seeder_id, "model": r.model,
                    "tokens": r.tokens, "cost": r.cost,
                    "signature": r.signature, "timestamp": r.timestamp,
                }
                for r in result.scalars().all()
            ]


class NodeRegistryRepository:
    """Node registry operations backed by SQLAlchemy.

    Provides a dict-like interface for compatibility with existing code
    that accesses node.node_registry as a dict.
    """

    async def get(self, peer_id: str) -> dict | None:
        async with get_session() as session:
            entry = await session.get(NodeRegistryEntry, peer_id)
            return entry.to_dict() if entry else None

    async def upsert(self, peer_id: str, data: dict) -> None:
        """Insert or update a node registry entry."""
        async with get_session() as session:
            entry = await session.get(NodeRegistryEntry, peer_id)
            if entry:
                for key in ("node_name", "api_addr", "role", "capabilities",
                            "system", "status", "last_seen", "ip", "online"):
                    if key in data:
                        setattr(entry, key, data[key])
            else:
                session.add(NodeRegistryEntry(
                    peer_id=peer_id,
                    node_name=data.get("node_name", ""),
                    api_addr=data.get("api_addr", ""),
                    role=data.get("role", "seeder"),
                    capabilities=data.get("capabilities", {}),
                    system=data.get("system", {}),
                    status=data.get("status", "pending"),
                    last_seen=data.get("last_seen", time.time()),
                    first_seen=data.get("first_seen", time.time()),
                    ip=data.get("ip", ""),
                    online=data.get("online", False),
                ))
            await session.commit()

    async def remove(self, peer_id: str) -> bool:
        async with get_session() as session:
            entry = await session.get(NodeRegistryEntry, peer_id)
            if entry is None:
                return False
            await session.delete(entry)
            await session.commit()
            return True

    async def list_all(self) -> list[dict]:
        """List all registered nodes."""
        async with get_session() as session:
            result = await session.execute(select(NodeRegistryEntry))
            entries = result.scalars().all()
            now = time.time()
            nodes = []
            for e in entries:
                d = e.to_dict()
                d["online"] = (now - e.last_seen) < 120
                nodes.append(d)
            return nodes

    async def values(self) -> list[dict]:
        """Compatibility: return all entries as dicts (like dict.values())."""
        return await self.list_all()

    async def count_by_status(self, status: str) -> int:
        async with get_session() as session:
            result = await session.execute(
                select(NodeRegistryEntry).where(NodeRegistryEntry.status == status)
            )
            return len(result.scalars().all())

    async def load_as_dict(self) -> dict[str, dict]:
        """Load all entries into an in-memory dict (for migration/compatibility)."""
        entries = await self.list_all()
        return {e["peer_id"]: e for e in entries}

    async def import_from_dict(self, registry: dict[str, dict]) -> int:
        """Import entries from a dict (for migration from JSON)."""
        count = 0
        for peer_id, data in registry.items():
            await self.upsert(peer_id, data)
            count += 1
        return count


class GrowthRepository:
    """Network growth snapshot operations."""

    async def record(
        self,
        total_nodes: int,
        online_nodes: int,
        total_models: int,
        total_requests: int,
        total_tokens: int,
        total_tps: float,
        total_vram_gb: float,
    ) -> None:
        """Record a growth snapshot."""
        now = time.time()
        async with get_session() as session:
            session.add(GrowthSnapshot(
                timestamp=now,
                total_nodes=total_nodes,
                online_nodes=online_nodes,
                total_models=total_models,
                total_requests=total_requests,
                total_tokens=total_tokens,
                total_tps=total_tps,
                total_vram_gb=total_vram_gb,
            ))
            await session.commit()

    async def get_deltas(self) -> dict:
        """Get 24h and 7d deltas from current snapshot."""
        now = time.time()
        async with get_session() as session:
            # Get latest snapshot
            result = await session.execute(
                select(GrowthSnapshot).order_by(desc(GrowthSnapshot.timestamp)).limit(1)
            )
            latest = result.scalar_one_or_none()
            if not latest:
                return {}

            deltas = {}
            for label, seconds in [("24h", 86400), ("7d", 604800)]:
                result = await session.execute(
                    select(GrowthSnapshot)
                    .where(GrowthSnapshot.timestamp <= now - seconds)
                    .order_by(desc(GrowthSnapshot.timestamp))
                    .limit(1)
                )
                old = result.scalar_one_or_none()
                if old:
                    deltas[label] = {
                        "nodes": latest.total_nodes - old.total_nodes,
                        "requests": latest.total_requests - old.total_requests,
                    }
            return deltas

    async def get_history(self, limit: int = 168) -> list[dict]:
        """Get recent growth history (default: 7 days at hourly = 168)."""
        async with get_session() as session:
            result = await session.execute(
                select(GrowthSnapshot)
                .order_by(desc(GrowthSnapshot.timestamp))
                .limit(limit)
            )
            snapshots = result.scalars().all()
            return [
                {
                    "ts": s.timestamp,
                    "nodes": s.total_nodes,
                    "online": s.online_nodes,
                    "models": s.total_models,
                }
                for s in reversed(snapshots)
            ]
