"""平台登录态（cookies）管理。

- 存储：{cookie_dir}/{platform}.cookies.txt（Netscape 格式，yt-dlp 可直接读取）
- 状态：{cookie_dir}/state.json
    status: none / imported / valid / expired
      none     未导入过登录态
      imported 已保存 cookie，尚未经抓取验证
      valid    最近一次抓取成功
      expired  抓取遇 401/403（登录失效）或 cookie 超过有效期阈值
- 定期巡检：periodic_check() 每小时由 lifespan 调用一次，
  把超过 STALE_DAYS 未成功使用的 cookie 置 expired（提示重新导入）。
  真实有效性由抓取链路回写：下载成功 → valid；登录失效错误 → expired。
"""
import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# cookie 存储根目录（帧间 data 下）
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
COOKIE_DIR = DATA_DIR / "cookies"
STATE_FILE = COOKIE_DIR / "state.json"
STALE_DAYS = 90  # cookie 超过该天数未成功使用视为过期

PLATFORMS = [
    {"platform": "bilibili", "label": "哔哩哔哩", "login_url": "https://passport.bilibili.com/login", "need_login": False, "downloadable": True},
    {"platform": "douyin", "label": "抖音", "login_url": "https://www.douyin.com/", "need_login": True, "downloadable": True},
    {"platform": "youtube", "label": "YouTube", "login_url": "https://accounts.google.com/Login", "need_login": False, "downloadable": True},
    {"platform": "xiaohongshu", "label": "小红书", "login_url": "https://www.xiaohongshu.com", "need_login": True, "downloadable": False},
    {"platform": "wechat", "label": "微信视频号", "login_url": "https://yuanbao.tencent.com", "need_login": True, "downloadable": True},
]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state: dict) -> None:
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def cookie_path(platform: str) -> Path:
    return COOKIE_DIR / f"{platform}.cookies.txt"


def _cookie_mtime(platform: str) -> int | None:
    p = cookie_path(platform)
    if p.exists() and p.stat().st_size > 0:
        return int(p.stat().st_mtime)
    return None


def _basic_structure_ok(platform: str) -> bool:
    """结构性探测：cookie 文件是否有该平台登录凭证字段。"""
    p = cookie_path(platform)
    if not p.exists():
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return False
    keys = {
        "douyin": ("sessionid", "passport_csrf_token", "ttwid"),
        "xiaohongshu": ("web_session", "a1", "webId"),
        "bilibili": ("SESSDATA", "bili_jct", "buvid3"),
        "youtube": ("SID", "HSID", "SSID", "__Secure-1PSID"),
        "wechat": ("wxsid", "data_ticket", "hy_token"),  # hy_token 为元宝解析接口登录态字段
    }
    needles = keys.get(platform, ())
    return any(k in text for k in needles)


def get_status(platform: str) -> dict:
    st = _load_state().get(platform) or {}
    mtime = _cookie_mtime(platform)
    status = st.get("status", "none") if mtime else "none"
    if status in ("imported", "valid") and not _basic_structure_ok(platform):
        status = "expired"
        st = {**st, "status": status, "note": "cookie 未识别到登录凭证字段"}
        state = _load_state()
        state[platform] = st
        _save_state(state)
    updated = st.get("updated_at")
    if mtime and updated is None:
        updated = datetime.fromtimestamp(mtime, tz=UTC).isoformat()
    return {
        "platform": platform,
        "status": status,
        "note": st.get("note", ""),
        "last_success_at": st.get("last_success_at"),
        "last_fail_at": st.get("last_fail_at"),
        "updated_at": updated,
        "has_cookie": bool(mtime),
    }


def list_status() -> list[dict]:
    out = []
    for p in PLATFORMS:
        s = get_status(p["platform"])
        s.update({k: v for k, v in p.items()})
        out.append(s)
    return out


