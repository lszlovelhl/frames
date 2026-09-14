"""拆解后台队列（承载表：breakdown_jobs）

第 7 章清空后旧表 analyses 已 DROP，本模块完成队列 + 进度 + 恢复的迁移：

* 入队 / worker / 恢复全部读写 ``breakdown_jobs``；
* 唯一拆解链路 = 采素材（media.ensure_media）→ 三层分库拆解（three_layer）→ 扣点 → 素材清理；
* 旧五层链路（analysis_layers/segments/analysis_notes/elements）调用已全部停用。

对外函数签名保持不变（enqueue / start_workers / stop_workers / recover_stale /
process / batch_state），路由层无需感知承载表变化。
"""
import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app import models as M
from app.db import SessionLocal
from app.services import billing
from app.services.media_prep import ensure_media, subtitle_for_breakdown
from app.services.analysis import (
    build_media_brief,
    mark_failed,
    run_three_layer_breakdown,
    set_progress,
)
from app.services.runtime_config import breakdown_alias

logger = logging.getLogger(__name__)

CONCURRENCY = 1
STAGE_LABELS = {
    "download": "下载原片",
    "transcribe": "语音转写",
    "frames": "抽帧",
    "vision": "画面理解",
    "audio": "声学分析",
    "bgm": "背景音乐分离",
}

_queue: asyncio.Queue[str] | None = None
_workers: list[asyncio.Task] = []
_pending: set[str] = set()


def _get_queue() -> asyncio.Queue[str]:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def enqueue(job_id: str) -> None:
    """把一条拆解任务加入队列（同一 id 不重复入队）。"""
    if job_id in _pending:
        return
    _pending.add(job_id)
    await _get_queue().put(job_id)


async def start_workers() -> None:
    """应用启动时拉起 worker 并回收上次残留的任务状态。"""
    await recover_stale()
    for i in range(CONCURRENCY):
        t = asyncio.create_task(_worker(i), name=f"breakdown-worker-{i}")
        _workers.append(t)
    logger.info("拆解后台队列已启动（并发 %s）", CONCURRENCY)


async def stop_workers() -> None:
    for t in _workers:
        t.cancel()
    _workers.clear()


async def recover_stale() -> int:
    """把上次进程退出时残留的 queued/running 标为 failed，避免界面卡在"进行中"。"""
    async with SessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(M.BreakdownJob).where(
                        M.BreakdownJob.status.in_(("queued", "running"))
                    )
                )
            )
            .scalars()
            .all()
        )
        for job in rows:
            job.status = "failed"
            job.stage = "interrupted"
            job.message = "上次运行被中断（应用已重启），可重新点击拆解续跑"
            job.pct = job.pct or 0
            job.failed_reason = "应用重启导致中断"
            job.finished_at = datetime.now(UTC)
        if rows:
            await db.commit()
        return len(rows)


