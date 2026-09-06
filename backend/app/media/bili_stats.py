"""B 站公开数据采集（真实接口，非演示数据）

- 视频主档：GET /x/web-interface/view?bvid=  → 标题/作者/互动指标
- 作者粉丝：GET /x/web-interface/card?mid=   → fans（获赞接口匿名不可用则留空）
- 热评：    GET /x/v2/reply?type=1&oid=&sort=2 → 按热度排序的顶层热评

低频匿名调用通常可用；风控时（-412）可挂 cookies 文件重试。
"""
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BILI_API = "https://api.bilibili.com"
BILI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")


def extract_bvid(url: str) -> str | None:
    m = BV_RE.search(url or "")
    return m.group(1) if m else None


class BiliApiError(RuntimeError):
    pass


async def _get(
    client: httpx.AsyncClient,
    path: str,
    params: dict,
    cookies_file: str | None = None,
) -> dict:
    headers = {"User-Agent": BILI_UA, "Referer": "https://www.bilibili.com/"}
    cookies = {}
    if cookies_file:
        # Netscape cookie 行：domain \t flag \t path \t secure \t expiry \t name \t value
        try:
            for line in open(cookies_file, encoding="utf-8", errors="ignore"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    cookies[parts[5].strip()] = parts[6].strip()
        except Exception:  # noqa: BLE001
            pass
    resp = await client.get(
        BILI_API + path, params=params, headers=headers, cookies=cookies
    )
    resp.raise_for_status()
    data = resp.json()
    code = data.get("code")
    if code != 0:
        raise BiliApiError(f"B站接口 {path} 返回 code={code} msg={data.get('message')}")
    return data.get("data") or {}


async def fetch_video_public(
    bvid: str, cookies_file: str | None = None, top_comments: int = 15
) -> dict[str, Any]:
    """抓取视频公开数据：互动指标、作者、热评。

    返回结构：
      {
        bvid, aid, title, pubdate,
        author: {mid, name, face, fans, likes(可能 None)},
        stats: {view_count, danmaku_count, comment_count,
                collect_count(favorite), coin_count, share_count, like_count},
        comments: [{comment_id, user_id, user_name, user_avatar, content,
                    like_count, reply_count, is_top}]
        warnings: [str]
      }
    """
    warnings: list[str] = []
    timeout = httpx.Timeout(15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        info = await _get(client, "/x/web-interface/view", {"bvid": bvid}, cookies_file)
        aid = info.get("aid")
        owner = info.get("owner") or {}
        stat = info.get("stat") or {}

        author = {
            "mid": str(owner.get("mid") or ""),
            "name": owner.get("name") or "",
            "face": owner.get("face") or "",
            "fans": None,
            "likes": None,
        }
        try:
            if author["mid"]:
                card = await _get(
                    client, "/x/web-interface/card", {"mid": author["mid"]}, cookies_file
                )
                ccard = card.get("card") or {}
                author["fans"] = ccard.get("fans")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"作者粉丝抓取失败：{exc}")

        comments: list[dict] = []
        try:
            if aid:
                reply = await _get(
                    client,
                    "/x/v2/reply",
                    {"type": 1, "oid": aid, "sort": 2, "ps": top_comments, "pn": 1},
                    cookies_file,
                )
                for r in reply.get("replies") or []:
                    member = r.get("member") or {}
                    content = r.get("content") or {}
                    avatar_raw = member.get("avatar") or ""
                    avatar = (
                        avatar_raw.get("url")
                        if isinstance(avatar_raw, dict)
                        else (avatar_raw if isinstance(avatar_raw, str) else "")
                    )
                    comments.append(
                        {
                            "comment_id": str(r.get("rpid") or ""),
                            "user_id": str(r.get("mid") or ""),
                            "user_name": member.get("uname") or "",
                            "user_avatar": avatar,
                            "content": content.get("message") or "",
                            "like_count": int(r.get("like") or 0),
                            "reply_count": int(r.get("rcount") or r.get("count") or 0),
                            "is_top": bool(r.get("top")),
                        }
                    )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"热评抓取失败：{exc}")

        stats = {
            "view_count": int(stat.get("view") or 0),
            "danmaku_count": int(stat.get("danmaku") or 0),
            "comment_count": int(stat.get("reply") or 0),
            "collect_count": int(stat.get("favorite") or 0),
            "coin_count": int(stat.get("coin") or 0),
            "share_count": int(stat.get("share") or 0),
            "like_count": int(stat.get("like") or 0),
        }

    return {
        "bvid": info.get("bvid") or bvid,
        "aid": aid,
        "title": info.get("title") or "",
        "pubdate": info.get("pubdate"),
        "author": author,
        "stats": stats,
        "comments": comments,
        "warnings": warnings,
    }
