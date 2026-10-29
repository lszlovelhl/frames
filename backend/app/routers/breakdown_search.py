"""拆解结果混合检索路由：GET /api/breakdown/search

检索 ``breakdown_chunk``（拆解产物分块索引）：向量余弦 0.7 + BM25 0.3，
本地 nomic-embed-text 隐私不出本机；本地不可用时由服务层降级纯关键词。
响应结构对齐 ``search_chunks``，供下一阶段 chat citations 直接复用。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import breakdown_index as bidx

router = APIRouter()


@router.get("/api/breakdown/search")
async def breakdown_search(
    q: str | None = None,
    top_k: int = Query(default=bidx.DEFAULT_TOP_K, ge=1, le=50),
    kind: str | None = None,
    db: AsyncSession = Depends(get_session),
):
    """自然语言检索已拆解视频的概览/段落/钩子/高价值句/完播驱动/可复用积木。

    - q: 自然语言查询，如「开头怎么留人」「干货密度高的选题」
    - top_k: 返回条数（1~50，默认 5）
    - kind: 可选过滤类型：script_summary / segment / hook / sentence_function /
      completion_driver / reusable_element；不传=全类型
    - 空 query 或无结果返回 ``{"items": [], "mode": ...}``，不报错
    """
    kinds = [kind] if kind else None
    result = await bidx.search_chunks(db, q or "", top_k=top_k, kinds=kinds)
    return result