def _ensure_netscape(text: str) -> str:
    """兼容输入：若为浏览器扩展导出的 JSON 数组（EditThisCookie 等），转换为 Netscape 格式。"""
    s = text.lstrip()
    if not s.startswith("["):
        return text
    try:
        arr = json.loads(text)
    except json.JSONDecodeError:
        return text
    lines = ["# Netscape HTTP Cookie File"]
    for c in arr:
        domain = str(c.get("domain", "")).strip()
        if not domain or not c.get("name"):
            continue
        host_only = c.get("hostOnly")
        # Netscape 规则：includeSubdomains(TRUE) 的域须以 . 开头，否则不带点
        if host_only is True:
            domain = domain.lstrip(".")
            include_flag = "FALSE"
        else:
            if not domain.startswith("."):
                domain = "." + domain.lstrip(".")
            include_flag = "TRUE"
        secure = bool(c.get("secure"))
        exp = c.get("expirationDate") or c.get("expires") or 0
        try:
            exp_i = int(float(exp))
        except (TypeError, ValueError):
            exp_i = 0
        lines.append(
            "\t".join(
                [
                    domain,
                    include_flag,
                    str(c.get("path") or "/"),
                    "TRUE" if secure else "FALSE",
                    str(exp_i),
                    str(c.get("name", "")),
                    str(c.get("value", "")),
                ]
            )
        )
    return "\n".join(lines)


def save_cookies(platform: str, text: str) -> dict:
    """保存/更新登录态（Netscape cookies.txt 文本，兼容浏览器扩展导出的 JSON 数组自动转换）。"""
    if platform not in {p["platform"] for p in PLATFORMS}:
        raise ValueError(f"不支持平台 {platform}")
    text = _ensure_netscape(text).strip()
    if not text:
        raise ValueError("内容为空")
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    cookie_path(platform).write_text(text, encoding="utf-8")

    state = _load_state()
    rec = {
        "status": "imported",
        "note": "已保存登录态，待抓取验证",
        "updated_at": _now(),
        "last_success_at": None,
        "last_fail_at": None,
    }
    if not _basic_structure_ok(platform):
        rec["status"] = "expired"
        rec["note"] = "未识别到该平台登录凭证字段，请确认导出的是 Netscape 格式 cookies（需含该平台登录后的关键 cookie）"
    state[platform] = rec
    _save_state(state)
    return get_status(platform)


def clear_cookies(platform: str) -> None:
    """清除登录态：状态置 none，cookie 内容清空（保留空文件以便状态一致）。"""
    p = cookie_path(platform)
    try:
        p.write_text("", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    state = _load_state()
    state[platform] = {"status": "none", "note": "", "updated_at": _now()}
    _save_state(state)


def mark_success(platform: str) -> None:
    state = _load_state()
    rec = state.get(platform) or {}
    rec.update({"status": "valid", "note": "最近一次抓取成功", "last_success_at": _now()})
    state[platform] = rec
    _save_state(state)


def mark_expired(platform: str, reason: str = "") -> None:
    state = _load_state()
    rec = state.get(platform) or {}
    rec.update({
        "status": "expired",
        "note": reason or "登录态失效，请重新导入",
        "last_fail_at": _now(),
    })
    state[platform] = rec
    _save_state(state)


def get_cookie_file_for(platform: str) -> str | None:
    """返回给下载器用的 cookie 文件路径；无有效登录态返回 None。"""
    st = get_status(platform)
    if st["status"] in ("valid", "imported") and st["has_cookie"]:
        return str(cookie_path(platform))
    return None


async def periodic_check() -> None:
    """定期巡检（每小时）：对超期未成功使用的登录态标 expired。"""
    while True:
        try:
            now = time.time()
            state = _load_state()
            changed = False
            for p in PLATFORMS:
                platform = p["platform"]
                rec = state.get(platform) or {}
                if rec.get("status") not in ("imported", "valid"):
                    continue
                mtime = _cookie_mtime(platform)
                if not mtime:
                    continue
                last_success = rec.get("last_success_at")
                ref = now
                if last_success:
                    try:
                        ref = datetime.fromisoformat(last_success).timestamp()
                    except Exception:  # noqa: BLE001
                        pass
                if (now - mtime) / 86400 > STALE_DAYS or (now - ref) / 86400 > STALE_DAYS:
                    rec["status"] = "expired"
                    rec["note"] = f"超过 {STALE_DAYS} 天未成功使用，请重新导入登录态"
                    rec["updated_at"] = _now()
                    changed = True
            if changed:
                _save_state(state)
        except Exception:  # noqa: BLE001
            logger.exception("登录态巡检异常")
        await asyncio.sleep(3600)


def looks_like_login_failure(msg: str) -> bool:
    """下载错误信息是否提示登录/风控失效。"""
    if not msg:
        return False
    low = msg.lower()
    return any(m in low for m in ("403", "401", "sign in", "login", "登录", "cookie", "风控", "需登录"))