async def _worker(idx: int) -> None:
    q = _get_queue()
    while True:
        job_id = await q.get()
        try:
            await process(job_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 单条失败不影响队列
            logger.exception("后台拆解任务异常：job=%s", job_id)
        finally:
            _pending.discard(job_id)
            q.task_done()


async def process(job_id: str) -> None:
    """执行一条后台拆解：采素材（含进度）→ 三层分库拆解 → 扣点 → 素材清理。"""
    async with SessionLocal() as db:
        job = (
            await db.execute(select(M.BreakdownJob).where(M.BreakdownJob.id == job_id))
        ).scalar_one_or_none()
        if job is None:
            logger.warning("后台任务找不到 job=%s，跳过", job_id)
            return
        video = (
            await db.execute(select(M.Video).where(M.Video.id == job.video_id))
        ).scalar_one_or_none()
        if video is None:
            await mark_failed(db, job, message="视频记录不存在")
            await db.commit()
            return

        with_bgm = True
        model = breakdown_alias()

        async def on_progress(stage: str, status: str, detail: str = "") -> None:
            label = STAGE_LABELS.get(stage, stage)
            if status == "running":
                msg = f"素材采集 · {label}…"
            elif status == "done":
                msg = f"素材采集 · {label} 完成" + (f"（{detail}）" if detail else "")
            else:
                msg = f"素材采集 · {label} {status}" + (f"（{detail}）" if detail else "")
            pct = {"download": 8, "transcribe": 22, "frames": 32, "vision": 40, "audio": 44, "bgm": 46}.get(stage, 5)
            await set_progress(db, job, stage=f"media:{stage}", message=msg, pct=pct)

        try:
            job.status = "running"
            job.started_at = job.started_at or datetime.now(UTC)
            await set_progress(db, job, stage="media", message="排队中，准备采集素材…", pct=2)

            manifest = await ensure_media(video, db, with_bgm=with_bgm, progress=on_progress)
            subtitle_text = subtitle_for_breakdown(manifest)
            if not subtitle_text and not manifest.get("frames"):
                await mark_failed(
                    db,
                    job,
                    message="素材不足：语音转写与画面理解均未产出，无法拆解",
                )
                await db.commit()
                return

            media_brief = build_media_brief(manifest)
            await set_progress(db, job, stage="breakdown", message="多模态融合 · 三层拆解开始…", pct=50)

            action = billing.action_for_video(video.duration_ms)
            acc, points = await billing.precheck(db, action)
            result = await run_three_layer_breakdown(
                db, video, job, manifest=manifest, model=model
            )
            if not result.get("ok"):
                logger.warning(
                    "三层拆解未成功：job=%s error=%s", job.id, result.get("error")
                )
            # 三层链路只失败在采集/模型侧时也照旧计费（与旧行为一致：模型已真实消耗）
            await billing.consume(
                db, account=acc, action=action, points=points,
                ref_type="video", ref_id=video.id,
                note=f"批量视频拆解（{action}）",
            )
            await db.commit()

            if job.status == "done":
                try:
                    from app.media.cleanup import cleanup_video

                    await cleanup_video(db, video, auto_trigger=True)
                except Exception:  # noqa: BLE001
                    logger.warning("批量拆解后素材清理失败：video=%s", video.id, exc_info=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("后台拆解失败：job=%s", job_id)
            await db.rollback()
            job = (
                await db.execute(select(M.BreakdownJob).where(M.BreakdownJob.id == job_id))
            ).scalar_one_or_none()
            if job is not None:
                await mark_failed(db, job, message=f"拆解失败：{exc}")
                await db.commit()


async def batch_state(db, *, finished_limit: int = 8) -> list[dict]:
    """进行中的拆解（全部）+ 最近完成的若干条（供前端常驻进度面板轮询）。"""
    active = (
        (
            await db.execute(
                select(M.BreakdownJob, M.Video)
                .join(M.Video, M.Video.id == M.BreakdownJob.video_id)
                .where(M.BreakdownJob.status.in_(("queued", "running")))
                .order_by(M.BreakdownJob.created_at.asc())
                .limit(50)
            )
        )
        .all()
    )
    recent = (
        (
            await db.execute(
                select(M.BreakdownJob, M.Video)
                .join(M.Video, M.Video.id == M.BreakdownJob.video_id)
                .where(M.BreakdownJob.status.in_(("done", "partial", "failed")))
                .order_by(M.BreakdownJob.created_at.desc())
                .limit(finished_limit)
            )
        )
        .all()
    )
    rows = list(active) + list(recent)
    items: list[dict] = []
    for job, video in rows:
        status = job.status
        items.append(
            {
                "analysis_id": str(job.id),
                "video_id": str(video.id),
                "title": video.title or "未命名视频",
                "platform": video.platform,
                "cover_url": video.cover_url,
                "duration_ms": video.duration_ms,
                "status": status,
                "current_layer": 5 if status == "done" else 0,
                "progress": {
                    "stage": job.stage or None,
                    "message": job.message or _default_message(status),
                    "layer": None,
                    "pct": job.pct or (100 if status in ("done", "partial", "failed") else 0),
                    "updated_at": job.updated_at.isoformat() if job.updated_at else None,
                },
                "model": job.model,
                "failed_layers": None,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            }
        )
    return items


def _default_message(status: str) -> str:
    return {
        "queued": "排队中…",
        "running": "拆解中…",
        "done": "拆解完成",
        "partial": "部分完成",
        "failed": "拆解失败",
    }.get(status, status)


# 兼容旧引用点（历史代码里曾从本模块取字幕工具）
__all__ = [
    "enqueue",
    "start_workers",
    "stop_workers",
    "recover_stale",
    "process",
    "batch_state",
    "subtitle_for_breakdown",
]
