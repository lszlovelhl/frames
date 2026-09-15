"""一次性迁移：script_sentence.quote 约束从 ≥8 字放宽到 ≥1 字。

背景：对话口播（美妆/剧情等）存在真实短句（如"还要放吗"4 字、"对"1 字），
ck_script_sentence_quote CHECK (length(trim(quote)) >= 8) 会把整批 L4 flush
拦下（IntegrityError），叠加日志异常导致 job 永久卡 running。
短句质量已由 validate R2 soft 提示承接（quote < 8 字 → soft，不拦截）。

用法：.venv/bin/python scripts/migrate_sentence_quote_check.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "frames.db"


def main() -> int:
    if not DB_PATH.exists():
        print(f"数据库不存在：{DB_PATH}")
        return 2

    c = sqlite3.connect(DB_PATH)
    try:
        c.execute("PRAGMA foreign_keys=OFF")
        cur = c.execute("SELECT sql FROM sqlite_master WHERE name='script_sentence' AND type='table'")
        old = cur.fetchone()
        if not old:
            print("script_sentence 表不存在，跳过")
            return 0
        sql = old[0]
        if "length(trim(quote)) >= 1" in sql:
            print("约束已是 ≥1，跳过")
            return 0

        n = c.execute("SELECT COUNT(*) FROM script_sentence").fetchone()[0]
        print(f"迁移前 script_sentence 行数：{n}")

        # 重建：仅改 quote 约束 ≥8 → ≥1，其余结构不变
        new_sql = sql.replace(
            "CONSTRAINT ck_script_sentence_quote CHECK (length(trim(quote)) >= 8)",
            "CONSTRAINT ck_script_sentence_quote CHECK (length(trim(quote)) >= 1)",
        )
        assert ">= 1" in new_sql
        c.execute("DROP TABLE IF EXISTS script_sentence_new")
        c.execute(new_sql.replace("CREATE TABLE script_sentence", "CREATE TABLE script_sentence_new"))
        c.execute("INSERT INTO script_sentence_new SELECT * FROM script_sentence")
        c.execute("DROP TABLE script_sentence")
        c.execute("ALTER TABLE script_sentence_new RENAME TO script_sentence")
        c.commit()
        n2 = c.execute("SELECT COUNT(*) FROM script_sentence").fetchone()[0]
        print(f"迁移后行数：{n2}（{'一致 ✓' if n == n2 else '不一致 ✗'}）")
        # 验证约束生效
        c.execute("INSERT INTO script_sentence (id, script_id, seq, raw_sentence_id, source_video_id, quote, start_ms, end_ms, sentence_function, function_reason, emotion_intensity, imagination) VALUES ('t'||'est000000000000000000000000001', 't'||'est000000000000000000000000002', 999999, 't'||'est000000000000000000000000003', 't'||'est000000000000000000000000004', '短', 0, 1, '钩子', '短句测试', 5, 'x')")
        c.execute("DELETE FROM script_sentence WHERE id LIKE 'test%'")
        c.commit()
        print("短 quote（1 字）写入验证 ✓")
        return 0
    finally:
        c.execute("PRAGMA foreign_keys=ON")
        c.close()


if __name__ == "__main__":
    sys.exit(main())
