"""异步数据库引擎与会话
对齐 SQLAlchemy 2.0 + asyncpg
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类（模型定义集中在 app/models.py）"""


async def get_session():
    """FastAPI 依赖：请求级会话"""
    async with SessionLocal() as session:
        yield session
