"""视频下载。降级链与重试策略（分类/退避实现在 app.media.resilience）：

- bilibili：自研官方 API 爬虫（可重试错误指数退避重试）→ 仍失败回退
  ``_download_ytdlp_bilibili``（带 cookie + ffmpeg 位置）→ 永久失败抛
  DownloadBlocked 并携带结构化 detail；
- youtube / douyin 等通用路线：yt-dlp，可重试 DownloadError 走退避；
  永久风控/登录失效走 ``cookies.mark_expired`` + DownloadBlocked；
- wechat：元宝解析 → finder-preview feed → 直链/m3u8 下载，各环节可重试退避，
  cookie 过期 / 内容下架直接 DownloadBlocked；
- xiaohongshu：无公开稳定取流，抛 DownloadBlocked。

产物安全：流式下载先写 ``.part`` 临时名，ffprobe 校验通过后再定名，
半截 mp4 / 空文件 / HTML 错误页不会被当作成功产物。
"""
import asyncio
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx
import yt_dlp

from app.media import cookies, ffbin
from app.media.resilience import (
    AUTH_EXPIRED,
    CONTENT_UNAVAILABLE,
    MAX_ERROR_TEXT,
    STREAM_INTERRUPTED,
    ErrorVerdict,
    classify_exception,
    classify_http_status,
    reason_code,
    retry_with_backoff,
    user_message_for,
)

logger = logging.getLogger(__name__)

BILI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 各链路尝试次数（退避基准/上限/抖动在 resilience.py 集中定义）
BILI_API_ATTEMPTS = 3
BILI_YTDLP_ATTEMPTS = 2
YTDLP_ATTEMPTS = 3
WECHAT_API_ATTEMPTS = 3
WECHAT_STREAM_ATTEMPTS = 2
# 超时常量：API 类短超时（快速失败进降级链），媒体流长超时
WECHAT_API_TIMEOUT = 30.0
MEDIA_CONNECT_TIMEOUT = 15.0
MEDIA_READ_TIMEOUT = 60.0
FFMPEG_CONVERT_TIMEOUT = 120


