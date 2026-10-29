"""采集韧性层单测：错误分类矩阵 + 指数退避重试（纯离线，不打任何网络）。

覆盖：
1. ``classify_exception`` / ``classify_http_status`` 分类矩阵：
   超时 / 连接错误 / 429 / 5xx → 可重试；401/403 / 404 / DownloadBlocked → 永久；
2. ``retry_with_backoff``：可重试错误按次数重试、永久错误不重试、成功直接返回；
3. ``mask_url`` 日志脱敏、``reason_code`` 短类别码格式。
"""
from __future__ import annotations

import pytest

import httpx

from app.media import resilience
from app.media.downloader import DownloadBlocked


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.com/video")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


# ---------------- 分类矩阵 ----------------

@pytest.mark.parametrize(
    "exc, expected_category, expected_retryable",
    [
        (httpx.TimeoutException("read timed out"), resilience.NETWORK_TIMEOUT, True),
        (httpx.ConnectError("connection refused"), resilience.CONNECTION_ERROR, True),
        (httpx.RemoteProtocolError("stream truncated"), resilience.STREAM_INTERRUPTED, True),
        (ConnectionError("reset by peer"), resilience.CONNECTION_ERROR, True),
        (_status_error(429), resilience.RATE_LIMITED, True),
        (_status_error(500), resilience.SERVER_ERROR, True),
        (_status_error(503), resilience.SERVER_ERROR, True),
        (_status_error(504), resilience.SERVER_ERROR, True),
        (_status_error(401), resilience.AUTH_EXPIRED, False),
        (_status_error(403), resilience.AUTH_EXPIRED, False),
        (_status_error(404), resilience.CONTENT_UNAVAILABLE, False),
        (DownloadBlocked("小红书无公开取流"), resilience.CONTENT_BLOCKED, False),
        (Exception("ERROR: [youtube] Private video; sign in required"),
         resilience.CONTENT_UNAVAILABLE, False),
        (Exception("ERROR: 429 Too Many Requests"), resilience.RATE_LIMITED, True),
        (Exception("Requested format is not available"),
         resilience.CONTENT_UNAVAILABLE, False),
        (Exception("视频已删除"), resilience.CONTENT_UNAVAILABLE, False),
        (Exception("无法解析 BV 号"), resilience.BAD_REQUEST, False),
        (RuntimeError("completely unknown thing"), resilience.UNKNOWN, False),
    ],
)
def test_classify_matrix(exc: BaseException, expected_category: str, expected_retryable: bool):
    verdict = resilience.classify_exception(exc, platform="test")
    assert verdict.category == expected_category
    assert verdict.retryable is expected_retryable
    assert verdict.user_message  # 永久/可重试都必须有人话提示


def test_download_blocked_detail_category_is_respected():
    exc = DownloadBlocked("被风控", detail={"category": resilience.AUTH_EXPIRED})
    verdict = resilience.classify_exception(exc)
    assert verdict.retryable is False
    assert verdict.category == resilience.AUTH_EXPIRED


def test_http_status_direct():
    assert resilience.classify_http_status(429).retryable is True
    assert resilience.classify_http_status(502).retryable is True
    assert resilience.classify_http_status(403).retryable is False
    assert resilience.classify_http_status(404).retryable is False


# ---------------- 退避重试 ----------------

async def _noop_sleep(_seconds: float) -> None:
    return None


async def test_retryable_error_retries_then_raises(monkeypatch):
    monkeypatch.setattr(resilience.asyncio, "sleep", _noop_sleep)
    calls: list[int] = []

    async def factory():
        calls.append(1)
        raise httpx.ConnectError("network down")

    out: dict = {}
    with pytest.raises(httpx.ConnectError):
        await resilience.retry_with_backoff(
            factory, attempts=3, base=0.0, out=out, platform="bilibili", url="https://b.tv/x"
        )
    assert len(calls) == 3
    assert out["attempts"] == 3


async def test_permanent_error_not_retried(monkeypatch):
    monkeypatch.setattr(resilience.asyncio, "sleep", _noop_sleep)
    calls: list[int] = []

    async def factory():
        calls.append(1)
        raise DownloadBlocked("小红书不支持自动下载")

    out: dict = {}
    with pytest.raises(DownloadBlocked):
        await resilience.retry_with_backoff(factory, attempts=3, base=0.0, out=out)
    assert len(calls) == 1  # 永久失败立即抛出，不做无意义重试
    assert out["attempts"] == 1


async def test_success_returns_value_without_retry(monkeypatch):
    slept: list[float] = []

    async def track_sleep(seconds: float):
        slept.append(seconds)

    monkeypatch.setattr(resilience.asyncio, "sleep", track_sleep)
    calls: list[int] = []

    async def factory():
        calls.append(1)
        return {"ok": True}

    out: dict = {}
    result = await resilience.retry_with_backoff(factory, attempts=3, out=out)
    assert result == {"ok": True}
    assert len(calls) == 1
    assert slept == []  # 成功不 sleep
    assert out["attempts"] == 1


async def test_permanent_403_not_retried(monkeypatch):
    monkeypatch.setattr(resilience.asyncio, "sleep", _noop_sleep)
    calls: list[int] = []

    async def factory():
        calls.append(1)
        raise _status_error(403)

    with pytest.raises(httpx.HTTPStatusError):
        await resilience.retry_with_backoff(factory, attempts=3, base=0.0)
    assert len(calls) == 1


# ---------------- 工具函数 ----------------

def test_mask_url_strips_query():
    masked = resilience.mask_url("https://api.bilibili.com/x/player/playurl?token=abc&sign=zz")
    assert "token" not in masked
    assert "sign" not in masked
    assert masked.startswith("https://api.bilibili.com/")


def test_mask_url_empty_safe():
    assert resilience.mask_url("") == ""


def test_reason_code_format():
    code = resilience.reason_code("bilibili", resilience.NETWORK_TIMEOUT)
    assert code == "bilibili_network_timeout"
    assert resilience.reason_code("", resilience.UNKNOWN) == "media_unknown"
