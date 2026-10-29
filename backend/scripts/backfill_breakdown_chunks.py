#!/usr/bin/env python3
"""一次性回填：把全部已拆解 script 建立 breakdown_chunk 混合索引。

用法：
  cd backend
  .venv/bin/python scripts/backfill_breakdown_chunks.py

幂等：
- 按 script_id 全量重建；content_hash 未变的分块复用旧向量，不重复调本地 Ollama；
- 本地 embedding 不可用时该 script 跳过并打印原因（不静默上云），其余继续。

owner_key 统一写 'default'（当前弱隔离取舍，见 app/services/breakdown_index.py docstring）。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.services.breakdown_index import (  # noqa: E402
    ensure_breakdown_index_schema,
    index_breakdown,
)

logger = logging.getLogger("backfill_breakdown_chunks")


async def main() -> None:
    async with SessionLocal() as db:
        await ensure_breakdown_index_schema(db)

        rows = (
            await db.execute(
                text("SELECT id, video_id FROM script_script ORDER BY created_at ASC")
            )
        ).all()
        print(f"待回填 script 共 {len(rows)} 条")

        grand: dict[str, int] = {}
        ok, failed = 0, 0
        for sid, vid in rows:
            try:
                counts = await index_breakdown(
                    db, str(sid), str(vid) if vid else None, owner_key="default"
                )
                ok += 1
                for kind, n in counts.items():
                    grand[kind] = grand.get(kind, 0) + n
                print(f"[ok] script={str(sid)[:8]} 计数={counts}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                await db.rollback()
                logger.warning("跳过 script=%s：%s", sid, e)
                print(f"[skip] script={str(sid)[:8]} 原因={e}")

        print("\n=== 回填完成 ===")
        print(f"成功 {ok} 条 / 跳过 {failed} 条")
        for kind in sorted(grand):
            print(f"  {kind}: {grand[kind]} 块")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    asyncio.run(main())
