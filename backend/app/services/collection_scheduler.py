"""自动采集定时任务"""
import asyncio
import logging
from datetime import datetime, time

from sqlalchemy import select

from app.db import SessionLocal
from app.models import CollectionConfig, Video
from app.services.hotlist import fetch_bilibili_hotlist, fetch_douyin_hotlist, save_hotlist_to_db, search_bilibili_videos, search_douyin_videos

logger = logging.getLogger(__name__)


async def add_video_to_queue(url: str, platform: str = "bilibili"):
    """把视频加入拆解队列"""
    from app.media.downloader import download
    from app.media.platform import detect_platform
    from pathlib import Path

    try:
        # 检查是否已存在
        async with SessionLocal() as db:
            existing = (await db.execute(select(Video).where(Video.url == url).limit(1))).scalar_one_or_none()
            if existing:
                logger.info("视频已存在: %s", url)
                return existing.id

        # 下载视频 - 用绝对路径，避免相对路径问题
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        work_dir = Path(base_dir) / "data" / "videos" / url[-20:].replace("/", "_")
        work_dir.mkdir(parents=True, exist_ok=True)

        dl = await download(url, work_dir, platform=platform)
        meta = dl["meta"]

        # 创建视频记录
        async with SessionLocal() as db:
            video = Video(
                platform=platform,
                url=url,
                title=meta.get("title") or "未命名视频",
                author_name=meta.get("uploader"),
                cover_url=meta.get("thumbnail"),
                duration_ms=int((meta.get("duration") or 0) * 1000) or None,
                stats_snapshot={
                    k: meta.get(k)
                    for k in ("view_count", "like_count", "comment_count", "publish_date")
                    if meta.get(k) is not None
                },
                raw_files={
                    "video_path": dl["video_path"],
                    "work_dir": str(work_dir),
                    "media_status": "downloaded",
                },
            )
            db.add(video)
            await db.commit()
            await db.refresh(video)

            logger.info("视频已加入队列: %s - %s", url, video.title)
            return video.id

    except Exception as e:
        logger.error("加入视频队列失败 %s: %s", url, e)
        return None


async def collection_loop():
    """自动采集主循环"""
    logger.info("自动采集定时任务已启动")

    while True:
        try:
            async with SessionLocal() as db:
                config = (await db.execute(select(CollectionConfig).limit(1))).scalar_one_or_none()

                if not config or not config.enabled:
                    # 未开启，10分钟后再检查
                    await asyncio.sleep(600)
                    continue

                # 检查是否到了运行时间
                now = datetime.now()
                if config.last_run_at:
                    next_run = config.last_run_at.timestamp() + config.interval_minutes * 60
                    if now.timestamp() < next_run:
                        await asyncio.sleep(60)
                        continue

                logger.info("开始自动采集...")

                # 更新运行时间
                config.last_run_at = now
                await db.commit()

                # 1. 热榜模式：获取热榜
                if config.hotlist_enabled:
                    try:
                        bili_items = await fetch_bilibili_hotlist(limit=config.max_videos_per_run)
                        douyin_items = await fetch_douyin_hotlist(limit=config.max_videos_per_run)

                        all_items = bili_items + douyin_items
                        if all_items:
                            saved = await save_hotlist_to_db(all_items, db)
                            logger.info("自动采集热榜完成，新增 %d 条", saved)

                            # 从B站热榜里选视频加入拆解队列
                            for item in bili_items:
                                if item.get("video_url"):
                                    # 检查点赞数是否达标
                                    if item.get("hot_value", 0) >= config.min_likes:
                                        await add_video_to_queue(item["video_url"], "bilibili")

                    except Exception as e:
                        logger.error("自动采集热榜失败: %s", e)

                # 2. 关键词模式：按关键词搜视频（B站+抖音）
                if config.keyword_enabled and config.keywords:
                    for keyword in config.keywords[:3]:  # 每次最多搜3个关键词
                        try:
                            logger.info("搜索关键词: %s", keyword)
                            per_platform_limit = config.max_videos_per_run // max(len(config.keywords), 1) // 2
                            
                            # 搜B站
                            bili_results = await search_bilibili_videos(keyword, limit=per_platform_limit)
                            for item in bili_results:
                                if item.get("hot_value", 0) >= config.min_likes:
                                    await add_video_to_queue(item["video_url"], "bilibili")
                            
                            # 搜抖音
                            douyin_results = await search_douyin_videos(keyword, limit=per_platform_limit)
                            for item in douyin_results:
                                await add_video_to_queue(item["video_url"], "douyin")

                        except Exception as e:
                            logger.error("关键词搜索失败 %s: %s", keyword, e)

                logger.info("自动采集完成")

        except Exception as e:
            logger.error("自动采集循环异常: %s", e)
            await asyncio.sleep(300)  # 出错后5分钟再试


async def daily_report_loop():
    """每日热榜简报定时任务（每天9点运行）"""
    logger.info("每日热榜简报定时任务已启动")

    while True:
        try:
            now = datetime.now()
            # 目标时间：今天早上9点
            target = now.replace(hour=9, minute=0, second=0, microsecond=0)

            # 如果现在已经过了9点，就等明天9点
            if now >= target:
                target = target.replace(day=now.day + 1)

            wait_seconds = (target - now).total_seconds()
            logger.info("下次热榜简报时间: %s，等待 %.1f 小时", target, wait_seconds / 3600)

            await asyncio.sleep(wait_seconds)

            # 生成昨日热榜简报
            logger.info("开始生成昨日热榜简报...")
            # TODO: AI生成热榜分析简报
            # 1. 获取昨天的热榜数据
            # 2. 总结热点趋势
            # 3. 分析和短视频创作相关的热点
            # 4. 推送到飞书/通知用户

        except Exception as e:
            logger.error("每日简报任务异常: %s", e)
            await asyncio.sleep(3600)  # 出错后1小时再试
