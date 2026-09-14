"""提示词模板公共读取（拆解层 / 创作链路共用）"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M


async def load_active_prompt(db: AsyncSession, code: str) -> M.PromptTemplate | None:
    """取该 code 最新 active 模板。"""
    stmt = (
        select(M.PromptTemplate)
        .where(M.PromptTemplate.code == code, M.PromptTemplate.status == "active")
        .order_by(M.PromptTemplate.version.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
