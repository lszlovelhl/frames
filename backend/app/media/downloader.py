"""视频下载。策略：
- bilibili：优先 bilix（内置 wbi 风控适配），失败回退 yt-dlp + 预取 cookie
- youtube / douyin：yt-dlp
- xiaohongshu / wechat：无公开稳定取流，抛 DownloadBlocked
"""
import asyncio
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yt_dlp

logger = logging.getLogger(__name__)

BILIX_EXE = str(Path(sys.executable).parent / "bilix")

BILI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class DownloadBlocked(Exception):
    """该平台/链接无法自动取流，需要用户介入（扫码、手动上传等）。"""


async def download(url: str, work_dir: Path, platform: str = "") -> dict[str, Any]:
    """下载视频到 work_dir，返回 {video_path, meta}。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    if platform in ("xiaohongshu", "wechat"):
        raise DownloadBlocked(
            "小红书 / 微信视频号无公开稳定取流接口，暂无法自动下载。"
            "后续版本将提供浏览器通道（需首次扫码授权）。"
        )
    if platform == "bilibili":
        return await _download_bilibili(url, work_dir)
    return await _download_generic(url, work_dir, platform)


# ---------- bilibili（bilix 优先） ----------

def _parse_bili_title(info_text: str) -> tuple[str, dict]:
    """从 bilix info 输出解析标题与数据快照。"""
    title = ""
    stats: dict[str, int | str] = {}
    for line in info_text.splitlines():
        m = re.match(r"^\s*([^\s].*?)\s*[-—]\s*([\d,]+)👀\s*([\d,]+)👍\s*([\d,]+)🪙", line)
        if m:
            title = m.group(1).strip()
            stats = {
                "view_count": int(m.group(2).replace(",", "")),
                "like_count": int(m.group(3).replace(",", "")),
                "coin_count": int(m.group(4).replace(",", "")),
            }
            break
        if not title and not line.startswith(("INFO", "┣", "┗", "┃")):
            t = line.strip()
            if t and " " not in t and len(t) > 2:
                title = t
                break
    if not title:
        for line in info_text.splitlines():
            t = line.strip()
            if t and not t.startswith(("INFO", "┣", "┗", "┃", "─", " ")):
                title = t
                break
    return title, stats


async def _bilix_info(url: str) -> tuple[str, dict]:
    loop = asyncio.get_running_loop()
    try:
        proc = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                [BILIX_EXE, "info", url], capture_output=True, text=True,
                timeout=60, cwd="/tmp",
            ),
        )
        return _parse_bili_title(proc.stdout)
    except Exception:  # noqa: BLE001
        return "", {}


def _find_largest_mp4(work_dir: Path) -> Path | None:
    files = list(work_dir.rglob("*.mp4"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_size)


async def _download_bilibili(url: str, work_dir: Path) -> dict[str, Any]:
    meta_title, stats = await _bilix_info(url)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                [BILIX_EXE, "get_video", url, "-d", str(work_dir), "-q", "0"],
                capture_output=True, text=True, timeout=600, cwd=str(work_dir),
            ),
        )
        video_path = _find_largest_mp4(work_dir)
        if video_path:
            return {
                "video_path": str(video_path),
                "meta": {"title": meta_title or "", "stats_snapshot": stats},
            }
    except subprocess.TimeoutExpired:
        logger.warning("bilix 下载超时，回退 yt-dlp")
    except Exception as exc:  # noqa: BLE001
        logger.warning("bilix 下载失败：%s，回退 yt-dlp", exc)

    # 回退：yt-dlp + 预取 buvid cookie
    try:
        return await _download_ytdlp_bilibili(url, work_dir, meta_title)
    except Exception as exc:  # noqa: BLE001
        raise DownloadBlocked(f"B站下载失败（风控或资源限制）：{exc}") from exc


async def _download_ytdlp_bilibili(
    url: str, work_dir: Path, meta_title: str
) -> dict[str, Any]:
    cookie_file = work_dir / "_bili_cookies.txt"
    # 先访问首页拿 buvid
    subprocess.run(
        ["curl", "-s", "-c", str(cookie_file), "-o", "/dev/null",
         "-A", BILI_UA, "https://www.bilibili.com/"],
        check=True, timeout=30, capture_output=True,
    )
    loop = asyncio.get_running_loop()

    def _run():
        opts: dict = {
            "outtmpl": str(work_dir / "ytdlp_media.%(ext)s"),
            "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "cookiefile": str(cookie_file),
            "http_headers": {
                "User-Agent": BILI_UA,
                "Referer": "https://www.bilibili.com/",
            },
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)

    info = await loop.run_in_executor(None, _run)
    video_path = next(work_dir.glob("ytdlp_media.*"), None)
    if video_path is None:
        raise RuntimeError("yt-dlp 未产出文件")
    return {
        "video_path": str(video_path),
        "meta": {
            "title": meta_title or info.get("title"),
            "uploader": info.get("uploader"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "comment_count": info.get("comment_count"),
        },
    }


# ---------- 通用（yt-dlp） ----------

async def _download_generic(url: str, work_dir: Path, platform: str) -> dict[str, Any]:
    referer = {
        "douyin": "https://www.douyin.com/",
        "youtube": "https://www.youtube.com/",
    }.get(platform, url)
    opts: dict = {
        "outtmpl": str(work_dir / "media.%(ext)s"),
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "socket_timeout": 25,
        "http_headers": {
            "User-Agent": BILI_UA,
            "Referer": referer,
        },
    }
    loop = asyncio.get_running_loop()

    def _run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)

    try:
        info = await loop.run_in_executor(None, _run)
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadBlocked(f"下载失败：{exc}") from exc

    video_path = next(work_dir.glob("media.*"), None)
    if video_path is None:
        raise DownloadBlocked("下载完成但未找到媒体文件（可能是会员/地区限制）")
    if video_path.suffix.lower() != ".mp4":
        converted = work_dir / "media.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-c", "copy", str(converted)],
            check=True, capture_output=True,
        )
        video_path.unlink(missing_ok=True)
        video_path = converted
    return {
        "video_path": str(video_path),
        "meta": {
            "title": info.get("title"),
            "uploader": info.get("uploader") or info.get("creator"),
            "uploader_id": info.get("uploader_id"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "comment_count": info.get("comment_count"),
            "publish_date": info.get("upload_date"),
        },
    }
