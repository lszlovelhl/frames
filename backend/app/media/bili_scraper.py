"""B 站爬虫：用官方 API 获取视频信息和视频流地址。

策略：
1. 用 view API 获取视频信息（标题、互动数据、cid 等）
2. 用 playurl API 获取视频流地址
3. 流式下载到 ``.part`` 临时文件，流完整结束后定名，由 downloader 侧 ffprobe 终验

设计说明
--------
- 不实现 WBI 签名（实现成本高、签名参数易随版本失效）：API 路遇风控/无签名错误时，
  由 ``downloader._download_bilibili`` 自动降级到 yt-dlp 路线兜底；
- HTTP 层错误（超时 / 连接错误 / 429 / 5xx / 403）不在本模块吞掉，交给
  ``resilience.classify_exception`` 统一分类退避；
- 业务 code 层区分永久失败（稿件删除 / 不可见 / 私密）与可重试错误。
"""
import logging
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# API 请求超时：信息接口短超时（快速失败进降级链），流下载长超时
API_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
STREAM_TIMEOUT = httpx.Timeout(300.0, connect=15.0)
# 最小有效视频字节数（与 downloader._ffprobe_has_video 阈值对齐，防空文件）
MIN_VIDEO_BYTES = 10240

# B 站永久失败业务码：稿件不可见 / 删除 / 私密 / 无权限 / 合集不可见
# （这些不会因重试消失，downloader 拿到后直接走降级链或落永久证据）
PERMANENT_API_CODES: frozenset[int] = frozenset({
    12051,  # 稿件不可见（UP 主删除 / 私密）
    62028,  # 合集/列表视频不可见
    -404, 404,  # 不存在
    -403, 12040,  # 无权限 / 仅限关注者
})


class BiliApiError(Exception):
    """B 站 API 业务错误：``code`` 为平台返回码，``permanent`` 表示是否永久失败。"""

    def __init__(self, code: int, message: str, *, permanent: bool = False):
        super().__init__(f"[code={code}] {message}")
        self.code = code
        self.message = message
        self.permanent = permanent


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
            raise BiliApiError(-1, "无法从链接解析 BV 号，请确认粘贴的是完整 B 站链接",
                               permanent=True)

        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            # 1. 获取视频信息
            resp = await client.get(
                f"{self.base_url}/x/web-interface/view",
                params={"bvid": bvid},
                headers=self.headers,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                code = data.get("code") or 0
                raise BiliApiError(
                    code, data.get("message") or "获取视频信息失败",
                    permanent=code in PERMANENT_API_CODES,
                )

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
        """下载视频；先写 ``video.mp4.part``，完整结束后定名 ``video.mp4``。"""
        # 1. 获取视频信息
        info = await self.get_video_info(url)
        cid = info["cid"]
        bvid = info["bvid"]
        if not cid:
            raise BiliApiError(-2, "API 未返回分 P cid（可能为合集/多 P 视频）",
                               permanent=True)

        # 2. 获取视频流地址
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
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
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                code = data.get("code") or 0
                raise BiliApiError(
                    code, data.get("message") or "获取视频流失败",
                    permanent=code in PERMANENT_API_CODES,
                )

            # 3. 下载视频
            durl = data["data"]["durl"]
            if not durl:
                raise BiliApiError(-3, "没有视频流地址（可能为纯音频 / 直播回放）",
                                   permanent=True)

            video_url = durl[0]["url"]
            video_path = work_dir / "video.mp4"
            part_path = work_dir / "video.mp4.part"

            await self._download_file(video_url, part_path)
            size = part_path.stat().st_size if part_path.exists() else 0
            if size < MIN_VIDEO_BYTES:
                part_path.unlink(missing_ok=True)
                raise BiliApiError(
                    -4, f"下载产物过小（{size} 字节），疑似截断流或空文件", permanent=False
                )
            part_path.replace(video_path)
            return str(video_path)

    def _extract_bvid(self, url: str) -> str | None:
        """从 URL 里提取 BV 号。"""
        import re
        match = re.search(r"(BV[a-zA-Z0-9]+)", url)
        if match:
            return match.group(1)
        return None

    async def _download_file(self, url: str, path: Path) -> None:
        """流式下载文件到临时路径；流中断时异常上抛（由上层分类退避/降级）。"""
        path.unlink(missing_ok=True)
        async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
            async with client.stream("GET", url, headers=self.headers) as resp:
                resp.raise_for_status()
                with open(path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1 << 20):
                        if chunk:
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
