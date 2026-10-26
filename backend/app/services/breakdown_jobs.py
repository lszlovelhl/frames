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
    mark_done,
    mark_failed,
    run_three_layer_breakdown,
    run_two_layer_breakdown,
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
    """崩溃恢复（学 LangGraph 断点续跑）：上次进程退出残留的 queued/running
    任务自动重新入队续跑，而不是直接标 failed。

    续跑原理：
    - 素材采集有缓存（ensure_media 复用已下载素材），重跑不会重复下载
    - script 落库有幂等，重跑不产生脏数据
    - 同一 job 只自动恢复一次（app_meta 记数），第二次再崩则标 failed，
      防止崩溃循环烧 token

    返回恢复的任务数。
    """
    async with SessionLocal() as db:
        from sqlalchemy import text as sa_text

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
        recovered = 0
        for job in rows:
            # 查该 job 已自动恢复次数
            row = (await db.execute(sa_text(
                "SELECT value FROM app_meta WHERE key=:k"
            ), {"k": f"job_recover:{job.id}"})).fetchone()
            prev = int(row[0]) if row and row[0] else 0

            if prev >= 1:
                # 已自动恢复过一次又崩 → 不再无限重试，标 failed
                job.status = "failed"
                job.stage = "interrupted"
                job.message = "自动续跑再次中断（应用重启），可手动重新拆解"
                job.failed_reason = "recover_again"
                job.finished_at = datetime.now(UTC)
                logger.warning("job=%s 二次中断，放弃自动恢复", job.id)
            else:
                # 首次崩溃 → 断点续跑：重新入队
                await db.execute(sa_text(
                    "INSERT INTO app_meta (key, value) VALUES (:k, '1') "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
                ), {"k": f"job_recover:{job.id}"})
                job.status = "queued"
                job.message = "上次运行被中断，已自动续跑…"
                job.stage = "recovered"
                job.pct = job.pct or 0
                # 素材已采集的部分会被 ensure_media 缓存复用，近似断点续跑
                recovered += 1

        if rows:
            await db.commit()
        # 续跑的重新入队（在事务提交后，避免队列消费先于提交）
        for job in rows:
            if job.status == "queued" and job.stage == "recovered":
                _queue_add(job.id)
        return recovered


def _queue_add(job_id: str) -> None:
    """把任务放回队列（含防重）。"""
    if job_id in _pending:
        return
    _pending.add(job_id)
    _get_queue().put_nowait(job_id)


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


