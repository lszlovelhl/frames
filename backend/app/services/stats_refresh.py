"""素材公开数据刷新服务：把平台真实互动指标与热评落库。

- baseline：首轮抓取写入 video.stats_snapshot（作为变化对比基准，之后不动）
- latest：每次刷新写入 video_stats_history（VideoStat）
- 评论：采集器返回 comments 为 None（平台不支持）时不改动已有热评快照
- 支持平台：
    bilibili — 真实 API（含播放/硬币/弹幕/热评/作者粉丝）
    douyin   — 分享页内嵌数据（赞/评论/收藏/转发；抖音不公开播放量与粉丝数，
               采集结果不含 view_count，旧快照里的 view_count=0 会在刷新时清洗）
"""
import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.media import cookies
from app.media.bili_stats import fetch_video_public as _fetch_bili
from app.media.douyin_stats import fetch_video_public as _fetch_douyin

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
_STAT_KEYS_SET = {k for k, _ in STAT_KEYS}


def _num(d: dict, key: str) -> int:
    v = d.get(key)
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _dirty_douyin_baseline(baseline: dict) -> bool:
    """抖音历史脏快照：把并不公开的 view_count=0 当成真实播放写入过。"""
    return baseline.get("view_count") == 0 and "view_count" in baseline


def _as_utc(dt: datetime | None) -> datetime | None:
    """DB 返回 naive 时间戳时补 UTC 时区，便于与抓取时刻对齐比较。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def refresh_video_public(video: M.Video, db: AsyncSession) -> dict | None:
    """抓取并落库一次最新公开数据；返回可展示的 stats 视图；失败返回 None。"""
    if video.platform == "bilibili":
        return await _refresh_bilibili(video, db)
    if video.platform == "douyin":
        return await _refresh_douyin(video, db)
    return None


async def _refresh_bilibili(video: M.Video, db: AsyncSession) -> dict | None:
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
        data = await _fetch_bili(bvid, cookies_file=cookie_file)
    except Exception as exc:  # noqa: BLE001
        logger.warning("视频公开数据抓取失败 %s: %s", video.id, exc)
        return None
    if data is None:
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

    await _persist_refresh(video, db, data, stats, author, now)
    return await build_stats_view(
        video, db=db, latest=stats, warnings=data.get("warnings", [])
    )


async def _refresh_douyin(video: M.Video, db: AsyncSession) -> dict | None:
    try:
        cookie_file = cookies.get_cookie_file_for("douyin")
        data = await _fetch_douyin(video.url, cookies_file=cookie_file)
    except Exception as exc:  # noqa: BLE001
        logger.warning("抖音公开数据抓取失败 %s: %s", video.id, exc)
        return None
    if data is None:
        return None

    stats = data["stats"]
    author = data["author"]
    now = datetime.now(UTC)

    # 抖音作者元数据以采集器为准（分享页+主页 API），强制对齐：
    # 存量行常是 yt-dlp 建档时的抖音号/uid，昵称/头像/粉丝/获赞全缺失，
    # 不能沿用 bilibili 的“非空不覆盖”策略。
    if author.get("name"):
        video.author_name = author["name"]
    if author.get("avatar"):
        video.author_avatar = author["avatar"]
    if author.get("uid"):
        video.author_id = str(author["uid"])
    if author.get("fans") is not None:
        video.author_fans = author["fans"]
    if author.get("likes") is not None:
        video.author_likes = author["likes"]

    # --- 基准（首抓时固化，用于 diff） ---
    baseline = dict(video.stats_snapshot or {})
    # 历史脏数据清洗：抖音不公开播放量，旧快照的 view_count=0 是假数据；
    # 但 like/comment 等真实指标仍保留为 diff 基准。
    if _dirty_douyin_baseline(baseline):
        baseline.pop("view_count", None)
        video.stats_snapshot = dict(baseline)
    if video.stats_baseline_at is None:
        # 旧建档快照是 yt-dlp 下载时的附带数据，非真实统计链路的采集时刻
        # （且往往缺 collect/share 维度）→ 以本次真实采集重建基准，diff 从今天起算
        video.stats_snapshot = dict(stats)
        video.stats_baseline_at = now
        baseline = dict(stats)

    await _persist_refresh(video, db, data, stats, author, now)
    return await build_stats_view(
        video, db=db, latest=stats, warnings=data.get("warnings", [])
    )


async def _persist_refresh(
    video: M.Video,
    db: AsyncSession,
    data: dict,
    stats: dict,
    author: dict,
    now: datetime,
) -> None:
    """落库最新快照与元数据回填（bili / douyin 通用）。"""
    # --- 作者/标题等元数据回填 ---
    if data.get("title") and (not video.title or video.title == "未命名视频"):
        video.title = data["title"]
    if author.get("name") and not video.author_name:
        video.author_name = author["name"]
    if author.get("avatar") and not video.author_avatar:
        video.author_avatar = author["avatar"]
    if author.get("uid") and not video.author_id:
        video.author_id = author["uid"]
    if author.get("fans") is not None:
        video.author_fans = author["fans"]
    if author.get("likes") is not None:
        video.author_likes = author["likes"]
    if not video.platform_video_id:
        if video.platform == "bilibili" and data.get("bvid"):
            video.platform_video_id = data["bvid"]
    if not video.publish_time:
        ts = data.get("publish_ts")
        if ts is None and data.get("pubdate") is not None:
            ts = data["pubdate"]
        if ts:
            try:
                video.publish_time = datetime.fromtimestamp(int(ts), tz=UTC)
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
            extra={"coin_count": _num(stats, "coin_count")} if video.platform == "bilibili" else {},
        )
    )

    # --- 热评快照：仅平台支持时先清后插（抖音分享页无热评，保留既有） ---
    if data.get("comments") is not None:
        await db.execute(delete(M.VideoComment).where(M.VideoComment.video_id == video.id))
        for c in data["comments"]:
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


async def build_stats_view(
    video: M.Video,
    db: AsyncSession | None = None,
    latest: dict | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """构造详情页 stats 视图：基准 + 最新 + 变化 + 作者 + 热评。

    - 无最新数据（从未刷新成功）时 diff 为空，避免把基准值错减成负数；
    - 从历史还原 latest 时只带「基准里出现过的键」：平台不提供的指标
      （如抖音 view_count）不会凭空补 0。
    """
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
            raw = {
                "view_count": row.play_count,
                "like_count": row.like_count,
                "collect_count": row.collect_count,
                "share_count": row.share_count,
                "comment_count": row.comment_count,
                "danmaku_count": row.danmaku_count,
            }
            # 平台不提供该指标（抖音 view=0 占位）→ 不展示，避免假 0
            cur = {k: v for k, v in raw.items() if k in baseline or v != 0}

    diff: dict[str, int] = {}
    if cur:
        # 仅对比「最新一次真实采集到的键」；抖音无 view_count → 不产生播放变化
        for k in cur:
            if k not in _STAT_KEYS_SET:
                continue
            diff[k] = _num(cur, k) - _num(baseline, k)

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


async def build_stats_history(video: M.Video, db: AsyncSession) -> dict:
    """构造互动数据历史时序视图（详情页趋势曲线数据源）。

    - 按 fetched_at 升序返回 video_stats_history 全量行；
    - 平台不提供的指标不掺 0：抖音旧脏快照的 view_count=0 不会进入
      series_keys，对应点位置 null（与 build_stats_view 展示口径一致）；
    - 附 baseline/latest 与端点 phase 标注，供前端标记「初始/当前」。
    """
    baseline = dict(video.stats_snapshot or {})
    # 抖音历史脏快照清洗：不公开的 view_count=0 不作为“支持维度”依据
    if video.platform == "douyin" and _dirty_douyin_baseline(baseline):
        baseline.pop("view_count", None)

    rows = (
        await db.execute(
            select(M.VideoStat)
            .where(M.VideoStat.video_id == video.id)
            .order_by(M.VideoStat.fetched_at.asc())
        )
    ).scalars().all()

    # 支持维度：基准中出现过，或历史快照里出现非 0 真实值
    supported: set[str] = set()
    for raw_key, col in STAT_KEYS:
        if raw_key in baseline:
            supported.add(raw_key)
            continue
        if any(getattr(r, col) != 0 for r in rows):
            supported.add(raw_key)
    if video.platform == "bilibili":
        coin_supported = any(bool((r.extra or {}).get("coin_count")) for r in rows)
        if coin_supported or baseline.get("coin_count") is not None:
            supported.add("coin_count")

    baseline_at = _as_utc(video.stats_baseline_at)
    updated_at = _as_utc(video.stats_updated_at)

    def _phase(idx: int, total: int, fetched_at: datetime) -> str | None:
        if total == 0:
            return None
        f = _as_utc(fetched_at) or fetched_at
        if idx == 0 and baseline_at is not None and abs((f - baseline_at).total_seconds()) <= 300:
            return "baseline"
        if idx == total - 1 and updated_at is not None and abs((f - updated_at).total_seconds()) <= 300:
            return "latest"
        return None

    points: list[dict] = []
    for idx, row in enumerate(rows):
        f_utc = _as_utc(row.fetched_at)
        point: dict = {"fetched_at": f_utc.isoformat() if f_utc else row.fetched_at.isoformat()}
        for raw_key, col in STAT_KEYS:
            point[raw_key] = getattr(row, col) if raw_key in supported else None
        coin = (row.extra or {}).get("coin_count")
        point["coin_count"] = coin if "coin_count" in supported else None
        point["phase"] = _phase(idx, len(rows), row.fetched_at)
        points.append(point)

    # latest：与 build_stats_view 相同的清洗口径（只还原真实采集到的键）
    cur: dict = {}
    if rows:
        last = rows[-1]
        raw = {
            "view_count": last.play_count,
            "like_count": last.like_count,
            "collect_count": last.collect_count,
            "share_count": last.share_count,
            "comment_count": last.comment_count,
            "danmaku_count": last.danmaku_count,
        }
        cur = {k: v for k, v in raw.items() if k in baseline or v != 0}
        if "coin_count" in supported:
            coin = (last.extra or {}).get("coin_count")
            if coin:
                cur["coin_count"] = coin

    # series_keys：按展示顺序给出可绘制维度（前端按此构建图例/序列）
    series_keys = [k for k, _ in STAT_KEYS if k in supported]
    if "coin_count" in supported:
        series_keys.append("coin_count")

    return {
        "video_id": str(video.id),
        "platform": video.platform,
        "baseline": baseline,
        "baseline_at": video.stats_baseline_at.isoformat()
        if video.stats_baseline_at
        else None,
        "latest": cur,
        "updated_at": video.stats_updated_at.isoformat()
        if video.stats_updated_at
        else None,
        "series_keys": series_keys,
        "points": points,
    }
