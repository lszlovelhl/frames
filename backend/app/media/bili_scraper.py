"""B 站爬虫：用官方 API 获取视频信息和视频流地址。

策略：
1. 用 view API 获取视频信息（标题、互动数据、cid 等）
2. 用 playurl API 获取视频流地址
3. 下载视频

优点：
- 不依赖页面解析，不受页面结构变更影响
- 速度快，稳定
- 数据准确
"""
import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class BilibiliScraper:
    """B 站爬虫（API 版）。"""

    def __init__(self):
        self.base_url = "https://api.bilibili.com"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com/",
        }

    async def get_video_info(self, url: str) -> dict[str, Any]:
        """获取视频信息。"""
        bvid = self._extract_bvid(url)
        if not bvid:
            raise Exception("无法解析 BV 号")

        async with httpx.AsyncClient(timeout=30) as client:
            # 1. 获取视频信息
            resp = await client.get(
                f"{self.base_url}/x/web-interface/view",
                params={"bvid": bvid},
                headers=self.headers,
            )
            data = resp.json()
            if data.get("code") != 0:
                raise Exception(f"获取视频信息失败：{data.get('message')}")

            video_data = data["data"]
            stat = video_data.get("stat", {})
            pages = video_data.get("pages", [])

            return {
                "bvid": bvid,
                "aid": video_data.get("aid"),
                "cid": pages[0].get("cid") if pages else None,
                "title": video_data.get("title", ""),
                "desc": video_data.get("desc", ""),
                "duration_ms": video_data.get("duration", 0) * 1000,
                "cover_url": video_data.get("pic", ""),
                "author": video_data.get("owner", {}).get("name", ""),
                "author_mid": video_data.get("owner", {}).get("mid"),
                "view_count": stat.get("view", 0),
                "like_count": stat.get("like", 0),
                "coin_count": stat.get("coin", 0),
                "favorite_count": stat.get("favorite", 0),
                "share_count": stat.get("share", 0),
                "danmaku_count": stat.get("danmaku", 0),
                "reply_count": stat.get("reply", 0),
            }

    async def download_video(self, url: str, work_dir: Path) -> str:
        """下载视频。"""
        # 1. 获取视频信息
        info = await self.get_video_info(url)
        cid = info["cid"]
        bvid = info["bvid"]

        # 2. 获取视频流地址
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base_url}/x/player/playurl",
                params={
                    "bvid": bvid,
                    "cid": cid,
                    "qn": 64,  # 720P
                    "fnval": 0,  # mp4 格式
                },
                headers=self.headers,
            )
            data = resp.json()
            if data.get("code") != 0:
                raise Exception(f"获取视频流失败：{data.get('message')}")

            # 3. 下载视频
            durl = data["data"]["durl"]
            if not durl:
                raise Exception("没有视频流地址")

            video_url = durl[0]["url"]
            video_path = work_dir / "video.mp4"

            await self._download_file(video_url, video_path)
            return str(video_path)

    def _extract_bvid(self, url: str) -> str | None:
        """从 URL 里提取 BV 号。"""
        import re
        match = re.search(r"(BV[a-zA-Z0-9]+)", url)
        if match:
            return match.group(1)
        return None

    async def _download_file(self, url: str, path: Path):
        """下载文件。"""
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("GET", url, headers=self.headers) as resp:
                resp.raise_for_status()
                with open(path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)


# 全局单例
_scraper: BilibiliScraper | None = None


async def get_scraper() -> BilibiliScraper:
    """获取爬虫单例。"""
    global _scraper
    if _scraper is None:
        _scraper = BilibiliScraper()
    return _scraper


async def close_scraper():
    """关闭爬虫。"""
    global _scraper
    _scraper = None
