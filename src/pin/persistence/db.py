"""PostgreSQL engine and per-command session factory."""

from __future__ import annotations

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    """Create the async PostgreSQL engine using the project's Psycopg 3 driver."""
    url = make_url(database_url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    elif url.drivername != "postgresql+psycopg":
        raise ValueError("database_url must use PostgreSQL with Psycopg 3")
    return create_async_engine(url, pool_pre_ping=True, isolation_level="READ COMMITTED")


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create short-lived, independent sessions for application units of work."""
    return async_sessionmaker(engine, expire_on_commit=False)
