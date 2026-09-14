"""抖音真实互动指标采集。

主数据源：抖音分享页 https://www.iesdouyin.com/share/video/{aweme_id}/
页面内嵌 window._ROUTER_DATA（无需 a_bogus/X-Bogus 签名），移动 UA + 登录 cookie
即可稳定拿到 statistics：digg_count / comment_count / collect_count / share_count。

补充数据源（iesdouyin web api，同样免签名）：
- 作者粉丝/获赞：https://www.iesdouyin.com/web/api/v2/user/info/?sec_uid=...
  user_info.mplatform_followers_count = 粉丝数；total_favorited = 获赞数。
- 热评：https://www.iesdouyin.com/web/api/v2/comment/list/?aweme_id=...&cursor=0&count=20
  返回 comments 列表（nickname/text/digg_count/reply_comment_total）。
补充源失败不阻塞主链路，仅记 warnings。

注意：抖音不公开播放量，statistics.play_count 恒为 0 或缺失——
因此本采集器返回的 stats 不包含 view_count 键，上层不得把 0 当作真实播放。
"""
import json
import logging
import re
import subprocess
import time
import urllib.parse
from typing import Any

logger = logging.getLogger(__name__)

SHARE_URL = "https://www.iesdouyin.com/share/video/{aweme_id}/"
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
DOUYIN_REFERER = "https://www.douyin.com/"
AWEME_ID_RE = re.compile(r"/(?:video|note|slides)/(\d+)")
MAX_ATTEMPTS = 3  # 抖音存在瞬时风控，重试可成功（历史验证）


def cookie_header(cookies_file: str | None) -> str:
    """把 Netscape cookie 文件读成 Cookie header；无有效 cookie 返回空串。"""
    if not cookies_file:
        return ""
    try:
        text = open(cookies_file, encoding="utf-8", errors="ignore").read()
    except OSError:
        return ""
    pairs = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            name, value = parts[5].strip(), parts[6].strip()
            if name and value:
                pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def resolve_aweme_id(url: str, cookies: str = "") -> str | None:
    """从视频 URL 解析 aweme_id；短链 v.douyin.com/xxx 先展开 302。"""
    m = AWEME_ID_RE.search(url)
    if m:
        return m.group(1)
    m = re.search(r"aweme_id=(\d+)", url)
    if m:
        return m.group(1)
    if "v.douyin.com" in url:
        try:
            cmd = [
                "curl", "-s", "-I", "-m", "15", "-o", "/dev/null",
                "-w", "%{redirect_url}",
                "-A", MOBILE_UA,
                "-H", f"Referer: {DOUYIN_REFERER}",
            ]
            if cookies:
                cmd += ["-H", f"Cookie: {cookies}"]
            cmd.append(url)
            proc = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=20,
            )
            loc = (proc.stdout or "").strip()
        except (subprocess.SubprocessError, OSError):
            return None
        if loc:
            m = AWEME_ID_RE.search(loc)
            if m:
                return m.group(1)
            m = re.search(r"aweme_id=(\d+)", loc)
            if m:
                return m.group(1)
    return None


def _http_get_share(aweme_id: str, cookies: str) -> str:
    """请求分享页 HTML，返回页面源码；失败抛 RuntimeError。"""
    headers = [
        "-A", MOBILE_UA,
        "-H", f"Referer: {DOUYIN_REFERER}",
        "-H", "Accept-Language: zh-CN,zh;q=0.9",
        "--compressed",
        "-s", "-L", "-m", "20",
    ]
    if cookies:
        headers += ["-H", f"Cookie: {cookies}"]
    proc = subprocess.run(
        ["curl", *headers, SHARE_URL.format(aweme_id=aweme_id)],
        capture_output=True, text=True, timeout=30,
    )
    html = proc.stdout or ""
    if len(html) < 2000:
        raise RuntimeError("抖音分享页响应异常（疑似风控/验证码）")
    return html


