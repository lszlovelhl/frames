"""素材公开数据刷新服务：把平台真实互动指标与热评落库。

- baseline：首轮抓取写入 video.stats_snapshot（作为变化对比基准，之后不动）
- latest：每次刷新写入 video_stats_history（VideoStat）
- 评论：video_comments 表先清后插，保留最近一次抓取的热评快照
- 支持平台：bilibili（真实 API）；其他平台暂返回 None（数据源未接入）
"""
import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.media import cookies
from app.media.bili_stats import fetch_video_public

logger = logging.getLogger(__name__)

# 采集器键 → VideoStat 列名
STAT_KEYS = [
    ("view_count", "play_count"),
    ("like_count", "like_count"),
    ("collect_count", "collect_count"),
    ("share_count", "share_count"),
    ("comment_count", "comment_count"),
    ("danmaku_count", "danmaku_count"),
]


def _num(d: dict, key: str) -> int:
    v = d.get(key)
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


async def refresh_video_public(video: M.Video, db: AsyncSession) -> dict | None:
    """抓取并落库一次最新公开数据；返回可展示的 stats 视图；失败返回 None。"""
    if video.platform != "bilibili":
        return None

    bvid = video.platform_video_id or (
        video.url.rsplit("/", 1)[-1] if "/" in video.url else video.url
    )
    if not bvid or not bvid.startswith("BV"):
        from app.media.bili_stats import extract_bvid

        bvid = extract_bvid(video.url) or ""
    if not bvid:
        return None

    try:
        cookie_file = cookies.get_cookie_file_for("bilibili")
        data = await fetch_video_public(bvid, cookies_file=cookie_file)
    except Exception as exc:  # noqa: BLE001
        logger.warning("视频公开数据抓取失败 %s: %s", video.id, exc)
        return None

    stats = data["stats"]
    author = data["author"]
    now = datetime.now(UTC)

    # --- 基准（首抓时固化，用于 diff） ---
    baseline = dict(video.stats_snapshot or {})
    if not baseline:
        video.stats_snapshot = dict(stats)
        video.stats_baseline_at = now
        baseline = dict(stats)

    # --- 作者/标题等元数据回填 ---
    if data.get("title") and (not video.title or video.title == "未命名视频"):
        video.title = data["title"]
    if author.get("name") and not video.author_name:
        video.author_name = author["name"]
    if author.get("mid"):
        video.author_id = author["mid"]
    if author.get("face"):
        video.author_avatar = author["face"]
    if author.get("fans") is not None:
        video.author_fans = author["fans"]
    if author.get("likes") is not None:
        video.author_likes = author["likes"]
    if not video.platform_video_id:
        video.platform_video_id = data.get("bvid")
    if not video.publish_time and data.get("pubdate"):
        try:
            video.publish_time = datetime.fromtimestamp(
                int(data["pubdate"]), tz=UTC
            )
        except (ValueError, OSError):
            pass

    # --- 历史时间序列 ---
    db.add(
        M.VideoStat(
            video_id=video.id,
            play_count=_num(stats, "view_count"),
            like_count=_num(stats, "like_count"),
            collect_count=_num(stats, "collect_count"),
            share_count=_num(stats, "share_count"),
            comment_count=_num(stats, "comment_count"),
            danmaku_count=_num(stats, "danmaku_count"),
            extra={"coin_count": _num(stats, "coin_count")},
        )
    )

    # --- 热评快照（先清后插，保持与最新抓取一致） ---
    await db.execute(delete(M.VideoComment).where(M.VideoComment.video_id == video.id))
    for c in data.get("comments") or []:
        db.add(
            M.VideoComment(
                video_id=video.id,
                comment_id=c.get("comment_id") or "",
                user_id=c.get("user_id") or "",
                user_name=c.get("user_name") or "",
                user_avatar=c.get("user_avatar"),
                content=c.get("content") or "",
                like_count=_num(c, "like_count"),
                reply_count=_num(c, "reply_count"),
                is_top=bool(c.get("is_top")),
            )
        )

    video.stats_updated_at = now
    await db.commit()
    await db.refresh(video)
    return await build_stats_view(
        video, db=db, latest=stats, warnings=data.get("warnings", [])
    )


async def build_stats_view(
    video: M.Video,
    db: AsyncSession | None = None,
    latest: dict | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """构造详情页 stats 视图：基准 + 最新 + 变化 + 作者 + 热评。"""
    baseline = dict(video.stats_snapshot or {})
    cur = dict(latest or {})
    if not cur:
        # 未抓取过最新值：从最近一条历史还原（否则仅基准）
        row = None
        if db is not None:
            row = (
                await db.execute(
                    select(M.VideoStat)
                    .where(M.VideoStat.video_id == video.id)
                    .order_by(M.VideoStat.fetched_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is not None:
            cur = {
                "view_count": row.play_count,
                "like_count": row.like_count,
                "collect_count": row.collect_count,
                "share_count": row.share_count,
                "comment_count": row.comment_count,
                "danmaku_count": row.danmaku_count,
            }
    diff: dict[str, int] = {}
    for k in ("view_count", "like_count", "collect_count", "share_count", "comment_count", "danmaku_count"):
        b = _num(baseline, k)
        c = _num(cur, k)
        if b or c:
            diff[k] = c - b

    comments: list[dict] = []
    if db is not None:
        rows = (
            await db.execute(
                select(M.VideoComment)
                .where(M.VideoComment.video_id == video.id)
                .order_by(M.VideoComment.like_count.desc())
                .limit(20)
            )
        ).scalars().all()
        comments = [
            {
                "comment_id": c.comment_id,
                "user_name": c.user_name,
                "user_avatar": c.user_avatar,
                "content": c.content,
                "like_count": c.like_count,
                "reply_count": c.reply_count,
                "is_top": c.is_top,
            }
            for c in rows
        ]

    return {
        "baseline": baseline,
        "baseline_at": video.stats_baseline_at.isoformat()
        if video.stats_baseline_at
        else None,
        "latest": cur,
        "updated_at": video.stats_updated_at.isoformat()
        if video.stats_updated_at
        else None,
        "diff": diff,
        "author": {
            "name": video.author_name,
            "avatar": video.author_avatar,
            "fans": video.author_fans,
            "likes": video.author_likes,
        },
        "comments": comments,
        "warnings": warnings or [],
    }
