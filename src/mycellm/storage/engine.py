"""Database engine factory — supports SQLite (default) and PostgreSQL."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from mycellm.storage.models import Base

logger = logging.getLogger("mycellm.storage")

# Module-level engine singleton (set by init_database)
_engine: AsyncEngine | None = None
_session_factory: sessionmaker | None = None


def get_database_url(db_url: str = "", db_path: str = "") -> str:
    """Resolve a database URL.

    Priority:
      1. Explicit db_url (e.g. "postgresql+asyncpg://user:pass@host/db")
      2. MYCELLM_DB_URL env var (handled by settings)
      3. SQLite at db_path (default: ~/.local/share/mycellm/mycellm.db)
    """
    if db_url:
        return db_url
    if db_path:
        return f"sqlite+aiosqlite:///{db_path}"
    return "sqlite+aiosqlite:///mycellm.db"


def create_engine_from_url(url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine from a URL."""
    connect_args = {}

    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        engine = create_async_engine(
            url,
            connect_args=connect_args,
            echo=False,
            pool_pre_ping=True,
        )
    else:
        # PostgreSQL or other async dialects
        engine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )

    return engine


async def init_database(
    db_url: str = "",
    db_path: str = "",
) -> AsyncEngine:
    """Initialize the database engine, create tables, and return the engine.

    This is the main entry point. Call once at startup.
    """
    global _engine, _session_factory

    url = get_database_url(db_url, db_path)
    _engine = create_engine_from_url(url)

    # Create all tables (idempotent)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Enable WAL for SQLite
    if url.startswith("sqlite"):
        async with _engine.begin() as conn:
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")

    _session_factory = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

    backend = "PostgreSQL" if "postgresql" in url else "SQLite"
    logger.info(f"Database initialized ({backend})")
    return _engine


def get_engine() -> AsyncEngine:
    """Get the current database engine. Raises if not initialized."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _engine


def get_session_factory() -> sessionmaker:
    """Get the session factory. Raises if not initialized."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _session_factory


def get_session() -> AsyncSession:
    """Create a new async session."""
    return get_session_factory()()


async def close_database() -> None:
    """Close the database engine and clean up."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
