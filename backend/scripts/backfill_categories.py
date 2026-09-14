"""存量视频赛道分类回填 CLI

用法（在 backend 目录）：
    .venv/bin/python scripts/backfill_categories.py [--limit 200]

将 category_guess 为空的所有存量 videos 逐条调 flash 分类并回填。
"""
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


async def main() -> None:
    from app.db import SessionLocal
    from app.services.categorize import backfill_missing_categories

    limit = 200
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    async with SessionLocal() as db:
        stat = await backfill_missing_categories(db, limit=limit)
    print(f"backfill done: {stat}")


if __name__ == "__main__":
    asyncio.run(main())