def _http_get_json(url: str, cookies: str) -> dict:
    """GET 一个返回 JSON 的接口；异常/非 JSON/空响应抛 RuntimeError。"""
    headers = [
        "-A", MOBILE_UA,
        "-H", f"Referer: {DOUYIN_REFERER}",
        "-H", "Accept-Language: zh-CN,zh;q=0.9",
        "--compressed",
        "-s", "-L", "-m", "15",
    ]
    if cookies:
        headers += ["-H", f"Cookie: {cookies}"]
    proc = subprocess.run(
        ["curl", *headers, url],
        capture_output=True, text=True, timeout=25,
    )
    raw = (proc.stdout or "").strip()
    if not raw:
        raise RuntimeError("接口返回为空")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("接口返回非 JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("接口返回结构异常")
    return data


def _pick_avatar(user: dict | None) -> str | None:
    """从 user 对象挑一个可用的头像 url。"""
    if not isinstance(user, dict):
        return None
    for key in ("avatar_medium", "avatar_thumb", "avatar_larger"):
        obj = user.get(key)
        if isinstance(obj, dict):
            lst = obj.get("url_list") or []
            if lst:
                return lst[0]
    return None


def _fetch_author_info(sec_uid: str, cookies: str) -> dict | None:
    """抓作者主页信息（粉丝数/获赞数/昵称/头像）；失败返回 None。"""
    try:
        url = (
            "https://www.iesdouyin.com/web/api/v2/user/info/?sec_uid="
            + urllib.parse.quote(sec_uid, safe="")
        )
        data = _http_get_json(url, cookies)
        u = data.get("user_info") or {}
        if not u:
            return None
        fans = u.get("mplatform_followers_count")
        if fans is None:
            fans = u.get("follower_count")
        likes = u.get("total_favorited")
        try:
            fans = int(fans) if fans is not None else None
            likes = int(likes) if likes is not None else None
        except (TypeError, ValueError):
            pass
        if not isinstance(u.get("sec_uid"), str):
            return None
        return {
            "name": u.get("nickname"),
            "avatar": _pick_avatar(u),
            "uid": u.get("sec_uid") or u.get("uid") or u.get("short_id"),
            "fans": fans,
            "likes": likes,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("抖音作者信息抓取失败: %s", exc)
        return None


def _fetch_comments(aweme_id: str, cookies: str, count: int = 20) -> list[dict] | None:
    """抓取抖音热评列表（iesdouyin web api）；失败返回 None（上层不动既有热评）。"""
    try:
        url = (
            "https://www.iesdouyin.com/web/api/v2/comment/list/"
            f"?aweme_id={aweme_id}&cursor=0&count={count}"
        )
        data = _http_get_json(url, cookies)
        arr = data.get("comments")
        if not isinstance(arr, list):
            return None
        out: list[dict] = []
        seen: set[str] = set()
        for c in arr[:count]:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("cid") or "")
            if not cid or cid in seen:
                continue  # 接口偶发重复返回同一条（第一页边界），去重防自撞唯一键
            seen.add(cid)
            u = c.get("user") or {}
            if not isinstance(u, dict):
                u = {}
            out.append(
                {
                    "comment_id": cid,
                    "user_id": str(
                        u.get("uid") or u.get("unique_id") or u.get("short_id") or ""
                    ),
                    "user_name": u.get("nickname") or "",
                    "user_avatar": _pick_avatar(u),
                    "content": c.get("text") or "",
                    "like_count": int(c.get("digg_count") or 0),
                    "reply_count": int(c.get("reply_comment_total") or 0),
                    "is_top": False,
                }
            )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("抖音热评抓取失败: %s", exc)
        return None


def _parse_router_data(html: str) -> dict[str, Any] | None:
    """从分享页 HTML 提取首个 aweme 对象（含 statistics/author/desc）。"""
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        # 兼容 RENDER_DATA = 'urlencoded json'
        m2 = re.search(r"RENDER_DATA\s*=\s*'([^']+)'", html)
        if not m2:
            return None
        try:
            data = json.loads(urllib.parse.unquote(m2.group(1)))
        except Exception:  # noqa: BLE001
            return None
    else:
        try:
            data = json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            return None

    # 深度优先找 item_list 首元素（不同页面层级可能变化）
    def find(o: Any) -> dict[str, Any] | None:
        if isinstance(o, dict):
            il = o.get("item_list")
            if isinstance(il, list) and il and isinstance(il[0], dict):
                return il[0]
            for v in o.values():
                r = find(v)
                if r is not None:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = find(v)
                if r is not None:
                    return r
        return None

    return find(data)


async def fetch_video_public(
    url: str, cookies_file: str | None = None
) -> dict[str, Any] | None:
    """抓取抖音一条视频的公开互动数据。

    返回与 bili_stats.fetch_video_public 对齐的结构：
        {stats, author, title, publish_ts, comments, warnings}
    stats 键：like_count / comment_count / collect_count / share_count（无 view_count）。
    失败（无法解析 id / 连续风控）返回 None。
    """
    cookies = cookie_header(cookies_file)
    aweme_id = resolve_aweme_id(url, cookies=cookies)
    if not aweme_id:
        logger.warning("抖音无法解析 aweme_id: %s", url)
        return None

    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            html = _http_get_share(aweme_id, cookies)
            item = _parse_router_data(html)
            if item is None:
                raise RuntimeError("分享页未找到视频数据")
            stat = item.get("statistics") or {}
            author = item.get("author") or {}
            desc = item.get("desc") or ""
            warnings: list[str] = []

            # --- 补充：作者主页粉丝/获赞（分享页不含，失败不阻塞） ---
            author_extra = None
            sec_uid = author.get("sec_uid")
            if sec_uid:
                author_extra = _fetch_author_info(str(sec_uid), cookies)
                if author_extra is None:
                    warnings.append("抖音作者粉丝/获赞抓取失败（已跳过）")

            # --- 补充：热评（分享页 comment_list 常为空，走 web api） ---
            comments = _fetch_comments(aweme_id, cookies)
            if comments is None:
                warnings.append("抖音热评抓取失败（保留既有热评）")

            merged_author = {
                "name": (author_extra or {}).get("name") or author.get("nickname"),
                "avatar": (author_extra or {}).get("avatar")
                or (
                    (author.get("avatar_medium") or {}).get("url_list", [None])[0]
                    if isinstance(author.get("avatar_medium"), dict)
                    else (author.get("avatar_thumb") or {}).get("url_list", [None])[0]
                    if isinstance(author.get("avatar_thumb"), dict)
                    else None
                ),
                "uid": (author_extra or {}).get("uid")
                or author.get("sec_uid")
                or author.get("short_id"),
                "fans": (author_extra or {}).get("fans"),
                "likes": (author_extra or {}).get("likes"),
            }
            return {
                "title": desc,
                "publish_ts": item.get("create_time"),
                "stats": {
                    "like_count": int(stat.get("digg_count") or 0),
                    "comment_count": int(stat.get("comment_count") or 0),
                    "collect_count": int(stat.get("collect_count") or 0),
                    "share_count": int(stat.get("share_count") or 0),
                },
                "author": merged_author,
                "comments": comments,
                "warnings": warnings,
            }
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("抖音 stats 抓取第 %s 次失败（瞬时风控可重试）: %s", attempt, exc)
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 * attempt)
    logger.warning("抖音 stats 抓取最终失败 %s: %s", url, last_err)
    return None
