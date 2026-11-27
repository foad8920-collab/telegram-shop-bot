"""SubWorld database layer (standalone - no imports from the Yemen bot).

Reads SUBWORLD_DATABASE_URL (Supabase pooler URI) from the environment.
Tables are created with Base.metadata.create_all on first start (v1);
Alembic migrations come later if the schema grows.
"""
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        url = os.environ.get("SUBWORLD_DATABASE_URL", "").strip()
        if not url:
            raise RuntimeError("SUBWORLD_DATABASE_URL is not set")
        _engine = create_async_engine(
            url, pool_size=5, max_overflow=10, pool_pre_ping=True
        )
    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def init_db() -> None:
    """Create SubWorld tables if they do not exist yet."""
    import subworld.models  # noqa: F401  (registers the models on Base)
    import subworld.supplier_models  # noqa: F401  (registers supplier tables)

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
