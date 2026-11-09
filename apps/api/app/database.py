import os
from collections.abc import AsyncGenerator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


pool_options = (
    {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout_seconds,
        "pool_recycle": settings.db_pool_recycle_seconds,
    }
    if settings.database_url.startswith("postgresql")
    else {}
)
# hide_parameters: SQL errors (and their log lines) never include bound values such as
# password hashes, e-mail addresses or user-entered text.
engine = create_async_engine(
    settings.database_url, pool_pre_ping=True, hide_parameters=True, **pool_options
)
if os.getenv("ALC_DB_PERF_DIAGNOSTICS") == "1":
    from app.performance_diagnostics import install

    install(engine)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def release_read_only_transaction(session: AsyncSession) -> None:
    """Finish a read-only request transaction after its response values are materialized.

    FastAPI reuses the same ``get_db`` dependency for authentication and the route. The
    authenticated-user SELECT therefore starts the shared transaction before the handler.
    This boundary is only valid after the handler has completed all reads and built its
    payload. Refuse to commit if any ORM changes are pending; read-only endpoints must never
    use this helper to commit business writes. ``expire_on_commit=False`` keeps already-loaded
    scalar attributes safe for response serialization, while no unloaded relationship may be
    accessed after this point.
    """
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("Read-only transaction boundary found pending ORM changes")
    if session.in_nested_transaction():
        raise RuntimeError("Read-only transaction boundary cannot close a nested transaction")
    if session.in_transaction():
        await session.commit()