async def _postprocess(db, job, video, result) -> None:
    """拆解完成后的后处理：向量化 + 飞书通知 + 主动推荐。失败不影响主流程。"""
    if job.status != "done":
        return

    sid = result.get("script_id")
    if not sid:
        return
    sid_str = sid.hex if hasattr(sid, "hex") else str(sid)

    # 1. script 向量化
    try:
        from sqlalchemy import text as sa_text
        from app.services.embedding import embed_texts, upsert_one
        row = (await db.execute(sa_text(
            "SELECT core_idea, summary FROM script_script WHERE id=:sid"
        ), {"sid": sid_str})).fetchone()
        if row:
            embed_text = f"视频主题：{row[0] or ''}\n摘要：{row[1] or ''}"
            vecs = await embed_texts([embed_text])
            await upsert_one(db, "script_script", sid_str, embed_text, vecs[0])
            logger.info("script 向量化成功：script_id=%s", sid_str)
    except Exception as e:
        logger.warning("script 向量化失败（不影响主流程）：%s", e, exc_info=True)

    # 2. 查质量分
    quality = 0.0
    try:
        from sqlalchemy import text as sa_text
        row = (await db.execute(sa_text(
            "SELECT quality_score FROM script_script WHERE id=:sid"
        ), {"sid": sid_str})).fetchone()
        if row:
            quality = float(row[0] or 0)
    except Exception:
        pass

    # 3. 飞书通知
    try:
        from app.services.feishu_notify import notify_breakdown_done
        await notify_breakdown_done(
            video_title=video.title or video.id[:8],
            script_id=sid_str,
            quality_score=quality,
        )
    except Exception as e:
        logger.warning("飞书通知失败（不影响主流程）：%s", e)

    # 4. 主动推荐：往聊天记录里插一条 AI 消息
    try:
        import uuid
        from sqlalchemy import text as sa_text
        sess_row = await db.execute(sa_text(
            "SELECT id FROM chat_sessions WHERE platform='web' ORDER BY updated_at DESC LIMIT 1"
        ))
        sess = sess_row.fetchone()
        if sess:
            msg_id = uuid.uuid4().hex
            reply = f"✅ 拆解完成！质量分 {quality}。\n要不要我帮你找类似的视频？"
            await db.execute(sa_text(
                "INSERT INTO chat_messages (id, session_id, role, content, intent) "
                "VALUES (:mid, :sid, 'assistant', :content, 'proactive')"
            ), {"mid": msg_id, "sid": sess[0], "content": reply})
            logger.info("主动推荐消息已插入 chat 记录")
    except Exception as e:
        logger.warning("主动推荐失败（不影响主流程）：%s", e)


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

            # 无论两层还是三层，统一提前计算计费动作与预检
            action = billing.action_for_video(video.duration_ms)
            acc, points = await billing.precheck(db, action)

            # 选择 pipeline：两层（新）还是三层（旧）
            # 环境变量 FRAMES_PIPELINE=two 用新的两层，默认用旧的三层
            import os
            use_two_layer = os.getenv("FRAMES_PIPELINE", "three") == "two"

            if use_two_layer:
                await set_progress(db, job, stage="breakdown", message="两层简化 pipeline 开始…", pct=50)
                result = await run_two_layer_breakdown(
                    db, video, job, manifest=manifest, model=model
                )
            else:
                await set_progress(db, job, stage="breakdown", message="多模态融合 · 三层拆解开始…", pct=50)
                result = await run_three_layer_breakdown(
                    db, video, job, manifest=manifest, model=model
                )
            if not result.get("ok"):
                err = result.get("error") or "三层链路未产出有效结果"
                logger.warning("拆解未成功：job=%s error=%s", job.id, err)
                await mark_failed(db, job, message=f"拆解失败：{err}")
            else:
                # 三层链路成功：标记 job done（script 已由 pipeline 落库）
                script_id = result.get("script_id")
                # 多步工作流：若该视频挂了文案待办（"拆解+写文案"），自动生成
                try:
                    from app.services.copywriting import run_copywriting_if_pending
                    await run_copywriting_if_pending(db, str(video.id), str(script_id))
                except Exception:
                    pass
                await mark_done(
                    db, job,
                    evidence={k: v for k, v in result.items() if k != "script_id"},
                    script_id=script_id,
                )
            # 链路只失败在采集/模型侧时也照旧记用量流水
            # 续跑的任务（app_meta 有 job_recover 标记）不重复扣费——首次已扣
            is_recovered = False
            try:
                from sqlalchemy import text as sa_text
                rec = (await db.execute(sa_text(
                    "SELECT value FROM app_meta WHERE key=:k"
                ), {"k": f"job_recover:{job.id}"})).fetchone()
                is_recovered = rec is not None
            except Exception:
                pass
            if not is_recovered:
                await billing.consume(
                    db, account=acc, action=action, points=points,
                    ref_type="video", ref_id=video.id,
                    note=f"批量视频拆解（{action}）",
                )
            await db.commit()

            # 后处理：向量化 + 飞书通知 + 主动推荐（失败不影响主流程）
            await _postprocess(db, job, video, result)

            if job.status == "done":
                try:
                    from app.media.cleanup import cleanup_video

                    await cleanup_video(db, video, auto_trigger=True)
                except Exception:  # noqa: BLE001
                    logger.warning("批量拆解后素材清理失败：video=%s", video.id, exc_info=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("后台拆解失败：job=%s", job_id)
            try:
                await db.rollback()
                db.expunge_all()
            except Exception:  # noqa: BLE001
                pass
            # 隔离故障会话：使用全新独立的 session 标记 failed，绝不复用受损会话（彻底杜绝 PendingRollbackError）
            try:
                async with SessionLocal() as fresh_db:
                    fresh_job = (
                        await fresh_db.execute(select(M.BreakdownJob).where(M.BreakdownJob.id == job_id))
                    ).scalar_one_or_none()
                    if fresh_job is not None:
                        await mark_failed(fresh_db, fresh_job, message=f"拆解失败：{exc}")
                        await fresh_db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("独立会话更新 job failed 状态失败：job=%s", job_id)


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
