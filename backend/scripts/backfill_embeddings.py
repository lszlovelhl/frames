#!/usr/bin/env python3
"""一次性回填：把 lib_* 所有 active 素材批量向量化入 lib_embedding。

用法：
  cd backend
  .venv/bin/python scripts/backfill_embeddings.py

幂等：已向量化且文本未变的行会跳过（content_hash 判断）。
"""
import asyncio
import sqlite3
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services import embedding as emb  # noqa: E402

DB_PATH = BACKEND_DIR / "data" / "frames.db"
TABLES = ["lib_hook", "lib_quote", "lib_copywriting", "lib_topic", "lib_method"]


def fetch_existing_hashes(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT target_id, content_hash FROM lib_embedding WHERE target_table=?",
        (table,),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def main():
    conn = sqlite3.connect(DB_PATH)
    total_new = 0

    for table in TABLES:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE status='active'"
        ).fetchall()
        rows = [dict(r) for r in rows]
        existing = fetch_existing_hashes(conn, table)

        # 过滤需要向量化的
        todo = []
        for r in rows:
            tid = str(r["id"])
            text = emb.build_payload(r, table)
            h = emb._hash_text(text)
            if existing.get(tid) != h:
                todo.append({"tid": tid, "text": text, "row": r})

        print(f"[{table}] 共 {len(rows)} active，需向量化 {len(todo)}")
        if not todo:
            continue

        # 批量调 embedding API
        texts = [t["text"] for t in todo]
        vectors = asyncio.run(emb.embed_texts(texts))

        # 写回（upsert）
        now = time.time()
        for item, vec in zip(todo, vectors):
            h = emb._hash_text(item["text"])
            blob = emb._vec_to_blob(vec)
            conn.execute(
                """INSERT INTO lib_embedding
                   (id, target_table, target_id, content_hash, embedding, dim, model, created_at, updated_at)
                   VALUES (lower(hex(randomblob(16))), ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(target_table, target_id) DO UPDATE SET
                     content_hash=excluded.content_hash,
                     embedding=excluded.embedding,
                     dim=excluded.dim,
                     model=excluded.model,
                     updated_at=excluded.updated_at""",
                (table, item["tid"], h, blob, len(vec), emb.EMBED_MODEL, now, now),
            )
        conn.commit()
        total_new += len(todo)

    print(f"\n=== 回填完成，本次共写入/更新 {total_new} 条向量 ===")
    # 汇总
    for table in TABLES:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM lib_embedding WHERE target_table=?", (table,)
        ).fetchone()[0]
        print(f"  {table}: {cnt} 条向量已入库")


if __name__ == "__main__":
    main()
