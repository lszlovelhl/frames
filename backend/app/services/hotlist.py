"""热榜获取服务 - 从B站/抖音获取热门视频"""
import asyncio
import logging
from datetime import date
from typing import Any

import httpx

from app.media.cookies import get_status as get_cookies_status, get_cookie_file_for
from app.services.hotlist_playwright import fetch_bilibili_hotlist_playwright

logger = logging.getLogger(__name__)

BILI_API = "https://api.bilibili.com"
BILI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DOUYIN_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


async def fetch_bilibili_hotlist(limit: int = 50) -> list[dict[str, Any]]:
    """获取B站热门排行榜 - 用Playwright爬取，避免API风控"""
    logger.info("开始用Playwright获取B站热榜...")

    # Playwright是同步的，放到线程池里跑
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(
        None,
        lambda: fetch_bilibili_hotlist_playwright(headless=True)
    )

    logger.info("B站热榜获取完成，共 %d 条", len(items))

    # 补充字段，统一格式
    result = []
    for item in items[:limit]:
        result.append({
            **item,
            "platform": "bilibili",
            "hotlist_type": "排行榜",
        })

    return result


async def fetch_douyin_hotlist(limit: int = 50) -> list[dict[str, Any]]:
    """获取抖音热搜榜"""
    # 检查登录态
    status = get_cookies_status("douyin")
    cookies_file = get_cookie_file_for("douyin") if status.get("status") == "valid" else None

    headers = {
        "User-Agent": DOUYIN_UA,
        "Referer": "https://www.douyin.com/",
    }

    # 读取cookies
    cookies = {}
    if cookies_file:
        try:
            for line in open(cookies_file, encoding="utf-8", errors="ignore"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    cookies[parts[5]] = parts[6]
        except Exception:
            pass

    async with httpx.AsyncClient(timeout=10.0, cookies=cookies) as client:
        try:
            # 抖音热搜API
            resp = await client.get(
                "https://www.douyin.com/aweme/v1/web/hot/search/list/",
                headers=headers,
            )
            data = resp.json()

            if data.get("status_code") != 0:
                logger.error("抖音热榜获取失败: %s", data.get("status_msg"))
                return []

            items = []
            word_list = data.get("data", {}).get("word_list", [])
            for i, v in enumerate(word_list[:limit]):
                items.append({
                    "platform": "douyin",
                    "hotlist_type": "热搜",
                    "title": v.get("word", ""),
                    "hot_value": v.get("hot_value", 0),
                    "rank": i + 1,
                    "video_url": None,  # 热搜词不是具体视频
                    "author": None,
                    "summary": v.get("sentence_id", "") or "",
                    "aweme_id": v.get("sentence_id"),
                })

            logger.info("抖音热榜获取成功，共 %d 条", len(items))
            return items

        except Exception as e:
            logger.error("抖音热榜获取异常: %s", e)
            return []


async def search_bilibili_videos(keyword: str, limit: int = 20) -> list[dict[str, Any]]:
    """按关键词搜索B站视频"""
    headers = {"User-Agent": BILI_UA, "Referer": "https://search.bilibili.com/"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{BILI_API}/x/web-interface/search/type",
                params={
                    "search_type": "video",
                    "keyword": keyword,
                    "order": "click",  # 按播放量排序
                    "page": 1,
                },
                headers=headers,
            )
            data = resp.json()

            if data.get("code") != 0:
                logger.error("B站搜索失败: %s", data.get("message"))
                return []

            items = []
            for v in data.get("data", {}).get("result", [])[:limit]:
                # 清理标题里的<em>标签
                import re
                title = re.sub(r"<[^>]+>", "", v.get("title", ""))

                items.append({
                    "platform": "bilibili",
                    "title": title,
                    "hot_value": v.get("play", 0),
                    "video_url": f"https://www.bilibili.com/video/{v.get('bvid', '')}",
                    "author": v.get("author", ""),
                    "summary": v.get("description", "")[:200] if v.get("description") else "",
                    "keyword": keyword,
                })

            logger.info("B站搜索 '%s' 成功，共 %d 条", keyword, len(items))
            return items

        except Exception as e:
            logger.error("B站搜索异常: %s", e)
            return []


async def search_douyin_videos(keyword: str, limit: int = 20) -> list[dict[str, Any]]:
    """按关键词搜索抖音视频（用Playwright，需要登录态）"""
    from app.media.cookies import get_cookie_file_for
    import os
    
    cookie_file = get_cookie_file_for("douyin")
    if not cookie_file or not os.path.exists(cookie_file):
        logger.warning("抖音未登录，无法搜索关键词")
        return []
    
    # 读取cookie文件
    with open(cookie_file, "r", encoding="utf-8") as f:
        cookies_text = f.read()
    
    try:
        from playwright.async_api import async_playwright
        
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            channel="chrome",
            headless=True,
        )
        context = await browser.new_context()
        
        # 加载cookies
        cookie_list = []
        for line in cookies_text.strip().split("\n"):
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                cookie_list.append({
                    "name": parts[5],
                    "value": parts[6],
                    "domain": parts[0],
                    "path": parts[2],
                    "secure": parts[3] == "TRUE",
                })
        if cookie_list:
            await context.add_cookies(cookie_list)
        
        page = await context.new_page()
        await page.goto(f"https://www.douyin.com/search/{keyword}?type=video", wait_until="networkidle")
        await asyncio.sleep(3)  # 等页面加载
        
        # 提取视频卡片
        items = []
        cards = await page.query_selector_all('div[class*="search-result-card"], div[class*="video-card"]')
        
        for card in cards[:limit]:
            try:
                title_el = await card.query_selector('a[class*="title"], p[class*="title"]')
                title = await title_el.inner_text() if title_el else ""
                
                link_el = await card.query_selector('a[href*="/video/"]')
                href = await link_el.get_attribute("href") if link_el else ""
                video_url = f"https:{href}" if href.startswith("//") else href
                
                author_el = await card.query_selector('span[class*="author"], a[class*="author"]')
                author = await author_el.inner_text() if author_el else ""
                
                # 精确匹配：标题必须包含关键词
                if keyword.lower() in title.lower():
                    items.append({
                        "platform": "douyin",
                        "title": title,
                        "hot_value": 0,  # 抖音搜索暂时拿不到点赞数
                        "video_url": video_url,
                        "author": author,
                        "summary": "",
                        "keyword": keyword,
                    })
            except Exception:
                continue
        
        await browser.close()
        await pw.stop()
        
        logger.info("抖音搜索 '%s' 成功，共 %d 条", keyword, len(items))
        return items
        
    except Exception as e:
        logger.error("抖音搜索异常: %s", e)
        return []


async def save_hotlist_to_db(items: list[dict], db_session):
    """把热榜数据存入数据库"""
    from sqlalchemy import select
    from app.models import DailyHotlist
    import uuid
    from datetime import datetime

    today = date.today().isoformat()
    saved_count = 0

    for item in items:
        # 检查是否已存在
        existing = (await db_session.execute(
            select(DailyHotlist).where(
                DailyHotlist.date == today,
                DailyHotlist.platform == item["platform"],
                DailyHotlist.title == item["title"],
            )
        )).scalar_one_or_none()

        if existing:
            continue

        hotlist_item = DailyHotlist(
            id=uuid.uuid4().hex,
            date=today,
            platform=item["platform"],
            hotlist_type=item["hotlist_type"],
            title=item["title"],
            hot_value=item["hot_value"],
            rank=item["rank"],
            video_url=item.get("video_url"),
            author=item.get("author"),
            summary=item.get("summary"),
            collected_at=datetime.utcnow(),
        )
        db_session.add(hotlist_item)
        saved_count += 1

    await db_session.commit()
    logger.info("热榜入库完成，新增 %d 条", saved_count)
    return saved_count
