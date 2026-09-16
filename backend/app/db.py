"""异步数据库引擎与会话
对齐 SQLAlchemy 2.0：PG(asyncpg) / SQLite(aiosqlite) 双方言
- SQLite 本地单体默认：WAL + busy_timeout + 外键开启
- 所有 DateTime 统一 UTC 语义（PG timestamptz / SQLite 文本均存 UTC）
"""
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _record):
    """SQLite 运行时 PRAGMA（仅 sqlite 方言生效，PG 无影响）"""
    if engine.dialect.name != "sqlite":
        return
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=30000")  # 并发拆解写锁等待 30s，避免读被 5s 超时误杀
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类（模型定义集中在 app/models.py）"""


async def get_session():
    """FastAPI 依赖：请求级会话"""
    async with SessionLocal() as session:
        yield session
