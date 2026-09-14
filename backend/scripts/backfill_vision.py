"""为已 ready 但画面简报为空的视频补跑视觉段（glm-4v-flash），更新 raw_files.frames 落库。

用法: PYTHONPATH=. .venv/bin/python scripts/backfill_vision.py <video_id>
"""
import asyncio
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.media.vision import describe_frames
from app.models import Video

VIDEO_ID = sys.argv[1] if len(sys.argv) > 1 else None


async def main() -> None:
    if not VIDEO_ID:
        print("usage: backfill_vision.py <video_id>")
        return
    async with SessionLocal() as db:
        video = (
            await db.execute(select(Video).where(Video.id == VIDEO_ID))
        ).scalar_one_or_none()
        if video is None:
            print("video not found:", VIDEO_ID)
            return
        raw = video.raw_files or {}
        frames = raw.get("frames") or []
        need = [f for f in frames if not (f.get("desc") or "").strip()]
        print(f"frames total={len(frames)} need_vision={len(need)}")
        if not need:
            print("no frames need vision; nothing to do")
            return

        # describe_frames 只接受标准 dict；路径用绝对路径
        for f in frames:
            p = f.get("path", "")
            if p and not p.startswith("/"):
                f["path"] = str(Path(raw.get("work_dir", "")) / p)

        from pathlib import Path

        enriched = await describe_frames(frames)
        done_cnt = sum(1 for f in enriched if f.get("desc"))
        print(f"vision done {done_cnt}/{len(enriched)}")
        raw["frames"] = enriched
        video.raw_files = raw
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(video, "raw_files")  # 同对象赋值需强制标记 dirty
        await db.commit()
        print("raw_files.frames updated, media_status:", raw.get("media_status"))


if __name__ == "__main__":
    asyncio.run(main())
