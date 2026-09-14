"""三层分库 L1~L6 提示词落库（《设计方案》第 8 章）。

把 ``prompts.TEMPLATES`` 中的六层提示词写入 ``prompt_templates``：
同一 code 内容变化时**新增版本**并把旧版本置 ``archived``（不删除任何历史行），
内容未变则跳过。旧五层链路（layer1_topline ~ layer5_element_extract）的模板
不属于本模块管辖范围，保持原样不动。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M

from .prompts import LAYER_CODES, TEMPLATES

logger = logging.getLogger(__name__)


async def seed_three_layer_prompts(db: AsyncSession) -> list[dict[str, Any]]:
    """幂等写入六层提示词，返回每个 code 的动作与版本。"""
    report: list[dict[str, Any]] = []
    for layer in sorted(LAYER_CODES):
        code = LAYER_CODES[layer]
        seed = TEMPLATES[code]
        rows = (
            await db.execute(
                select(M.PromptTemplate)
                .where(M.PromptTemplate.code == code)
                .order_by(M.PromptTemplate.version)
            )
        ).scalars().all()
        active = [r for r in rows if r.status == "active"]
        if active and active[-1].content == seed["content"]:
            report.append({"code": code, "layer": layer, "action": "unchanged", "version": active[-1].version})
            continue
        archived = 0
        for row in active:
            row.status = "archived"
            archived += 1
        version = max([int(r.version or 0) for r in rows], default=0) + 1
        db.add(
            M.PromptTemplate(
                code=code,
                name=str(seed["name"])[:128],
                layer=layer,
                content=str(seed["content"]),
                version=version,
                status="active",
                role_scope=[],
                platform_scope=[],
            )
        )
        logger.info("三层提示词 %s → v%s（归档旧版本 %s 个）", code, version, archived)
        report.append(
            {
                "code": code,
                "layer": layer,
                "action": "created" if not rows else "updated",
                "version": version,
                "archived_versions": archived,
            }
        )
    await db.flush()
    return report
