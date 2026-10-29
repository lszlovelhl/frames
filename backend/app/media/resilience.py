"""采集链路韧性层：错误分类 + 指数退避重试（纯标准库 + httpx，不引入新依赖）。

设计原则
--------
1. 先分类、再决策：``classify_exception`` 把异常归一为
   ``{retryable, category, user_message}``，上层（downloader / 队列落库）只消费结论。
2. 可重试（retryable=True）：连接失败、读/连接超时、HTTP 429、HTTP 5xx、
   下载流中断等暂时性故障，走指数退避 + 随机抖动重试。
3. 永久失败（retryable=False）：登录失效（401/403）、内容下架/404/地区限制、
   DownloadBlocked（平台不支持取流）、参数错误（链接无法解析），立即上抛，
   不做无意义重试，避免烧 token / 烧磁盘。
4. 退避只用 ``asyncio.sleep`` + ``random``，不依赖 tenacity 等第三方库；
   每次尝试失败必须写 ``logger.warning``（平台 / 脱敏 URL / 第几次 / 类别），
   禁止裸 ``except: pass``。

分类矩阵（测试见 tests/test_resilience.py）
------------------------------------------
| category            | retryable | 典型触发                                  |
|---------------------|-----------|-------------------------------------------|
| network_timeout     | True      | httpx.TimeoutException / asyncio.TimeoutError |
| connection_error    | True      | httpx.ConnectError / NetworkError / ECONNRESET |
| rate_limited        | True      | HTTP 429 / 平台限流文案（too many requests）|
| server_error        | True      | HTTP 5xx                                  |
| stream_interrupted  | True      | 流读取中断 / 截断产物 / 空文件            |
| auth_expired        | False     | HTTP 401/403 / 登录态失效关键词           |
| content_unavailable | False     | HTTP 404 / 已删除 / 私密 / 地区限制       |
| content_blocked     | False     | DownloadBlocked / 平台不支持取流          |
| bad_request         | False     | 链接无法解析 / 参数错误                   |
| unknown             | False     | 未识别异常（默认不重试，由上层降级链兜底）|
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

# ---------- 分类常量（魔法数集中在此，禁止散落字面量） ----------

NETWORK_TIMEOUT = "network_timeout"
CONNECTION_ERROR = "connection_error"
RATE_LIMITED = "rate_limited"
SERVER_ERROR = "server_error"
STREAM_INTERRUPTED = "stream_interrupted"
AUTH_EXPIRED = "auth_expired"
CONTENT_UNAVAILABLE = "content_unavailable"
CONTENT_BLOCKED = "content_blocked"
BAD_REQUEST = "bad_request"
UNKNOWN = "unknown"

# 可重试类别集合（新增类别时在此登记，避免 if 分支漂移）
RETRYABLE_CATEGORIES: frozenset[str] = frozenset(
    {NETWORK_TIMEOUT, CONNECTION_ERROR, RATE_LIMITED, SERVER_ERROR, STREAM_INTERRUPTED}
)

# 退避参数默认值：3 次尝试、基准 1s、上限 15s（2^n * base，全抖动）
DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 15.0

# 日志中错误文本截断长度，避免 cookie / 长堆栈刷屏
MAX_ERROR_TEXT = 300

# yt-dlp / 平台文案关键词 → 类别（小写匹配；顺序即优先级：内容属性类先于登录提示类，
# 避免 "Private video; sign in required" 被 "sign in" 抢判为登录失效）
_KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        CONTENT_UNAVAILABLE,
        (
            "404", "not found", "deleted", "removed", "private video",
            "members-only", "member-only", "age-restricted", "not available in your country",
            "region", "copyright", "unavailable", "requested format is not available",
            "已删除", "下架", "私密", "地区限制", "仅会员", "不可见", "稿件不存在",
        ),
    ),
    (
        AUTH_EXPIRED,
        (
            "401", "403", "sign in", "log in", "login required",
            "登录", "未登录", "cookie 失效", "登录态", "风控",
        ),
    ),
    (
        RATE_LIMITED,
        ("429", "too many requests", "rate limit", "请求频繁", "限流"),
    ),
    (
        SERVER_ERROR,
        ("500", "502", "503", "504", "internal server error", "bad gateway", "service unavailable"),
    ),
    (
        NETWORK_TIMEOUT,
        ("timed out", "timeout", "超时", "read timeout", "connect timeout"),
    ),
    (
        CONNECTION_ERROR,
        (
            "connection", "connecterror", "network is unreachable", "reset by peer",
            "eof while reading", "connection aborted", "connection refused", "无法连接",
        ),
    ),
    (
        STREAM_INTERRUPTED,
        ("incomplete", "truncated", "unexpected eof", "下载中断", "截断", "空文件"),
    ),
    (
        BAD_REQUEST,
        ("invalid url", "unsupported url", "no video found", "无法解析", "参数错误"),
    ),
)

_USER_MESSAGES: dict[str, str] = {
    NETWORK_TIMEOUT: "网络连接超时（平台接口繁忙或网络波动），已自动重试仍失败，请稍后重试。",
    CONNECTION_ERROR: "网络连接失败（无法建立到平台的连接），请检查网络后重试。",
    RATE_LIMITED: "请求过于频繁被平台限流，已自动退避重试，请稍后再试。",
    SERVER_ERROR: "平台服务端临时错误，已自动重试仍失败，请稍后重试。",
    STREAM_INTERRUPTED: "视频流下载中断或文件不完整，已自动重试仍失败，请稍后重试。",
    AUTH_EXPIRED: "登录态已失效或被平台风控拦截，请到「采集账号」页更新对应平台 Cookie 后重试。",
    CONTENT_UNAVAILABLE: "视频不可用：可能已删除、设为私密、仅会员可见或受地区限制。",
    CONTENT_BLOCKED: "当前平台暂不支持自动取流，需要完成平台授权或手动上传本地文件。",
    BAD_REQUEST: "链接无法解析，请确认粘贴的是完整、有效的视频分享链接。",
    UNKNOWN: "下载失败，已自动重试仍未成功；若反复出现可手动上传本地文件。",
}


@dataclass(frozen=True)
class ErrorVerdict:
    """异常分类结论。"""

    retryable: bool
    category: str
    user_message: str


def user_message_for(category: str) -> str:
    return _USER_MESSAGES.get(category, _USER_MESSAGES[UNKNOWN])


def classify_http_status(status_code: int, message: str = "") -> ErrorVerdict:
    """按 HTTP 状态码分类（供 httpx 响应层 / yt-dlp 文案复用）。"""
    text = (message or "").lower()
    if status_code in (401, 403):
        return ErrorVerdict(False, AUTH_EXPIRED, user_message_for(AUTH_EXPIRED))
    if status_code == 429:
        return ErrorVerdict(True, RATE_LIMITED, user_message_for(RATE_LIMITED))
    if 500 <= status_code <= 599:
        return ErrorVerdict(True, SERVER_ERROR, user_message_for(SERVER_ERROR))
    if status_code == 404:
        return ErrorVerdict(False, CONTENT_UNAVAILABLE, user_message_for(CONTENT_UNAVAILABLE))
    if status_code in (400, 422):
        return ErrorVerdict(False, BAD_REQUEST, user_message_for(BAD_REQUEST))
    # 无状态码但文案命中关键词时再兜一层
    return _classify_by_text(text)


def _classify_by_text(text: str) -> ErrorVerdict:
    low = text.lower()
    for category, keywords in _KEYWORD_RULES:
        if any(kw in low for kw in keywords):
            return ErrorVerdict(
                category in RETRYABLE_CATEGORIES, category, user_message_for(category)
            )
    return ErrorVerdict(False, UNKNOWN, user_message_for(UNKNOWN))


def classify_exception(exc: BaseException, *, platform: str = "") -> ErrorVerdict:
    """把任意采集异常归一为分类结论。

    顺序：自定义 verdict 附件 → DownloadBlocked → httpx 类型 → HTTP 状态码 →
    yt-dlp / 文案关键词 → 默认 unknown（不重试）。
    """
    # 1) 调用方已显式标注结论（如 _WechatError 带 verdict）
    attached = getattr(exc, "verdict", None)
    if isinstance(attached, ErrorVerdict):
        return attached

    # 2) 永久失败：平台不支持取流 / 上层已判定不可下载
    from app.media.downloader import DownloadBlocked

    if isinstance(exc, DownloadBlocked):
        detail = getattr(exc, "detail", None) or {}
        category = detail.get("category") or CONTENT_BLOCKED
        return ErrorVerdict(False, category, str(exc) or user_message_for(category))

    # 3) httpx 类型层
    if isinstance(exc, httpx.TimeoutException):
        return ErrorVerdict(True, NETWORK_TIMEOUT, user_message_for(NETWORK_TIMEOUT))
    if isinstance(exc, httpx.HTTPStatusError):
        return classify_http_status(exc.response.status_code, str(exc))
    if isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError)):
        return ErrorVerdict(True, STREAM_INTERRUPTED, user_message_for(STREAM_INTERRUPTED))
    if isinstance(exc, httpx.ConnectError):
        return ErrorVerdict(True, CONNECTION_ERROR, user_message_for(CONNECTION_ERROR))
    if isinstance(exc, httpx.NetworkError):
        return ErrorVerdict(True, CONNECTION_ERROR, user_message_for(CONNECTION_ERROR))

    # 4) 标准库超时 / 连接类 OSError
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ErrorVerdict(True, NETWORK_TIMEOUT, user_message_for(NETWORK_TIMEOUT))
    if isinstance(exc, ConnectionError):
        return ErrorVerdict(True, CONNECTION_ERROR, user_message_for(CONNECTION_ERROR))
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in (
        54,  # ECONNRESET
        61,  # ECONNREFUSED
        64,  # ENETDOWN
        101,  # ENETUNREACH
    ):
        return ErrorVerdict(True, CONNECTION_ERROR, user_message_for(CONNECTION_ERROR))

    # 5) 文案关键词层（覆盖 yt-dlp DownloadError / B 站 API 错误 / 自定义异常）
    verdict = _classify_by_text(str(exc))
    if verdict.category != UNKNOWN:
        return verdict

    # 6) 默认：未知异常不自动重试（由平台降级链兜底；永久失败语义更安全）
    logger.warning("未识别的采集异常类型=%s platform=%s", type(exc).__name__, platform)
    return ErrorVerdict(False, UNKNOWN, user_message_for(UNKNOWN))


def reason_code(platform: str, category: str) -> str:
    """落库用短类别码：``{platform}_{category}``，只保留安全字符。"""
    base = f"{platform or 'media'}_{category or UNKNOWN}"
    return re.sub(r"[^a-z0-9_]", "_", base.lower())[:48]


def mask_url(url: str) -> str:
    """日志脱敏：去掉 query（token / sign / cookie 参数），只留协议+域名+路径前段。"""
    if not url:
        return ""
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(url)
        path = parts.path[:48]
        netloc = parts.netloc[:48]
        return urlunsplit((parts.scheme, netloc, path, "", ""))
    except Exception:  # noqa: BLE001 脱敏失败宁可只留长度信息
        logger.debug("URL 脱敏失败", exc_info=True)
        return f"<url len={len(url)}>"


async def retry_with_backoff(
    factory: Callable[[], Awaitable[Any]],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    platform: str = "",
    url: str = "",
    label: str = "",
    out: dict[str, Any] | None = None,
) -> Any:
    """指数退避 + 全抖动重试。

    - ``factory``：每次尝试都重新调用（返回协程），保证无状态可重入；
    - 仅对 ``retryable=True`` 的分类重试；永久失败立即抛出原异常；
    - 退避时长 ``min(max_delay, base * 2**(n-1))`` 后乘 ``uniform(0, delay)`` 全抖动；
    - ``out``：可选回传 ``{"attempts": 实际尝试次数}`` 供落库 evidence；
    - 每次失败写 warning 日志，最终失败抛出最后一个异常（保留 traceback 链）。
    """
    if attempts < 1:
        raise ValueError("attempts 必须 >= 1")
    masked = mask_url(url)
    last_exc: BaseException | None = None

    for attempt in range(1, attempts + 1):
        try:
            result = await factory()
            if out is not None:
                out["attempts"] = attempt
            if attempt > 1:
                logger.info(
                    "采集重试成功 platform=%s label=%s url=%s attempts=%d",
                    platform, label, masked, attempt,
                )
            return result
        except Exception as exc:  # noqa: BLE001 分类后决定重试或抛出；CancelledError 不拦截
            last_exc = exc
            verdict = classify_exception(exc, platform=platform)
            err_text = str(exc)[:MAX_ERROR_TEXT]
            logger.warning(
                "采集失败 platform=%s label=%s url=%s attempt=%d/%d category=%s retryable=%s error=%s",
                platform, label, masked, attempt, attempts, verdict.category,
                verdict.retryable, err_text,
            )
            if not verdict.retryable or attempt == attempts:
                if out is not None:
                    out["attempts"] = attempt
                raise
            delay = min(max_delay, base * (2 ** (attempt - 1)))
            sleep_s = random.uniform(0, delay)
            await asyncio.sleep(sleep_s)

    # 理论上不可达（循环内要么 return 要么 raise），防御性兜底
    if out is not None:
        out["attempts"] = attempts
    raise RuntimeError("retry_with_backoff 退出但无异常可抛") from last_exc
