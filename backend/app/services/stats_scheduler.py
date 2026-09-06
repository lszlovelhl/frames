"""互动数据后台定时刷新：定期对已建立基准的 B 站视频真实重抓，形成时间序列。

- 每 REFRESH_INTERVAL 秒巡检一次
- 仅处理 platform=bilibili 且 stats_baseline_at 非空、且距上次抓取超过 MIN_GAP 秒的视频
- 抓取失败仅记录日志，不中断循环
"""
import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app import models as M
from app.services.stats_refresh import refresh_video_public

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = 6 * 60 * 60  # 巡检周期 6 小时
MIN_GAP = timedelta(hours=1)  # 距上次抓取至少 1 小时才重抓


async def _refresh_loop() -> None:
    while True:
        try:
            await _refresh_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("互动数据定时刷新巡检异常: %s", exc)
        await asyncio.sleep(REFRESH_INTERVAL)


async def _refresh_once() -> None:
    async with SessionLocal() as db:
        now = datetime.now(UTC)
        res = await db.execute(
            select(M.Video).where(
                M.Video.platform == "bilibili",
                M.Video.stats_baseline_at.is_not(None),
                M.Video.stats_updated_at.is_not(None),
            )
        )
        refreshed = 0
        for video in res.scalars():
            u = video.stats_updated_at
            if u.tzinfo is None:
                u = u.replace(tzinfo=UTC)
            if now - u < MIN_GAP:
                continue
            try:
                await refresh_video_public(video, db)
                refreshed += 1
                logger.info("定时刷新互动数据完成 %s (%s)", video.id, video.platform_video_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("定时刷新失败 %s: %s", video.id, exc)
        if refreshed:
            logger.info("互动数据定时刷新：本次更新 %s 个视频", refreshed)


async def periodic_stats_refresh() -> None:
    """供 FastAPI lifespan 使用：后台任务入口。"""
    # 启动后先等 90s（避免与用户即时操作抢抓取），再进入周期循环
    await asyncio.sleep(90)
    await _refresh_loop()
