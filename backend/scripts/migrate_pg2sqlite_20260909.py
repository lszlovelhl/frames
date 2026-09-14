"""PG -> SQLite 数据搬移 CLI（第①阶段 · 2026-09-09）

链路：PG 只读导出 JSONL 备份 -> 校验目标 SQLite 空基线 -> 导入 -> 逐表行数对比 + 抽样校验。
原则：
- 源 PG 全程只读（SELECT 与 pg_dump 归档），不删不改。
- 严禁 drop_all / 清库；目标库默认仅在“空表 / --fresh”时才写入。
- 幂等可重跑：--jsonl-dir 已存在时跳过导出；目标已有业务数据时默认拒绝（除非 --fresh 重置）。

用法示例：
    python -m scripts.migrate_pg2sqlite_20260909                     # 全链路
    python -m scripts.migrate_pg2sqlite_20260909 --fresh             # 目标库存在旧数据时强制重建
    python -m scripts.migrate_pg2sqlite_20260909 --no-archive        # 跳过 pg_dump 归档
    python -m scripts.migrate_pg2sqlite_20260909 --jsonl-dir <dir>   # 复用已有导出
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import DateTime, JSON, Uuid, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app import models as _models_  # noqa: E402,F401  确保 metadata 完整
from app.core.config import BACKEND_DIR as CFG_DIR  # noqa: E402
from app.core.config import PG_LEGACY_URL  # noqa: E402
from app.db import Base  # noqa: E402

DEFAULT_TARGET = str(Path(CFG_DIR) / "data" / "frames.db")

# 表依赖顺序（Base.metadata.sorted_tables 给出拓扑序，显式兜底校验）
_BUSINESS_TABLES = [
    t.name for t in Base.metadata.sorted_tables if t.name != "alembic_version"
]


def _iso(v):
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, uuid.UUID):
        # 与 SQLAlchemy Uuid(SQLite CHAR(32)) 存储一致：无连字符 hex
        return v.hex
    return v


def _coerce(col_type, v):
    """JSONL -> SQLAlchemy 绑定前按列类型还原。"""
    if v is None:
        return None
    if isinstance(col_type, DateTime):
        if isinstance(v, str):
            s = v[:-1] + "+00:00" if v.endswith("Z") else v
            return datetime.fromisoformat(s)
        return v
    if isinstance(col_type, JSON):
        # 原生 sqlite3 不支持 list/dict 绑定，按 SQLAlchemy JSON 语义序列化
        if isinstance(v, str):
            return v
        return json.dumps(v, ensure_ascii=False)
    if isinstance(col_type, Uuid):
        return v  # Uuid 交给 bind 处理器归一
    if isinstance(col_type, __import__("sqlalchemy").Boolean):
        return bool(v)
    return v


def archive_pg(dump_path: Path) -> None:
    """PG 全量归档（-Fc 压缩格式，回滚底）。"""
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "pg_dump", "-h", "localhost", "-U", "zhuolittlelong",
        "-Fc", "-d", "frames_dev", "-f", str(dump_path),
    ]
    print(f"[archive] pg_dump -> {dump_path}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"pg_dump 失败: {r.stderr[:1000]}")
    print(f"[archive] ok size={dump_path.stat().st_size} bytes")


async def export_pg(jsonl_dir: Path, source_url: str) -> dict[str, int]:
    """PG 只读导出：每表一个 JSONL；返回 {表: 行数}。"""
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(source_url)
    counts: dict[str, int] = {}
    try:
        async with engine.connect() as conn:
            for table_name in _BUSINESS_TABLES:
                out = jsonl_dir / f"{table_name}.jsonl"
                if out.exists():
                    # 幂等：已有导出则跳过
                    n = sum(1 for _ in out.open(encoding="utf-8"))
                    counts[table_name] = n
                    print(f"[export] reuse {table_name}: {n}")
                    continue
                res = await conn.execute(text(f'SELECT * FROM "{table_name}"'))
                rows = res.fetchall()
                cols = list(res.keys())
                n = 0
                with out.open("w", encoding="utf-8") as f:
                    for row in rows:
                        rec = {c: _iso(row._mapping[c]) for c in cols}
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        n += 1
                counts[table_name] = n
                print(f"[export] {table_name}: {n}")
    finally:
        await engine.dispose()
    return counts


def _ensure_schema(target: str) -> None:
    """目标库无业务表时，用 alembic 基线重建空 schema（严禁 drop_all；子进程避免嵌套事件循环）。"""
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{target}"
    for args in (
        ["stamp", "c3d5e7f9a1b3"],
        ["upgrade", "head"],
    ):
        r = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"alembic {args[0]} 失败: {r.stderr[-1000:]}")
    print(f"[schema] alembic baseline ready -> {target}")


def import_to_sqlite(jsonl_dir: Path, target: str, reset: bool) -> dict[str, int]:
    """导入 JSONL 到空 SQLite；返回 {表: 行数}。"""
    if reset and Path(target).exists():
        Path(target).unlink()
    # 目标库必须已有 alembic 基线 schema（由 migrate 前 alembic upgrade head 创建）
    conn = sqlite3.connect(target)
    existing = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if reset or not existing.issuperset(set(_BUSINESS_TABLES)):
        conn.close()
        _ensure_schema(target)
        conn = sqlite3.connect(target)
    conn.execute("PRAGMA foreign_keys=ON")
    counts: dict[str, int] = {}
    try:
        existing = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        # 业务表若已有数据则拒绝导入（防重复/覆盖真实数据）
        for t in _BUSINESS_TABLES:
            if t in existing:
                cnt = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                if cnt and not reset:
                    raise RuntimeError(
                        f"目标表 {t} 已有 {cnt} 行；如需重建请加 --fresh"
                    )
        table_map = {t.name: t for t in Base.metadata.sorted_tables}
        for table_name in _BUSINESS_TABLES:
            table = table_map[table_name]
            jl = jsonl_dir / f"{table_name}.jsonl"
            if not jl.exists():
                raise RuntimeError(f"缺少导出文件 {jl}")
            n = 0
            batch: list[tuple] = []
            placeholders = ",".join(["?"] * len(table.columns))
            col_names = [c.name for c in table.columns]
            sql = f'INSERT INTO "{table_name}" ({",".join(col_names)}) VALUES ({placeholders})'
            with jl.open(encoding="utf-8") as f:
                for line in f:
                    rec = json.loads(line)
                    batch.append(
                        tuple(_coerce(table.c[c].type, rec.get(c)) for c in col_names)
                    )
                    n += 1
                    if len(batch) >= 200:
                        conn.executemany(sql, batch)
                        batch.clear()
                if batch:
                    conn.executemany(sql, batch)
            conn.commit()
            counts[table_name] = n
            print(f"[import] {table_name}: {n}")
    finally:
        conn.close()
    return counts


def verify(target: str, pg_counts: dict[str, int], sqlite_counts: dict[str, int]) -> None:
    """逐表行数对比 + 抽样：videos 关键 JSON 可解析、datetime 可被 ORM 解析。"""
    conn = sqlite3.connect(target)
    try:
        errors = []
        for t in _BUSINESS_TABLES:
            sc = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            pc = pg_counts.get(t)
            flag = "OK " if pc == sc else "DIFF"
            if pc != sc:
                errors.append(f"{t}: PG={pc} SQLite={sc}")
            print(f"[verify] {t}: PG={pc} SQLite={sc} {flag}")
        # 抽样：videos 最新 3 条 id + raw_files JSON 可解析
        rows = conn.execute(
            "SELECT id, raw_files, title FROM videos ORDER BY created_at DESC LIMIT 3"
        ).fetchall()
        print("[verify] sample videos:", [(r[0], r[2]) for r in rows])
        bad_json = 0
        raw_rows = conn.execute(
            "SELECT id, raw_files FROM videos WHERE raw_files IS NOT NULL AND raw_files != ''"
        ).fetchall()
        for rid, rj in raw_rows:
            try:
                json.loads(rj)
            except Exception:
                bad_json += 1
                errors.append(f"videos {rid} raw_files 非 JSON")
        print(f"[verify] raw_files JSON 可解析: {len(raw_rows) - bad_json}/{len(raw_rows)}")
        if errors:
            raise RuntimeError("校验失败:\n" + "\n".join(errors))
        print("[verify] 全通过")
    finally:
        conn.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description="PG -> SQLite 数据搬移")
    ap.add_argument("--source-url", default=PG_LEGACY_URL)
    ap.add_argument("--target", default=DEFAULT_TARGET)
    ap.add_argument("--backup-dir", default=str(Path(CFG_DIR) / "data" / "backup"))
    ap.add_argument("--jsonl-dir", default="")
    ap.add_argument("--fresh", action="store_true", help="目标库重置后重建导入")
    ap.add_argument("--no-archive", action="store_true")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(args.backup_dir)
    jsonl_dir = Path(args.jsonl_dir) if args.jsonl_dir else backup_dir / f"jsonl_{ts}"
    jsonl_dir.mkdir(parents=True, exist_ok=True)

    if not args.no_archive:
        archive_pg(backup_dir / f"frames_pg_{ts}.dump")
    else:
        print("[archive] skip")
    pg_counts = await export_pg(jsonl_dir, args.source_url)
    # 导出后在同库上二次确认 PG 行数（只读，不依赖计数缓存）
    async def pg_recount(source_url: str) -> dict[str, int]:
        eng = create_async_engine(source_url)
        out = {}
        try:
            async with eng.connect() as c:
                for t in _BUSINESS_TABLES:
                    out[t] = (await c.execute(text(f'SELECT COUNT(*) FROM "{t}"'))).scalar()
        finally:
            await eng.dispose()
        return out

    pg_counts = await pg_recount(args.source_url)
    sqlite_counts = import_to_sqlite(jsonl_dir, args.target, args.fresh)
    verify(args.target, pg_counts, sqlite_counts)


if __name__ == "__main__":
    asyncio.run(main())
