"""自动采集配置与热榜管理"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, text as sql_text

from app.db import get_session
from app.models import DailyHotlist, CollectionConfig
from app.services.hotlist import fetch_bilibili_hotlist, fetch_douyin_hotlist, save_hotlist_to_db
from app.services.hotlist_ai import analyze_hotlist
from app.services import qr_login

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/collection", tags=["采集配置"])


# ---------- Pydantic 模型 ----------

class CollectionConfigUpdate(BaseModel):
    enabled: bool | None = None
    hotlist_enabled: bool | None = None
    keyword_enabled: bool | None = None
    keywords: list[str] | None = None
    platforms: list[str] | None = None
    max_videos_per_run: int | None = Field(None, ge=1, le=50)
    min_likes: int | None = Field(None, ge=0)
    interval_minutes: int | None = Field(None, ge=10, le=1440)


# ---------- 接口 ----------

@router.get("/config")
async def get_config(db: AsyncSession = Depends(get_session)):
    """获取采集配置"""
    config = (await db.execute(select(CollectionConfig).limit(1))).scalar_one_or_none()
    if not config:
        # 默认配置
        config = CollectionConfig(
            enabled=False,
            hotlist_enabled=True,
            keyword_enabled=False,
            keywords=[],
            platforms=["bilibili"],
            max_videos_per_run=10,
            min_likes=10000,
            interval_minutes=60,
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)

    return {
        "enabled": config.enabled,
        "hotlist_enabled": config.hotlist_enabled,
        "keyword_enabled": config.keyword_enabled,
        "keywords": config.keywords,
        "platforms": config.platforms,
        "max_videos_per_run": config.max_videos_per_run,
        "min_likes": config.min_likes,
        "interval_minutes": config.interval_minutes,
        "last_run_at": config.last_run_at.isoformat() if config.last_run_at else None,
    }


@router.put("/config")
async def update_config(req: CollectionConfigUpdate, db: AsyncSession = Depends(get_session)):
    """更新采集配置"""
    config = (await db.execute(select(CollectionConfig).limit(1))).scalar_one_or_none()
    if not config:
        config = CollectionConfig()
        db.add(config)

    # 关键词校验
    if req.keywords is not None:
        # 长度限制 + 去重
        cleaned = []
        seen = set()
        for kw in req.keywords:
            kw = kw.strip()
            if 1 <= len(kw) <= 20 and kw not in seen:
                cleaned.append(kw)
                seen.add(kw)
        req.keywords = cleaned

    for field, value in req.dict(exclude_unset=True).items():
        setattr(config, field, value)

    await db.commit()
    await db.refresh(config)

    return {"ok": True, "message": "配置已更新"}


@router.get("/hotlist/dates")
async def get_hotlist_dates(db: AsyncSession = Depends(get_session)):
    """获取有热榜记录的日期列表"""
    from sqlalchemy import distinct
    dates = (await db.execute(select(distinct(DailyHotlist.date)).order_by(desc(DailyHotlist.date)))).scalars().all()
    return {"dates": dates}


@router.get("/hotlist/{date}")
async def get_hotlist_by_date(
    date: str,
    platform: str | None = None,
    db: AsyncSession = Depends(get_session),
):
    """获取指定日期的热榜"""
    query = select(DailyHotlist).where(DailyHotlist.date == date).order_by(DailyHotlist.rank)
    if platform:
        query = query.where(DailyHotlist.platform == platform)
    items = (await db.execute(query)).scalars().all()

    return {
        "date": date,
        "items": [
            {
                "id": str(item.id),
                "platform": item.platform,
                "hotlist_type": item.hotlist_type,
                "title": item.title,
                "hot_value": item.hot_value,
                "rank": item.rank,
                "author": item.author,
                "summary": item.summary,
                "ai_summary": item.ai_summary,
            }
            for item in items
        ],
    }


@router.post("/hotlist/refresh")
async def refresh_hotlist(db: AsyncSession = Depends(get_session)):
    """手动刷新热榜（B站+抖音）"""
    all_items = []

    # B站热榜
    bili_items = await fetch_bilibili_hotlist(limit=30)
    all_items.extend(bili_items)

    # 抖音热榜
    douyin_items = await fetch_douyin_hotlist(limit=30)
    all_items.extend(douyin_items)

    if not all_items:
        raise HTTPException(status_code=500, detail="热榜获取失败，请检查登录态")

    saved = await save_hotlist_to_db(all_items, db)

    # AI分析热榜
    ai_summary = await analyze_hotlist(all_items)
    if ai_summary:
        # 更新今天的热榜记录，把AI分析存进去
        from datetime import date
        today = date.today().isoformat()
        await db.execute(
            sql_text("UPDATE daily_hotlist SET ai_summary = :summary WHERE date = :date"),
            {"summary": ai_summary, "date": today}
        )
        await db.commit()

    return {
        "ok": True,
        "message": f"热榜获取成功，新增 {saved} 条",
        "bilibili_count": len(bili_items),
        "douyin_count": len(douyin_items),
        "ai_summary": ai_summary,
    }


# ---------- 手动关键词搜索 ----------

@router.post("/search")
async def manual_search(payload: dict):
    """手动触发关键词搜索，立即搜B站+抖音，把视频加入拆解队列。"""
    keyword = payload.get("keyword", "").strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="请输入关键词")
    
    limit = min(int(payload.get("limit", 10)), 30)
    
    from app.services.hotlist import search_bilibili_videos, search_douyin_videos
    from app.services.collection_scheduler import add_video_to_queue
    
    added = {"bilibili": 0, "douyin": 0}
    
    # 搜B站
    bili_results = await search_bilibili_videos(keyword, limit=limit)
    logger.info("B站搜索返回 %d 条结果", len(bili_results))
    for item in bili_results:
        try:
            await add_video_to_queue(item["video_url"], "bilibili")
            added["bilibili"] += 1
        except Exception as e:
            logger.warning("加入队列失败 %s: %s", item["video_url"], e)
    
    # 搜抖音
    douyin_results = await search_douyin_videos(keyword, limit=limit)
    for item in douyin_results:
        try:
            await add_video_to_queue(item["video_url"], "douyin")
            added["douyin"] += 1
        except Exception:
            pass
    
    return {
        "ok": True,
        "keyword": keyword,
        "added": added,
        "total": added["bilibili"] + added["douyin"],
        "message": f"搜索完成！B站新增 {added['bilibili']} 个，抖音新增 {added['douyin']} 个",
    }


# ---------- 扫码登录 ----------

@router.post("/qr-login/start/{platform}")
async def start_qr_login(platform: str):
    """启动扫码登录流程，返回二维码图片。"""
    try:
        result = await qr_login.start_qr_login(platform)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动扫码登录失败: {str(e)}")


@router.get("/qr-login/status/{session_id}")
async def get_qr_login_status(session_id: str):
    """查询扫码登录状态。"""
    return qr_login.get_qr_login_status(session_id)


@router.post("/qr-login/submit-code/{session_id}")
async def submit_qr_verify_code(session_id: str, payload: dict):
    """用户输入手机验证码，自动填入页面提交。"""
    code = payload.get("code", "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="请输入验证码")
    return await qr_login.submit_verify_code(session_id, code)
