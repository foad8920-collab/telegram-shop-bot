"""SubWorld database layer (standalone - no imports from the Yemen bot).

Reads SUBWORLD_DATABASE_URL (Supabase pooler URI) from the environment.
Tables are created with Base.metadata.create_all on first start (v1);
Alembic migrations come later if the schema grows.
"""
import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool


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
        # PgBouncer fix (live 2026-11-27 13:28-13:29 UTC): the admin list
        # pages failed with DuplicatePreparedStatementError on
        # SELECT count(...) - even with statement_cache_size=0.
        #
        # Why that did not work: SQLAlchemy's asyncpg dialect calls
        # connection.prepare() for EVERY statement, naming it
        # __asyncpg_stmt_<hex counter>__. statement_cache_size=0 only
        # disables asyncpg's own client-side cache - it does not stop the
        # dialect from preparing named statements, and a name left behind
        # on a PgBouncer-reused server session collides with the next
        # client's counter (live names: __asyncpg_stmt_29__,
        # __asyncpg_stmt_2a__).
        #
        # The documented SQLAlchemy recipe for PgBouncer (transaction
        # mode): give every prepared statement a UNIQUE name
        # (prepared_statement_name_func -> UUID, so a name can never
        # collide with a leftover), and do not pool connections
        # client-side (NullPool - PgBouncer does the pooling; each
        # checkout gets a fresh connection and PgBouncer discards its
        # prepared statements on return). statement_cache_size=0 is kept
        # as well. pool_pre_ping stays on so a stale pooled connection
        # is detected before use.
        _engine = create_async_engine(
            _normalize_url(url),
            poolclass=NullPool,
            pool_pre_ping=True,
            connect_args={
                "statement_cache_size": 0,
                "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
            },
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
    import subworld.button_models  # noqa: F401  (registers button/rule tables)
    import subworld.models  # noqa: F401  (registers the models on Base)
    import subworld.supplier_models  # noqa: F401  (registers supplier tables)

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
