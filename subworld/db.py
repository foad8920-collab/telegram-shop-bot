"""SubWorld database layer (standalone - no imports from the Yemen bot).

Reads SUBWORLD_DATABASE_URL (Supabase pooler URI) from the environment.
Tables are created with Base.metadata.create_all on first start (v1);
Alembic migrations come later if the schema grows.
"""
import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory = None


def _normalize_url(url: str) -> str:
    """Force the asyncpg driver.

    Supabase gives a plain postgresql:// URL, which SQLAlchemy's async
    engine resolves to psycopg2 - not installed in this service (the
    Yemen bot uses asyncpg). Also drop sslmode, which asyncpg does not
    accept as a connect argument.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme
    if scheme in ("postgresql", "postgres"):
        scheme = "postgresql+asyncpg"
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k.lower() != "sslmode"]
    return urlunparse(parsed._replace(scheme=scheme, query=urlencode(query)))


def get_engine():
    global _engine
    if _engine is None:
        url = os.environ.get("SUBWORLD_DATABASE_URL", "").strip()
        if not url:
            raise RuntimeError("SUBWORLD_DATABASE_URL is not set")
        # statement_cache_size=0: Supabase's Transaction pooler (PgBouncer,
        # port 6543) does not support prepared statements - without this the
        # first query can fail with DuplicatePreparedStatementError when a
        # server connection is reused (seen live 2026-11-27 13:09 UTC).
        _engine = create_async_engine(
            _normalize_url(url),
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            connect_args={"statement_cache_size": 0},
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
