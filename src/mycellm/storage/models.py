"""SQLAlchemy ORM models for mycellm persistent storage."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


class Account(Base):
    """Credit account for a peer node."""

    __tablename__ = "accounts"

    peer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    balance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_earned: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_spent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)


class Transaction(Base):
    """Credit transaction record."""

    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    peer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    counterparty_id: Mapped[str] = mapped_column(String(64), nullable=True, default="")
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # 'credit' or 'debit'
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_signature: Mapped[str] = mapped_column(Text, nullable=True, default="")
    timestamp: Mapped[float] = mapped_column(Float, nullable=False, index=True)


class Receipt(Base):
    """Signed credit receipt for verified transactions."""

    __tablename__ = "receipts"

    tx_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    consumer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    seeder_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost: Mapped[float] = mapped_column(Float, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)


class GrowthSnapshot(Base):
    """Hourly network growth snapshot."""

    __tablename__ = "growth_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    total_nodes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    online_nodes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_models: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tps: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_vram_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class NodeRegistryEntry(Base):
    """Registered node in the bootstrap/admin registry."""

    __tablename__ = "node_registry"

    peer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    api_addr: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="seeder")
    capabilities: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    system: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    last_seen: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    first_seen: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("idx_node_registry_status", "status"),
    )

    def to_dict(self) -> dict:
        """Convert to the dict format used by existing code."""
        return {
            "peer_id": self.peer_id,
            "node_name": self.node_name,
            "api_addr": self.api_addr,
            "role": self.role,
            "capabilities": self.capabilities or {},
            "system": self.system or {},
            "status": self.status,
            "last_seen": self.last_seen,
            "first_seen": self.first_seen,
            "ip": self.ip,
            "online": self.online,
        }