class DownloadBlocked(Exception):
    """该平台/链接无法自动取流，需要用户介入（扫码、手动上传等）。

    ``detail`` 为结构化失败证据（category / retryable / attempts / last_error ...），
    供队列层写入 ``job.evidence["collect_failure"]``；纯展示路径只用 ``str(exc)``。
    """

    def __init__(self, message: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.detail: dict[str, Any] = detail or {}


async def download(url: str, work_dir: Path, platform: str = "") -> dict[str, Any]:
    """下载视频到 work_dir，返回 {video_path, meta}。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    if platform == "xiaohongshu":
        # 无公开稳定取流；即使有登录态 yt-dlp 也没有可靠 extractor
        raise DownloadBlocked(
            "小红书无公开稳定取流接口，暂无法自动下载。"
            "请到「采集账号」页按提示完成平台授权；如仍失败可先把视频存到本地再手动上传。"
        )
    if platform == "wechat":
        return await _download_wechat(url, work_dir)
    if platform == "bilibili":
        return await _download_bilibili(url, work_dir)
    return await _download_generic(url, work_dir, platform)


# ---------- 结构化失败证据 ----------

def _failure_detail(
    *,
    platform: str,
    verdict: ErrorVerdict,
    attempts: int,
    last_error: str,
    chain: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """组装写入 ``job.evidence["collect_failure"]`` 的结构化证据。"""
    return {
        "stage": "download",
        "platform": platform,
        "category": verdict.category,
        "error_type": reason_code(platform, verdict.category),
        "retryable": verdict.retryable,
        "attempts": attempts,
        "last_error": (last_error or "")[:MAX_ERROR_TEXT],
        "user_message": verdict.user_message,
        "chain": chain or [],
    }


def _worst_verdict(a: ErrorVerdict, b: ErrorVerdict) -> ErrorVerdict:
    """降级链汇总：永久失败优先于可重试失败，避免可重试结论掩盖永久结论。"""
    if not a.retryable and not b.retryable:
        # 同为永久：以最后一路（最新错误）为准
        return b
    if not a.retryable:
        return a
    if not b.retryable:
        return b
    return b


# ---------- bilibili（自研 API 爬虫 → yt-dlp 降级链） ----------

async def _download_bilibili(url: str, work_dir: Path) -> dict[str, Any]:
    """B 站下载降级链：

    1. 自研官方 API 爬虫（view + playurl），可重试错误指数退避重试；
    2. 仍失败（含风控 / 产物校验失败）→ 回退 ``_download_ytdlp_bilibili``；
    3. 两路都失败 → 按分类给用户可操作中文 message，detail 携带链路证据。
    """
    from app.media.bili_scraper import get_scraper

    chain_trace: list[dict[str, Any]] = []

    # 第一路：自研 API 爬虫
    api_out: dict[str, Any] = {}
    first_verdict: ErrorVerdict | None = None
    try:
        async def api_factory():
            scraper = await get_scraper()
            video_info = await scraper.get_video_info(url)
            video_path = await scraper.download_video(url, work_dir)
            return video_info, video_path

        video_info, video_path = await retry_with_backoff(
            api_factory,
            attempts=BILI_API_ATTEMPTS,
            platform="bilibili",
            url=url,
            label="bili_api",
            out=api_out,
        )
        # 产物闸门：ffprobe 不认作有效视频流（空文件 / 截断流 / HTML 错误页）→ 进降级链
        if not _ffprobe_has_video(Path(video_path)):
            raise RuntimeError("API 爬虫产物未通过 ffprobe 视频流校验（空文件或截断流）")
        if cookies.get_cookie_file_for("bilibili"):
            cookies.mark_success("bilibili")
        return {
            "video_path": str(video_path),
            "meta": {
                "title": video_info.get("title", ""),
                "stats_snapshot": {
                    "view_count": video_info.get("view_count", 0),
                    "like_count": video_info.get("like_count", 0),
                    "coin_count": video_info.get("coin_count", 0),
                    "favorite_count": video_info.get("favorite_count", 0),
                    "share_count": video_info.get("share_count", 0),
                },
            },
        }
    except Exception as exc:  # noqa: BLE001 分类后进入降级链，不吞异常
        first_verdict = classify_exception(exc, platform="bilibili")
        chain_trace.append({
            "source": "bili_api",
            "category": first_verdict.category,
            "retryable": first_verdict.retryable,
            "attempts": api_out.get("attempts", 1),
            "error": str(exc)[:MAX_ERROR_TEXT],
        })
        logger.warning(
            "B站 API 链路失败，进入 yt-dlp 降级：category=%s error=%s",
            first_verdict.category, exc,
        )
        # 清理可能残留的半截文件，避免被后续流程误判成功
        for stale in work_dir.glob("video.mp4*"):
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                logger.warning("清理 B站 API 残留文件失败: %s", stale, exc_info=True)

    # 第二路：yt-dlp 回退（自带 cookie / ffmpeg 位置适配）
    yt_out: dict[str, Any] = {}
    try:
        return await retry_with_backoff(
            lambda: _download_ytdlp_bilibili(url, work_dir, meta_title=""),
            attempts=BILI_YTDLP_ATTEMPTS,
            platform="bilibili",
            url=url,
            label="bili_ytdlp",
            out=yt_out,
        )
    except Exception as exc:  # noqa: BLE001 汇总两路证据后抛 DownloadBlocked
        second_verdict = classify_exception(exc, platform="bilibili")
        chain_trace.append({
            "source": "yt_dlp",
            "category": second_verdict.category,
            "retryable": second_verdict.retryable,
            "attempts": yt_out.get("attempts", 1),
            "error": str(exc)[:MAX_ERROR_TEXT],
        })
        if second_verdict.category == AUTH_EXPIRED:
            cookies.mark_expired("bilibili", "B站抓取被平台判定需登录/风控，登录态可能失效")
        final_verdict = _worst_verdict(first_verdict or second_verdict, second_verdict)
        detail = _failure_detail(
            platform="bilibili",
            verdict=final_verdict,
            attempts=api_out.get("attempts", 0) + yt_out.get("attempts", 0),
            last_error=" | ".join(item["error"] for item in chain_trace),
            chain=chain_trace,
        )
        raise DownloadBlocked(final_verdict.user_message, detail=detail) from exc


async def _download_ytdlp_bilibili(
    url: str, work_dir: Path, meta_title: str
) -> dict[str, Any]:
    """yt-dlp 降级路线：Netscape cookie 文件 + 显式 ffmpeg 位置。"""
    cookie_file = cookies.get_cookie_file_for("bilibili")
    if cookie_file:
        cookie_path_obj = Path(cookie_file)
    else:
        cookie_path_obj = work_dir / "_bili_cookies.txt"
        # 先访问首页拿 buvid
        try:
            subprocess.run(
                ["curl", "-s", "-c", str(cookie_path_obj), "-o", "/dev/null",
                 "-A", BILI_UA, "https://www.bilibili.com/"],
                check=True, timeout=30, capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            logger.warning("预取 B 站 buvid cookie 失败（继续尝试无 cookie 下载）: %s", exc)
    loop = asyncio.get_running_loop()

    def _run():
        opts: dict = {
            "outtmpl": str(work_dir / "ytdlp_media.part.%(ext)s"),
            "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "cookiefile": str(cookie_path_obj),
            "http_headers": {
                "User-Agent": BILI_UA,
                "Referer": "https://www.bilibili.com/",
            },
        }
        # 显式告知 yt-dlp ffmpeg 位置：GUI/launchd 启动时 PATH 可能不含 homebrew，
        # 否则音视频合流（merge_output_format）会因找不到 ffmpeg 失败
        ff_loc = ffbin.ffmpeg_location()
        if ff_loc:
            opts["ffmpeg_location"] = ff_loc
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)

    info = await loop.run_in_executor(None, _run)
    if cookie_file:
        cookies.mark_success("bilibili")
    raw_path = next(work_dir.glob("ytdlp_media.part.*"), None)
    if raw_path is None:  # 兼容旧命名残留
        raw_path = next(work_dir.glob("ytdlp_media.*"), None)
    if raw_path is None:
        raise RuntimeError("yt-dlp 未产出文件（可能为会员专属 / 地区限制 / 稿件不可见）")
    video_path = work_dir / "ytdlp_media.mp4"
    if raw_path != video_path:
        raw_path.replace(video_path)
    if not _ffprobe_has_video(video_path):
        video_path.unlink(missing_ok=True)
        raise RuntimeError("yt-dlp 产物未通过 ffprobe 视频流校验（截断流或空文件）")
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
    # douyin 注入自定义 UA 会被风控判为 "Fresh cookies needed"，须用 yt-dlp 默认 UA
    headers = {"Referer": referer}
    if platform != "douyin":
        headers["User-Agent"] = BILI_UA
    opts: dict = {
        "outtmpl": str(work_dir / "media.part.%(ext)s"),
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "socket_timeout": 25,
        "http_headers": headers,
    }
    # 同 bilibili：显式指定 ffmpeg 位置，避免合流失败
    ff_loc = ffbin.ffmpeg_location()
    if ff_loc:
        opts["ffmpeg_location"] = ff_loc
    # 平台已配置登录态 → 注入 cookie，规避反爬/风控
    cookie_file = cookies.get_cookie_file_for(platform)
    if cookie_file:
        opts["cookiefile"] = cookie_file
    loop = asyncio.get_running_loop()

    def _run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)

    out: dict[str, Any] = {}
    try:
        info = await retry_with_backoff(
            lambda: loop.run_in_executor(None, _run),
            attempts=YTDLP_ATTEMPTS,
            platform=platform,
            url=url,
            label="yt_dlp",
            out=out,
        )
    except Exception as exc:  # noqa: BLE001 DownloadError 与其他异常统一分类落证据
        verdict = classify_exception(exc, platform=platform)
        msg = str(exc)
        # "Fresh cookies (not necessarily logged in) are needed" 是 yt-dlp 对反爬
        # 的提示（常因 UA/首次请求被风控），并非登录态失效，不应置 expired 自锁
        if verdict.category == AUTH_EXPIRED and "not necessarily logged in" not in msg.lower():
            cookies.mark_expired(platform, "抓取被平台判定需登录/风控，登录态可能失效")
        detail = _failure_detail(
            platform=platform, verdict=verdict,
            attempts=out.get("attempts", 1), last_error=msg,
        )
        raise DownloadBlocked(verdict.user_message, detail=detail) from exc

    # 抓取成功 → 回写登录态有效（仅当确实用过 cookie）
    if cookie_file:
        cookies.mark_success(platform)

    raw_path = next(work_dir.glob("media.part.*"), None)
    if raw_path is None:  # 兼容旧命名残留
        raw_path = next(work_dir.glob("media.*"), None)
    if raw_path is None:
        verdict = ErrorVerdict(
            False, CONTENT_UNAVAILABLE, user_message_for(CONTENT_UNAVAILABLE)
        )
        raise DownloadBlocked(
            verdict.user_message,
            detail=_failure_detail(
                platform=platform, verdict=verdict, attempts=out.get("attempts", 1),
                last_error="下载完成但未找到媒体文件（可能是会员/地区限制）",
            ),
        )
    video_path = work_dir / "media.mp4"
    if raw_path.suffix.lower() != ".mp4":
        try:
            subprocess.run(
                [ffbin.ffmpeg_bin(), "-y", "-i", str(raw_path), "-c", "copy", str(video_path)],
                check=True, capture_output=True, timeout=FFMPEG_CONVERT_TIMEOUT,
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            verdict = classify_exception(exc, platform=platform)
            raise DownloadBlocked(
                verdict.user_message,
                detail=_failure_detail(
                    platform=platform, verdict=verdict,
                    attempts=out.get("attempts", 1), last_error=str(exc),
                ),
            ) from exc
        raw_path.unlink(missing_ok=True)
    elif raw_path != video_path:
        raw_path.replace(video_path)
    # 产物闸门：ffprobe 校验通过才算成功；半截流/空文件直接删除并报可重试类错误
    if not _ffprobe_has_video(video_path):
        video_path.unlink(missing_ok=True)
        verdict = ErrorVerdict(
            True, STREAM_INTERRUPTED, user_message_for(STREAM_INTERRUPTED)
        )
        raise DownloadBlocked(
            verdict.user_message,
            detail=_failure_detail(
                platform=platform, verdict=verdict,
                attempts=out.get("attempts", 1),
                last_error="ffprobe 未检出视频流（空文件 / 截断流 / 非媒体响应）",
            ),
        )
    # douyin 不公开播放量：yt-dlp 返回的 view_count 为 0/缺失是平台特性，
    # 不写入 meta，避免建档 stats_snapshot 出现假的「播放 0」
    if platform == "douyin" and not (info.get("view_count") or 0):
        meta_view = None
    else:
        meta_view = info.get("view_count")
    return {
        "video_path": str(video_path),
        "meta": {
            "title": info.get("title"),
            "uploader": info.get("uploader") or info.get("creator"),
            "uploader_id": info.get("uploader_id"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "view_count": meta_view,
            "like_count": info.get("like_count"),
            "comment_count": info.get("comment_count"),
            "publish_date": info.get("upload_date"),
        },
    }


# ---------- 微信视频号（元宝解析 → finder-preview feed 直链） ----------

# 视频号没有公开播放流：分享链接先借道腾讯元宝解析接口换 exportId+token，
# 再调 finder-preview feed 接口取 finder.video.qq.com 直链（可能带 decodeKey，
# 表示文件头 128KB 经 ISAAC64 加密，需下载后本地解密）。Cookie 来自元宝网页登录态，
# 由「采集账号」页导入（data/cookies/wechat.cookies.txt，header 字符串格式）。
_WECHAT_PARSE_URL = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
_WECHAT_PARSE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "content-type": "application/json",
    "origin": "https://yuanbao.tencent.com",
    "referer": "https://yuanbao.tencent.com/chat/naQivTmsDa/cf4d0079-ed1b-4c55-a3f3-2ca1379727d1",
    "user-agent": BILI_UA,
    "x-agentid": "naQivTmsDa/cf4d0079-ed1b-4c55-a3f3-2ca1379727d1",
    "x-source": "web",
    "x-requested-with": "XMLHttpRequest",
    "x-platform": "mac",
    "x-language": "zh-CN",
}
_WECHAT_FEED_URL = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
_WECHAT_FEED_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Origin": "https://channels.weixin.qq.com",
    "User-Agent": BILI_UA,
}
_WECHAT_ENC_LIMIT = 131072  # decodeKey 加密仅作用于文件头 128KB
_U64 = (1 << 64) - 1


class _WechatError(Exception):
    """视频号解析链路中可向用户展示的错误；``verdict`` 供退避 / 落库分类决策。"""

    def __init__(self, message: str, verdict: ErrorVerdict | None = None):
        super().__init__(message)
        self.verdict = verdict


def _wechat_cookie_text() -> str:
    """读取元宝登录态（header 字符串格式，非 Netscape）。"""
    if not cookies.get_status("wechat")["has_cookie"]:
        raise _WechatError(
            "微信视频号暂未配置有效登录态：请到「采集账号」页微信视频号一栏，"
            "登录 yuanbao.tencent.com 后从浏览器开发者工具复制 Cookie 导入。",
            ErrorVerdict(False, AUTH_EXPIRED, user_message_for(AUTH_EXPIRED)),
        )
    st = cookies.get_status("wechat")
    if st["status"] not in ("valid", "imported"):
        raise _WechatError(
            "微信视频号登录态已失效，请在「采集账号」页重新导入元宝 Cookie。",
            ErrorVerdict(False, AUTH_EXPIRED, user_message_for(AUTH_EXPIRED)),
        )
    text = cookies.cookie_path("wechat").read_text(encoding="utf-8", errors="ignore").strip()
    if "hy_token" not in text:
        raise _WechatError(
            "微信视频号登录态缺少元宝鉴权字段（hy_token），请重新导入 Cookie。",
            ErrorVerdict(False, AUTH_EXPIRED, user_message_for(AUTH_EXPIRED)),
        )
    return text


def _plain_text(value: Any) -> str:
    import html as _html

    text = str(value or "")
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]*>", "", text)
    return _html.unescape(text).strip()


async def _wechat_parse_share(cookie: str, share_url: str) -> tuple[str, str]:
    """元宝解析分享链接 → (eid, token)；可重试错误由 retry_with_backoff 退避。"""
    headers = {**_WECHAT_PARSE_HEADERS, "cookie": cookie}

    async def factory():
        async with httpx.AsyncClient(timeout=WECHAT_API_TIMEOUT) as client:
            return await client.post(
                _WECHAT_PARSE_URL,
                json={"type": "video_channel_url", "url": share_url, "scene": 1},
                headers=headers,
            )

    try:
        resp = await retry_with_backoff(
            factory, attempts=WECHAT_API_ATTEMPTS,
            platform="wechat", url=share_url, label="wechat_parse",
        )
    except httpx.HTTPError as exc:
        raise _WechatError(
            f"元宝解析接口请求失败：{exc}", classify_exception(exc, platform="wechat")
        ) from exc

    status = resp.status_code
    if status in (401, 403):
        cookies.mark_expired("wechat", "元宝解析接口返回 401/403，Cookie 缺失或已过期")
        raise _WechatError(
            "元宝解析接口返回 401/403：Cookie 已过期，请在「采集账号」页更新。",
            ErrorVerdict(False, AUTH_EXPIRED, user_message_for(AUTH_EXPIRED)),
        )
    if status == 429 or 500 <= status <= 599:
        raise _WechatError(
            f"元宝解析接口临时不可用（HTTP {status}），已自动退避重试仍失败",
            classify_http_status(status),
        )
    if status != 200:
        raise _WechatError(
            f"元宝解析接口异常: HTTP {status}", classify_http_status(status)
        )
    data = (resp.json() or {}).get("data") or {}
    export_id = data.get("wx_export_id") or ""
    token, eid = "", export_id
    playable = data.get("playable_url") or ""
    if playable:
        from urllib.parse import parse_qs, urlsplit

        query = parse_qs(urlsplit(playable).query)
        token = (query.get("token") or [""])[0]
        eid = query.get("eid") or [export_id]
        eid = eid[0] if eid else ""
    if not eid:
        raise _WechatError(
            "元宝解析未返回 export id（分享链接可能已失效或被删除）",
            ErrorVerdict(False, CONTENT_UNAVAILABLE, user_message_for(CONTENT_UNAVAILABLE)),
        )
    return eid, token


async def _wechat_feed_info(eid: str, token: str) -> dict:
    """调用视频号 finder-preview feed 接口取媒体信息（无需 cookie，但需 eid+token）。"""
    import random
    import time
    from urllib.parse import quote

    rid = f"{int(time.time()):x}-" + "".join(random.choice("0123456789abcdef") for _ in range(8))
    api = (
        f"{_WECHAT_FEED_URL}?_rid={rid}"
        "&_pageUrl=https%3A%2F%2Fchannels.weixin.qq.com%2Ffinder-preview%2Fpages%2Ffeed"
    )
    referer = (
        "https://channels.weixin.qq.com/finder-preview/pages/feed"
        f"?entry_card_type=48&comment_scene=39&appid=0"
        f"&token={quote(token)}&entry_scene=0&eid={quote(eid)}"
    )

    async def factory():
        async with httpx.AsyncClient(timeout=WECHAT_API_TIMEOUT) as client:
            return await client.post(
                api,
                json={"baseReq": {"generalToken": token}, "exportId": eid},
                headers={**_WECHAT_FEED_HEADERS, "Referer": referer},
            )

    try:
        resp = await retry_with_backoff(
            factory, attempts=WECHAT_API_ATTEMPTS,
            platform="wechat", url=api, label="wechat_feed",
        )
    except httpx.HTTPError as exc:
        raise _WechatError(
            f"视频号 feed 接口请求失败：{exc}", classify_exception(exc, platform="wechat")
        ) from exc
    status = resp.status_code
    if status == 429 or 500 <= status <= 599:
        raise _WechatError(
            f"视频号接口临时不可用（HTTP {status}），已自动退避重试仍失败",
            classify_http_status(status),
        )
    if status not in (200, 201):
        raise _WechatError(
            f"视频号接口异常: HTTP {status}", classify_http_status(status)
        )
    result = resp.json()
    if result.get("errCode"):
        raise _WechatError(
            f"视频号接口错误: {_plain_text(result.get('errMsg'))}",
            ErrorVerdict(False, CONTENT_UNAVAILABLE, user_message_for(CONTENT_UNAVAILABLE)),
        )
    detail = (result.get("data") or {}).get("errMsg") or {}
    title = _plain_text(detail.get("title"))
    content = _plain_text(detail.get("content"))
    if detail.get("type") or title or content:
        raise _WechatError(
            content and f"{title}: {content}" or title or "内容无法播放（可能为回放或已下架）",
            ErrorVerdict(False, CONTENT_UNAVAILABLE, user_message_for(CONTENT_UNAVAILABLE)),
        )
    return result


def _walk_collect_video_media(node: Any, found: list[dict] | None = None) -> list[dict]:
    """递归收集 feed JSON 中的视频媒体项（老版 mediaList / 新版 videoUrl 均兼容）。"""
    if found is None:
        found = []
    if isinstance(node, dict):
        url = None
        for key in ("mediaUrl", "videoUrl", "url"):
            value = node.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                url = value
                break
        if url and any(
            key in node for key in ("decodeKey", "fileType", "mediaUrl", "videoUrl", "spec")
        ):
            token = node.get("urlToken") or ""
            if token and token not in url:
                url = url + token
            decode_key = node.get("decodeKey")
            try:
                decode_key = int(str(decode_key)) if decode_key not in (None, "") else None
            except (TypeError, ValueError):
                decode_key = None
            found.append({"url": url, "decode_key": decode_key})
        for value in node.values():
            _walk_collect_video_media(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk_collect_video_media(value, found)
    return found


def _feed_meta(feed: dict) -> dict:
    """从 feed JSON 提取标题/作者/封面/时长。"""
    meta: dict[str, Any] = {}

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if "description" in node:
                meta.setdefault("title", _plain_text(node.get("description"))[:120])
                for key in ("nickname", "objectNickname", "userName"):
                    if node.get(key):
                        meta.setdefault("uploader", str(node[key]))
                        break
                for key in ("coverUrl", "coverImgUrl", "thumbUrl"):
                    if node.get(key):
                        meta.setdefault("thumbnail", str(node[key]))
                        break
            if node.get("nickname") and "headImgUrl" in node:
                meta.setdefault("uploader", str(node["nickname"]))
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(feed)
    return {k: v for k, v in meta.items() if v}


def _ffprobe_has_video(path: Path) -> bool:
    """ffprobe 验证文件含视频流；不可用时退化为容器签名弱验证。"""
    if not path.exists() or path.stat().st_size < 10240:
        return False
    try:
        proc = subprocess.run(
            [ffbin.ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
            capture_output=True, timeout=30,
        )
        if proc.returncode == 0:
            return bool(proc.stdout.strip())
    except FileNotFoundError:
        with open(path, "rb") as f:
            head = f.read(8)
        return len(head) >= 8 and head[4:8] == b"ftyp"
    except Exception:  # noqa: BLE001
        return False
    return False


def _isaac64_mix(a, b, c, d, e, f, g, h):
    a = (a - e) & _U64
    f ^= h >> 9
    h = (h + a) & _U64
    b = (b - f) & _U64
    g ^= (a << 9) & _U64
    a = (a + b) & _U64
    c = (c - g) & _U64
    h ^= b >> 23
    b = (b + c) & _U64
    d = (d - h) & _U64
    a ^= (c << 15) & _U64
    c = (c + d) & _U64
    e = (e - a) & _U64
    b ^= d >> 14
    d = (d + e) & _U64
    f = (f - b) & _U64
    c ^= (e << 20) & _U64
    e = (e + f) & _U64
    g = (g - c) & _U64
    d ^= f >> 17
    f = (f + g) & _U64
    h = (h - d) & _U64
    e ^= (g << 14) & _U64
    g = (g + h) & _U64
    return a, b, c, d, e, f, g, h


def _isaac64_keystream(key: int):
    """按 WechatSphDecrypt 的 ISAAC64 实现生成密钥流（每数 XOR 8 字节，大端）。"""
    golden = 0x9E3779B97F4A7C13
    seed = [0] * 256
    seed[0] = key & _U64
    mm = [0] * 256
    a = b = c = d = e = f = g = h = golden
    for _ in range(4):
        a, b, c, d, e, f, g, h = _isaac64_mix(a, b, c, d, e, f, g, h)
    for i in range(0, 256, 8):
        a = (a + seed[i]) & _U64
        b = (b + seed[i + 1]) & _U64
        c = (c + seed[i + 2]) & _U64
        d = (d + seed[i + 3]) & _U64
        e = (e + seed[i + 4]) & _U64
        f = (f + seed[i + 5]) & _U64
        g = (g + seed[i + 6]) & _U64
        h = (h + seed[i + 7]) & _U64
        a, b, c, d, e, f, g, h = _isaac64_mix(a, b, c, d, e, f, g, h)
        mm[i:i + 8] = [a, b, c, d, e, f, g, h]
    for i in range(0, 256, 8):
        a = (a + mm[i]) & _U64
        b = (b + mm[i + 1]) & _U64
        c = (c + mm[i + 2]) & _U64
        d = (d + mm[i + 3]) & _U64
        e = (e + mm[i + 4]) & _U64
        f = (f + mm[i + 5]) & _U64
        g = (g + mm[i + 6]) & _U64
        h = (h + mm[i + 7]) & _U64
        a, b, c, d, e, f, g, h = _isaac64_mix(a, b, c, d, e, f, g, h)
        mm[i:i + 8] = [a, b, c, d, e, f, g, h]

    state = {"aa": 0, "bb": 0, "cc": 0}

    def _refill():
        state["cc"] = (state["cc"] + 1) & _U64
        state["bb"] = (state["bb"] + state["cc"]) & _U64
        aa, bb = state["aa"], state["bb"]
        for i in range(256):
            if i % 4 == 0:
                aa = ~(aa ^ ((aa << 21) & _U64)) & _U64
            elif i % 4 == 1:
                aa = (aa ^ (aa >> 5)) & _U64
            elif i % 4 == 2:
                aa = (aa ^ ((aa << 12) & _U64)) & _U64
            else:
                aa = (aa ^ (aa >> 33)) & _U64
            aa = (aa + mm[(i + 128) % 256]) & _U64
            x = mm[i]
            y = (mm[(x >> 3) % 256] + aa + bb) & _U64
            mm[i] = y
            bb = (mm[(y >> 11) % 256] + x) & _U64
            seed[i] = bb
        state["aa"], state["bb"] = aa, bb

    _refill()
    rand_cnt = 255
    while True:
        result = seed[rand_cnt]
        if rand_cnt == 0:
            _refill()
            rand_cnt = 255
        else:
            rand_cnt -= 1
        yield result


def _decrypt_wechat_media_head(path: Path, key: int, enc_len: int = _WECHAT_ENC_LIMIT) -> None:
    """就地解密文件头 enc_len 字节（ISAAC64 密钥流 XOR，8 字节对齐）。"""
    size = path.stat().st_size
    span = min(size, enc_len) // 8 * 8
    if span <= 0:
        return
    with open(path, "r+b") as f:
        head = f.read(span)
        out = bytearray(span)
        i = 0
        for rand_number in _isaac64_keystream(key):
            if i >= span:
                break
            stream = rand_number.to_bytes(8, "big")
            for j in range(8):
                if i + j >= span:
                    break
                out[i + j] = head[i + j] ^ stream[j]
            i += 8
        f.seek(0)
        f.write(bytes(out))


async def _download_wechat(url: str, work_dir: Path) -> dict[str, Any]:
    """视频号下载：元宝解析换直链 → 流式下载到 ``.part`` 临时文件 →
    ffprobe 校验（带 decodeKey 则解密头部）通过后再定名。
    """
    m = re.search(r"weixin\.qq\.com/sph/([A-Za-z0-9_\-]+)", url, re.IGNORECASE)
    vid = m.group(1) if m else ""
    final_path = work_dir / f"{vid or 'wechat_video'}.mp4"
    part_path = final_path.with_name(f"{final_path.stem}.part.mp4")

    try:
        cookie = _wechat_cookie_text()
    except _WechatError as exc:
        verdict = exc.verdict or ErrorVerdict(False, AUTH_EXPIRED, str(exc))
        raise DownloadBlocked(
            str(exc),
            detail=_failure_detail(
                platform="wechat", verdict=verdict, attempts=0, last_error=str(exc)
            ),
        ) from exc

    try:
        eid, token = await _wechat_parse_share(cookie, url)
        feed = await _wechat_feed_info(eid, token)
    except _WechatError as exc:
        raw = exc.verdict or classify_exception(exc, platform="wechat")
        # 子环节内部已完成退避；对外作为最终失败结论落证据（retryable=False）
        final_verdict = ErrorVerdict(False, raw.category, raw.user_message)
        raise DownloadBlocked(
            str(exc),
            detail=_failure_detail(
                platform="wechat", verdict=final_verdict,
                attempts=WECHAT_API_ATTEMPTS, last_error=str(exc),
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001 退避耗尽后的底层 httpx 异常也收口为结构化失败
        raw = classify_exception(exc, platform="wechat")
        final_verdict = ErrorVerdict(False, raw.category, raw.user_message)
        raise DownloadBlocked(
            final_verdict.user_message,
            detail=_failure_detail(
                platform="wechat", verdict=final_verdict,
                attempts=WECHAT_API_ATTEMPTS, last_error=str(exc),
            ),
        ) from exc

    meta = _feed_meta(feed)
    candidates: list[dict] = []
    seen: set[str] = set()
    for item in _walk_collect_video_media(feed):
        if item["url"] not in seen:
            seen.add(item["url"])
            candidates.append(item)
    if not candidates:
        verdict = ErrorVerdict(
            False, CONTENT_UNAVAILABLE, user_message_for(CONTENT_UNAVAILABLE)
        )
        raise DownloadBlocked(
            "元宝解析成功但未在视频号接口返回中找到视频流（可能为直播/回放内容）",
            detail=_failure_detail(
                platform="wechat", verdict=verdict, attempts=1,
                last_error="feed JSON 中无可用媒体地址",
            ),
        )

    async def fetch_candidate(cand: dict[str, Any]) -> None:
        """下载单个候选源到 part 文件，校验通过后替换为最终 mp4。"""
        media_url = cand["url"]
        decode_key = cand["decode_key"]
        part_path.unlink(missing_ok=True)

        async def factory():
            if media_url.lower().split("?")[0].endswith(".m3u8"):
                proc = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [ffbin.ffmpeg_bin(), "-y", "-i", media_url, "-c", "copy",
                         "-bsf:a", "aac_adtstoasc", str(part_path)],
                        capture_output=True, timeout=600,
                    ),
                )
                if proc.returncode != 0:
                    raise RuntimeError("m3u8 下载失败（可能为加密流或流中断）")
            else:
                headers = {
                    "User-Agent": BILI_UA,
                    "Referer": "https://weixin.qq.com/",
                }
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(MEDIA_READ_TIMEOUT, connect=MEDIA_CONNECT_TIMEOUT)
                ) as client:
                    async with client.stream("GET", media_url, headers=headers) as resp:
                        resp.raise_for_status()
                        with open(part_path, "wb") as f:
                            async for chunk in resp.aiter_bytes(chunk_size=1 << 20):
                                f.write(chunk)

        await retry_with_backoff(
            factory, attempts=WECHAT_STREAM_ATTEMPTS,
            platform="wechat", url=media_url, label="wechat_stream",
        )
        # 常规校验失败且带 decodeKey → 解密文件头后重验
        if not _ffprobe_has_video(part_path) and decode_key:
            _decrypt_wechat_media_head(part_path, decode_key)
        if not _ffprobe_has_video(part_path):
            raise RuntimeError("候选流校验失败：ffprobe 未检出视频流（空文件或截断流）")
        part_path.replace(final_path)

    errors: list[str] = []
    last_verdict: ErrorVerdict | None = None
    for cand in candidates:
        try:
            await fetch_candidate(cand)
            cookies.mark_success("wechat")
            logger.info("视频号下载完成: %s", final_path)
            return {"video_path": str(final_path), "meta": meta}
        except Exception as exc:  # noqa: BLE001 换下一个候选源，单源失败不崩 worker
            last_verdict = classify_exception(exc, platform="wechat")
            errors.append(f"{str(exc)[:120]} ({cand['url'][:60]})")
            logger.warning("视频号候选源失败: %s", exc)
            part_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)

    raw_verdict = last_verdict or ErrorVerdict(
        False, CONTENT_UNAVAILABLE, user_message_for(CONTENT_UNAVAILABLE)
    )
    # 所有候选均失败：退避已在每路执行，对外作为最终失败落证据
    final_verdict = ErrorVerdict(False, raw_verdict.category, raw_verdict.user_message)
    raise DownloadBlocked(
        f"视频号下载失败（{'；'.join(errors[-2:])}）。可尝试到「采集账号」页更新元宝 Cookie 后重试。",
        detail=_failure_detail(
            platform="wechat",
            verdict=final_verdict,
            attempts=WECHAT_STREAM_ATTEMPTS,
            last_error=" | ".join(errors[-2:]),
            chain=[{"source": "candidate_feeds", "errors": errors[-3:]}],
        ),
    )

