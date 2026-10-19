"""
app/database.py  –  async SQLAlchemy engine + session factory
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# ── Engine ───────────────────────────────────────────────────────────────────
# Opening a connection is expensive here: the Postgres login (SCRAM) is computed
# on the event loop, so every new connection stalls all requests for a moment.
# Keep a pool big enough for the busiest moment open (connections above
# pool_size are closed again on return) and open it at startup: open_pool().
POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,           # set True to log all SQL (dev only)
    pool_pre_ping=True,   # detect stale connections
    pool_size=POOL_SIZE,
    max_overflow=20,
)


async def open_pool() -> None:
    """Open all POOL_SIZE connections now rather than under the first rush."""
    async def one():
        async with engine.connect():
            await asyncio.sleep(0.5)  # hold it so the others open new ones
    await asyncio.gather(*(one() for _ in range(POOL_SIZE)))

# ── Session factory ──────────────────────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# ── Declarative base shared by all ORM models ────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── FastAPI dependency ───────────────────────────────────────────────────────
async def get_db() -> AsyncSession:
    """Yield an async DB session; roll back on error, close always."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
