"""async SQLAlchemy のエンジンとセッション境界。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

_engine: AsyncEngine | None = None
_engine_url: str | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """設定ごとに 1 つの async engine を遅延生成する。"""

    global _engine, _engine_url, _session_factory

    resolved_settings = settings or get_settings()
    database_url = resolved_settings.database_url
    if _engine is None or _engine_url != database_url:
        _engine = create_async_engine(database_url, pool_pre_ping=True)
        _engine_url = database_url
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    get_engine(settings)
    if _session_factory is None:  # pragma: no cover - get_engine が必ず初期化する
        raise RuntimeError("セッションファクトリを初期化できませんでした")
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI Depends 用。成功時だけコミットする。"""

    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope(settings: Settings | None = None) -> AsyncIterator[AsyncSession]:
    """CLI とジョブ用のトランザクション境界。"""

    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def check_database(settings: Settings | None = None) -> None:
    """接続プールだけでなく SQL 実行まで確認する。"""

    engine = get_engine(settings)
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def dispose_engine() -> None:
    """lifespan 終了時に接続プールを閉じる。"""

    global _engine, _engine_url, _session_factory

    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _engine_url = None
    _session_factory = None
