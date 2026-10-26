"""本地 embedding 迁移：把库里全部旧向量（1024维智谱）重算为本地 nomic-embed-text（768维）。

隐私优先：重算全程本地，文本不出本机。
用法：backend/.venv/bin/python scripts/rebuild_embeddings.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import SessionLocal
from app.services.embedding import build_payload, embed_texts, upsert_one

TABLES = ["lib_hook", "lib_quote", "lib_copywriting", "lib_topic", "lib_method", "script_script"]


async def main():
    async with SessionLocal() as db:
        # 清掉旧向量（1024 维）
        res = await db.execute(text("DELETE FROM lib_embedding WHERE dim != 768"))
        await db.commit()
        print(f"已清理旧维度向量 {res.rowcount} 条")

        total = 0
        for table in TABLES:
            rows = (await db.execute(text(f"SELECT * FROM {table}"))).mappings().all()
            if not rows:
                continue
            payloads = [build_payload(dict(r), table) for r in rows]
            vecs = await embed_texts(payloads)
            for r, vec in zip(rows, vecs):
                if not vec:
                    continue
                await upsert_one(db, table, str(r["id"]), payloads[rows.index(r)], vec)
            total += len(rows)
            print(f"{table}: 重算 {len(rows)} 条")

        # 记忆向量旧的全部删（对话时会自动重新写入）
        mem_del = await db.execute(text(
            "DELETE FROM lib_embedding WHERE target_table='user_memory'"
        ))
        await db.commit()
        print(f"清理 user_memory 旧向量 {mem_del.rowcount} 条（对话时自动重写）")
        print(f"完成，共重算 {total} 条素材向量")


asyncio.run(main())
