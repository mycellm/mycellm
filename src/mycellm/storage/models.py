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


class NetworkAccount(Base):
    """Per-network credit account held by a tracker (the source-of-truth node
    for a network — the public prime for the public net, a homelab bootstrap
    for a private one).

    Distinct from Account (a node's own local view): keyed by
    (peer_id, network_id) so one peer has an independent, authoritative balance
    per network. A new table rather than altering Account's primary key, so it
    is created cleanly by create_all with no migration of existing data.
    """

    __tablename__ = "network_accounts"

    peer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    network_id: Mapped[str] = mapped_column(String(64), primary_key=True, default="")
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


class QueuedJobRow(Base):
    """A job waiting for a device that can run it.

    ⚠️ THIS TABLE EXISTS BECAUSE INTERMITTENCY IS THE NORMAL STATE OF A
    PERSONAL FLEET, NOT AN ERROR IN IT. Phones sleep, laptops close, iPads
    charge overnight. Until 0.8 every one of those was a failed request; a
    queue makes them a scheduling input instead, which is the one execution
    model a pay-per-token marketplace cannot offer.

    Persisted rather than held in memory because the whole promise is that the
    work survives longer than the process — a job submitted from a laptop that
    then closes must still be there when the Mac Studio wakes up.
    """

    __tablename__ = "queued_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Who submitted it. Empty means this node itself (a local caller).
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: '' = let the scheduler resolve, else a named model or strategy.
    model: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    messages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: Resolution floor, applied only while `model` is empty — same rule the
    #: chat surfaces enforce, for the same reason.
    min_tier: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    trust: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=2048)
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fanout: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    #: Credits STAKED, not spent — refunded when a job expires or is cancelled
    #: unrun, so bidding for position is never a lottery ticket.
    stake: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Why this job is not running yet, in the user's words. A queue that
    #: cannot explain itself is indistinguishable from a hang.
    waiting_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    started_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    finished_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Absolute deadline. 0 = never expires.
    expires_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    result_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    served_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    served_model: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("idx_queued_jobs_state", "state"),
        Index("idx_queued_jobs_owner", "owner_id"),
        Index("idx_queued_jobs_created", "created_at"),
    )

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "owner_id": self.owner_id,
            "model": self.model,
            "min_tier": self.min_tier,
            "trust": self.trust,
            "state": self.state,
            "stake": self.stake,
            "waiting_reason": self.waiting_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "expires_at": self.expires_at,
            "attempts": self.attempts,
            "result_text": self.result_text,
            "error": self.error,
            "served_by": self.served_by,
            "served_model": self.served_model,
            "token_budget": self.token_budget,
            "fanout": self.fanout,
            "meta": self.meta or {},
        }
