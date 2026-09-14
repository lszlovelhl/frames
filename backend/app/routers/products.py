"""产品库路由：创作标的物（参数 + 卖点 + 内容赛道对齐）查询与手动扩充。"""

import json
import logging

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import String, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.ai import chat
from app.core.categories import (
    INDUSTRY_TAXONOMY,
    normalize_category,
    normalize_industry,
)
from app.db import get_session
from app.services import billing

logger = logging.getLogger(__name__)
router = APIRouter()


class SpecItem(BaseModel):
    k: str
    v: str


class SellingPoint(BaseModel):
    title: str
    detail: str = ""


class ProductCreateReq(BaseModel):
    industry: str = Field(min_length=1)
    category_tags: list[str] = []
    brand: str = Field(min_length=1)
    name: str = Field(min_length=1)
    series: str | None = None
    headline: str = Field(min_length=1)
    price_range: str | None = None
    specs: list[SpecItem] = []
    selling_points: list[SellingPoint] = []


class AutofillReq(BaseModel):
    """AI 补全入参：只要求行业 + 品牌 + 产品名，其余可空。"""

    industry: str = Field(min_length=1)
    brand: str = Field(min_length=1)
    name: str = Field(min_length=1)
    series: str | None = None
    price_range: str | None = None


# 行业 → 默认内容赛道兜底（AI 失败 / 返回空时保证一键可用）
_DEFAULT_TAGS_BY_INDUSTRY: dict[str, list[str]] = {
    "汽车": ["汽车出行", "好物测评"],
    "数码3C": ["科技数码", "好物测评"],
    "美妆个护": ["美妆", "好物测评"],
    "服饰穿搭": ["时尚穿搭", "好物测评"],
    "食品饮料": ["美食", "好物测评"],
    "家用电器": ["好物测评", "生活记录"],
    "家居家装": ["好物测评", "生活记录"],
    "母婴亲子": ["亲子育儿", "好物测评"],
    "运动户外": ["运动健身", "好物测评"],
    "宠物生活": ["萌宠", "好物测评"],
    "游戏文娱": ["游戏", "好物测评"],
    "健康医疗": ["知识口播", "好物测评"],
    "金融保险": ["财经职场", "好物测评"],
    "教育学习": ["知识口播", "好物测评"],
    "旅游": ["文旅非遗", "好物测评"],
}

_AUTOFILL_SYSTEM = (
    "你是懂短视频种草与主流消费品的产品运营。用户给出一个产品的品牌与名称，"
    "请为它补齐一份可直接用于短视频创作的产品档案。只输出 JSON，不要任何解释。"
    '字段：{"series": "系列/定位一句话（可为空字符串）", '
    '"headline": "一句话种草点（20字内，口语化、有钩子，可直接当短视频选题文案）", '
    '"price_range": "价格带字符串（如 21.59 万起 / 约 1999 元）", '
    '"category_tags": ["该产品适合的 1-3 个内容赛道"], '
    '"specs": [{"k": "参数名", "v": "参数值"}], '
    '"selling_points": [{"title": "卖点标题（短）", "detail": "一句话展开"}], }'
    "要求：category_tags 只能从候选赛道里选：好物测评、汽车出行、科技数码、美妆、时尚穿搭、"
    "美食、运动健身、生活记录、知识口播、游戏、财经职场、文旅非遗、亲子育儿、综艺娱乐、"
    "萌宠、音乐舞蹈、情感、剧情短剧、影视解说、搞笑。"
    "specs 给 3-6 条真实可查的核心参数，selling_points 给 2-4 条从用户视角出发的种草卖点。"
    "产品必须真实存在且参数准确，不确定的字段宁缺毋滥；若产品不存在或信息不足，"
    '输出 {"headline": "", "category_tags": [], "specs": [], "selling_points": []}。'
)


def _clean_tags(tags: list) -> list[str]:
    seen: list[str] = []
    for t in tags:
        norm = normalize_category(str(t))
        if norm and norm not in seen:
            seen.append(norm)
    return seen


async def _autofill_draft(req: AutofillReq) -> tuple[bool, dict]:
    """调 flash 补全；失败时返回 (False, 可用空草稿)，不阻塞保存。"""
    industry = normalize_industry(req.industry) or req.industry
    probe = f"行业：{industry}\n品牌：{req.brand.strip()}\n产品名：{req.name.strip()}"
    if req.series:
        probe += f"\n系列/定位：{req.series.strip()}"
    if req.price_range:
        probe += f"\n参考价格：{req.price_range.strip()}"
    fallback_tags = _DEFAULT_TAGS_BY_INDUSTRY.get(industry, ["好物测评"])
    empty = {
        "series": req.series or "",
        "headline": "",
        "price_range": req.price_range or "",
        "category_tags": fallback_tags,
        "specs": [],
        "selling_points": [],
    }
    try:
        resp = await chat(
            messages=[
                {"role": "system", "content": _AUTOFILL_SYSTEM},
                {"role": "user", "content": probe},
            ],
            model="flash",
            temperature=0.3,
            max_tokens=2048,
            json_mode=True,
            timeout=60,
            scene="product_autofill",
        )
        raw = (resp.get("reply") or "").strip()
        data = _parse_autofill(raw)
        if data is None:
            logger.warning("autofill 解析失败 brand=%s name=%s raw=%r", req.brand, req.name, raw[:200])
            return False, empty
        tags = _clean_tags(data.get("category_tags", [])) or fallback_tags
        return True, {
            "series": str(data.get("series") or req.series or "").strip(),
            "headline": str(data.get("headline") or "").strip(),
            "price_range": str(data.get("price_range") or req.price_range or "").strip(),
            "category_tags": tags,
            "specs": [
                {"k": str(s.get("k", "")).strip(), "v": str(s.get("v", "")).strip()}
                for s in (data.get("specs") or [])
                if isinstance(s, dict) and s.get("k") and s.get("v")
            ][:8],
            "selling_points": [
                {
                    "title": str(s.get("title", "")).strip(),
                    "detail": str(s.get("detail") or "").strip(),
                }
                for s in (data.get("selling_points") or [])
                if isinstance(s, dict) and s.get("title")
            ][:6],
        }
    except Exception:  # noqa: BLE001
        logger.warning("autofill 调用失败 brand=%s name=%s", req.brand, req.name, exc_info=True)
        return False, empty


