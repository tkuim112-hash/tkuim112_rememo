"""
資料庫連線管理。
建立 async engine + session factory,給其他模組共用。
"""
import os
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from sqlalchemy.orm import DeclarativeBase


def _build_async_database_url() -> str:
    """
    讀 DATABASE_URL,轉成 asyncpg 版。
    docker-compose 給的是 postgresql://...
    async 需要 postgresql+asyncpg://...
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL 沒設定,請檢查 docker-compose.yml")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


class Base(DeclarativeBase):
    """所有 ORM model 的基底。"""
    pass


# 全域 engine 與 session factory
engine = create_async_engine(
    _build_async_database_url(),
    echo=False,         # 想看 SQL log 可改 True
    pool_pre_ping=True, # 自動偵測斷線
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)