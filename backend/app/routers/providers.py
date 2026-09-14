"""AI 服务商 / api-key / 余额 / 充值 管理路由。

前端「模型管理页」与「AI 用量页（服务商卡片 + 详情）」共用。
"""
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.db import get_session
from app.services import ai_provider as svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai-providers"])

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class ProviderIn(BaseModel):
    key: str = Field(..., min_length=2, max_length=32)
    name: str = Field(..., min_length=1, max_length=64)
    base_url: str = Field(..., min_length=6, max_length=256)
    api_key: str = ""  # 留空表示不修改/新建空配置
    models: list[dict] = []
    priority: int = 100
    enabled: bool = True
    brand_color: str | None = None
    logo_url: str | None = None
    topup_url: str | None = None
    balance_warn_threshold: float = 10.0


def _local_day_range_utc() -> tuple:
    now_local = datetime.now(LOCAL_TZ)
    day_start_local = datetime.combine(now_local.date(), time.min, tzinfo=LOCAL_TZ)
    start_utc = day_start_local.astimezone(timezone.utc)
    return start_utc, start_utc + timedelta(days=1)


async def _provider_usage_rows(db: AsyncSession, provider_key: str | None = None):
    conds = [M.AiUsageLog.ok.is_(True)]
    if provider_key:
        conds.append(M.AiUsageLog.provider == provider_key)
    return (
        await db.execute(
            select(
                M.AiUsageLog.provider,
                func.count().label("calls"),
                func.coalesce(func.sum(M.AiUsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(M.AiUsageLog.completion_tokens), 0),
                func.coalesce(func.sum(M.AiUsageLog.total_tokens), 0),
                func.coalesce(func.sum(M.AiUsageLog.cost_cny), 0),
            )
            .where(*conds)
            .group_by(M.AiUsageLog.provider)
        )
    ).all()


@router.get("/providers")
async def list_provider_cards(db: AsyncSession = Depends(get_session)):
    """服务商卡片列表：已配置（含未填 key 的）+ 可快速添加的模板。"""
    providers = await svc.list_providers(db, include_disabled=False)
    day_start, _ = _local_day_range_utc()
    rows = await _provider_usage_rows(db)
    usage_map = {
        r[0]: {
            "calls": int(r[1]),
            "prompt_tokens": int(r[2]),
            "completion_tokens": int(r[3]),
            "total_tokens": int(r[4]),
            "cost_cny": round(float(r[5]), 6),
        }
        for r in rows
    }
    today_map: dict[str, dict] = {}
    trows = (
        await db.execute(
            select(
                M.AiUsageLog.provider,
                func.count(),
                func.coalesce(func.sum(M.AiUsageLog.cost_cny), 0),
            )
            .where(
                M.AiUsageLog.ok.is_(True),
                M.AiUsageLog.created_at >= day_start,
            )
            .group_by(M.AiUsageLog.provider)
        )
    ).all()
    for r in trows:
        today_map[r.provider] = {
            "calls": int(r[1]),
            "cost_cny": round(float(r[2]), 6),
        }

    items = []
    from datetime import timezone as dt_tz

    for p in providers:
        # 带 key 且可接口查余额：余额缺失或超过 30 分钟未检查则顺带刷新（失败静默）
        if (
            p.api_key
            and not p.balance_manual
            and (svc.template(p.key) or {}).get("balance_api")
        ):
            # SQLite 读回的 balance_checked_at 为 naive，须补 UTC 再相减（避免 aware-naive TypeError）
            checked = p.balance_checked_at
            if checked is not None and checked.tzinfo is None:
                checked = checked.replace(tzinfo=dt_tz.utc)
            stale = checked is None or (
                datetime.now(dt_tz.utc) - checked
            ).total_seconds() > 1800
            if stale:
                try:
                    await svc.auto_refresh_balance(db, p)
                except Exception:  # noqa: BLE001
                    pass
        pub = svc.provider_public(p)
        status = svc.balance_status(p)
        pub["usage"] = usage_map.get(p.key, {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "cost_cny": 0.0,
        })
        pub["today"] = today_map.get(p.key, {"calls": 0, "cost_cny": 0.0})
        pub["balance_status"] = status
        if status == "low":
            pub["warn"] = (
                f"余额 ¥{p.balance_cny:.2f} 已低于预警线 ¥{p.balance_warn_threshold:.2f}"
            )
        items.append(pub)

    configured = {p.key for p in providers}
    templates = [
        {
            **svc.provider_public(
                M.AiProvider(
                    key=k, name=t["name"], base_url=t["base_url"], models=t["models"],
                    topup_url=t.get("topup_url"), brand_color=t.get("brand_color"),
                    api_key="", enabled=True, priority=100,
                )
            ),
            "balance_status": "no_key",
            "usage": {"calls": 0, "cost_cny": 0.0},
            "today": {"calls": 0, "cost_cny": 0.0},
        }
        for k, t in svc.PROVIDER_TEMPLATES.items()
        if k not in configured
    ]
    return {"items": items, "templates": templates}


@router.post("/providers")
async def create_provider(
    req: ProviderIn, db: AsyncSession = Depends(get_session)
):
    existing = await svc.get_provider_by_key(db, req.key)
    if existing:
        raise HTTPException(status_code=409, detail="该服务商已存在，请直接编辑")
    p = M.AiProvider(
        key=req.key,
        name=req.name,
        base_url=req.base_url,
        api_key=req.api_key,
        models=req.models or [],
        priority=req.priority,
        enabled=req.enabled,
        brand_color=req.brand_color,
        logo_url=req.logo_url,
        topup_url=req.topup_url,
        balance_warn_threshold=req.balance_warn_threshold,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    # 填了 key 立即尝试一次余额
    if p.api_key and not p.balance_manual:
        await svc.auto_refresh_balance(db, p)
    return svc.provider_public(p)


@router.put("/providers/{key}")
async def update_provider(
    key: str, req: ProviderIn, db: AsyncSession = Depends(get_session)
):
    p = await svc.get_provider_by_key(db, key)
    if p is None:
        raise HTTPException(status_code=404, detail="服务商不存在")
    # 只有 key 相同的可 PUT（key 不允许改）
    if req.key != key:
        raise HTTPException(status_code=422, detail="key 不可修改")
    p.name = req.name
    p.base_url = req.base_url
    if req.api_key:  # 留空表示不改 key
        p.api_key = req.api_key
        p.balance_cny = None
        p.balance_checked_at = None
    if req.models is not None:
        p.models = req.models
    p.priority = req.priority
    p.enabled = req.enabled
    p.brand_color = req.brand_color
    p.logo_url = req.logo_url
    p.topup_url = req.topup_url
    p.balance_warn_threshold = req.balance_warn_threshold
    await db.commit()
    await db.refresh(p)
    if p.api_key and not p.balance_manual:
        await svc.auto_refresh_balance(db, p)
    return svc.provider_public(p)


@router.delete("/providers/{key}")
async def delete_provider(key: str, db: AsyncSession = Depends(get_session)):
    p = await svc.get_provider_by_key(db, key)
    if p is None:
        raise HTTPException(status_code=404, detail="服务商不存在")
    await db.delete(p)
    await db.commit()
    return {"ok": True}


@router.post("/providers/{key}/refresh-balance")
async def refresh_balance(key: str, db: AsyncSession = Depends(get_session)):
    p = await svc.get_provider_by_key(db, key)
    if p is None:
        raise HTTPException(status_code=404, detail="服务商不存在")
    if not p.api_key:
        raise HTTPException(status_code=422, detail="尚未填写 api-key")
    if svc.template(p.key) is None or not (svc.template(p.key) or {}).get("balance_api"):
        # 非模板/不支持接口查询：允许手动余额模式
        return {
            "ok": True,
            "manual": True,
            "balance_cny": p.balance_cny,
            "note": "该服务商不支持接口查询余额，可在编辑页手动维护",
        }
    bal, err = await svc.fetch_balance(p)
    if bal is None:
        raise HTTPException(status_code=502, detail=err or "余额查询失败")
    p.balance_cny = bal
    p.balance_checked_at = datetime.now(timezone.utc)
    p.balance_manual = False
    await db.commit()
    return {
        "ok": True,
        "manual": False,
        "balance_cny": bal,
        "checked_at": p.balance_checked_at.isoformat(),
        "status": svc.balance_status(p),
    }


@router.post("/providers/{key}/balance-manual")
async def set_manual_balance(
    key: str, body: dict, db: AsyncSession = Depends(get_session)
):
    """手动维护余额（支持手动填的服务商用）。"""
    p = await svc.get_provider_by_key(db, key)
    if p is None:
        raise HTTPException(status_code=404, detail="服务商不存在")
    val = body.get("balance_cny")
    try:
        p.balance_cny = None if val is None else float(val)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="余额需为数字") from None
    p.balance_manual = True
    p.balance_checked_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(p)
    return svc.provider_public(p)


@router.get("/providers/{key}/usage")
async def provider_usage_detail(
    key: str, db: AsyncSession = Depends(get_session)
):
    """某服务商用量详情：累计/今日 + 按 alias/模型 + 最近流水。"""
    p = await svc.get_provider_by_key(db, key)
    if p is None:
        raise HTTPException(status_code=404, detail="服务商不存在")
    day_start, _ = _local_day_range_utc()

    async def agg(conds):
        return (
            await db.execute(
                select(
                    func.count().label("calls"),
                    func.coalesce(func.sum(M.AiUsageLog.prompt_tokens), 0).label("prompt"),
                    func.coalesce(func.sum(M.AiUsageLog.completion_tokens), 0).label("completion"),
                    func.coalesce(func.sum(M.AiUsageLog.total_tokens), 0).label("tokens"),
                    func.coalesce(func.sum(M.AiUsageLog.cost_cny), 0).label("cost"),
                ).where(*conds)
            )
        ).one()

    def fmt(row):
        return {
            "calls": int(row[0]),
            "prompt_tokens": int(row[1]),
            "completion_tokens": int(row[2]),
            "total_tokens": int(row[3]),
            "cost_cny": round(float(row[4]), 6),
        }

    base = [M.AiUsageLog.provider == key, M.AiUsageLog.ok.is_(True)]
    total = fmt(await agg(base))
    today = fmt(
        await agg(
            base
            + [
                M.AiUsageLog.created_at >= day_start,
                M.AiUsageLog.created_at < day_start + timedelta(days=1),
            ]
        )
    )

    by_alias_rows = (
        await db.execute(
            select(
                M.AiUsageLog.alias,
                M.AiUsageLog.model,
                func.count(),
                func.coalesce(func.sum(M.AiUsageLog.total_tokens), 0),
                func.coalesce(func.sum(M.AiUsageLog.cost_cny), 0),
            )
            .where(*base)
            .group_by(M.AiUsageLog.alias, M.AiUsageLog.model)
            .order_by(func.count().desc())
        )
    ).all()
    by_model = [
        {
            "alias": a,
            "model": m,
            "calls": int(c),
            "total_tokens": int(t),
            "cost_cny": round(float(cost), 6),
        }
        for a, m, c, t, cost in by_alias_rows
    ]

    recent_rows = (
        (
            await db.execute(
                select(M.AiUsageLog)
                .where(M.AiUsageLog.provider == key)
                .order_by(M.AiUsageLog.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    recent = [
        {
            "id": str(r.id),
            "scene": r.scene,
            "alias": r.alias,
            "model": r.model,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "total_tokens": r.total_tokens,
            "cost_cny": r.cost_cny,
            "ok": r.ok,
            "error": r.error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in recent_rows
    ]
    pub = svc.provider_public(p)
    pub["balance_status"] = svc.balance_status(p)
    return {"provider": pub, "total": total, "today": today, "by_model": by_model, "recent": recent}