def _parse_autofill(raw: str) -> dict | None:
    """容错解析 autofill JSON 输出。"""
    if not raw:
        return None
    text = raw.strip().strip("`")
    if text.startswith("json"):
        text = text[4:].strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        # JSON 被截断时，尽量抽一个合法 JSON 对象
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, dict) else None
            except Exception:  # noqa: BLE001
                pass
        return None


def _to_dict(p: M.Product) -> dict:
    return {
        "id": str(p.id),
        "industry": p.industry,
        "category_tags": p.category_tags or [],
        "brand": p.brand,
        "name": p.name,
        "series": p.series,
        "headline": p.headline,
        "price_range": p.price_range,
        "specs": p.specs or [],
        "selling_points": p.selling_points or [],
        "source": p.source,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("/api/products")
async def list_products(
    industry: str | None = None,
    category: str | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_session),
):
    """产品列表：支持行业 / 内容赛道 / 关键词过滤；keyword 匹配品牌、名称、卖点标题。"""
    stmt = select(M.Product).where(M.Product.status == "active")
    if industry and industry != "all":
        stmt = stmt.where(M.Product.industry == industry)
    if category and category != "all":
        stmt = stmt.where(M.Product.category_tags.contains([category]))
    if q:
        like = f"%{q.strip()}%"
        # JSONB 转文本便于关键字命中卖点标题/参数值（PG: jsonb::text）
        stmt = stmt.where(
            or_(
                M.Product.brand.ilike(like),
                M.Product.name.ilike(like),
                M.Product.series.ilike(like),
                M.Product.headline.ilike(like),
                M.Product.selling_points.cast(String).ilike(like),
                M.Product.specs.cast(String).ilike(like),
            )
        )
    stmt = stmt.order_by(M.Product.industry, M.Product.brand, M.Product.name)
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": [_to_dict(p) for p in rows], "industries": INDUSTRY_TAXONOMY}


@router.get("/api/products/{product_id}")
async def product_detail(product_id: UUID, db: AsyncSession = Depends(get_session)):
    p = await db.get(M.Product, product_id)
    if p is None or p.status != "active":
        raise HTTPException(status_code=404, detail="产品不存在")
    return _to_dict(p)


@router.post("/api/products")
async def create_product(req: ProductCreateReq, db: AsyncSession = Depends(get_session)):
    """手动补充产品：行业/赛道做归一校验，避免脏标签。"""
    industry = normalize_industry(req.industry)
    if not industry:
        raise HTTPException(status_code=400, detail=f"行业无法识别: {req.industry}（可选：{'/'.join(INDUSTRY_TAXONOMY)}）")
    tags = []
    for t in req.category_tags:
        norm = normalize_category(t)
        if not norm:
            raise HTTPException(status_code=400, detail=f"赛道无法识别: {t}")
        if norm not in tags:
            tags.append(norm)
    if not tags:
        raise HTTPException(status_code=400, detail="至少需要 1 个适用赛道（对齐内容赛道，如 好物测评/汽车/数码）")
    p = M.Product(
        industry=industry,
        category_tags=tags,
        brand=req.brand.strip(),
        name=req.name.strip(),
        series=req.series.strip() if req.series else None,
        headline=req.headline.strip(),
        price_range=req.price_range.strip() if req.price_range else None,
        specs=[s.model_dump() for s in req.specs],
        selling_points=[s.model_dump() for s in req.selling_points],
        status="active",
        source="manual",
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return _to_dict(p)


@router.post("/api/products/autofill")
async def autofill_product(req: AutofillReq, db: AsyncSession = Depends(get_session)):
    """AI 一键补全草稿：根据品牌/名称生成系列、种草点、赛道、参数与卖点建议（按对话动作扣点，成功才扣）。"""
    acc, points = await billing.precheck(db, "chat")
    ok, draft = await _autofill_draft(req)
    if ok:
        await billing.consume(
            db, account=acc, action="chat", points=points,
            ref_type=None, ref_id=None,
            note="产品库 AI 一键补全",
        )
        await db.commit()
    return draft
