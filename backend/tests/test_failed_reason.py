"""failed_reason 禁止 NULL 回归测试（体检清单 #2）。

覆盖三层防线：
1. models 字段默认值 server_default（未来缺省落 unknown_error）；
2. 所有置 failed 的代码路径必须覆盖 failed_reason（AST 精确扫描，防未来新增路径漏写）；
3. 最新 Alembic 增量迁移包含存量回填 + 默认值。
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FAILED_SRC_FILES = [
    "app/services/analysis.py",
    "app/services/breakdown_jobs.py",
]


def _sets_failed(node: ast.AST) -> bool:
    """函数内是否存在真正"置 failed"的语句（status='failed' 赋值 / mark_failed / mark_done(failed)）。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign):
            for t in sub.targets:
                attr = t.attr if isinstance(t, ast.Attribute) else (t.id if isinstance(t, ast.Name) else "")
                if attr == "status" and isinstance(sub.value, ast.Constant) and sub.value.value == "failed":
                    return True
        elif isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name) and func.id == "mark_failed":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "mark_done":
                kw = {k.arg: k.value for k in sub.keywords}
                if "status" in kw and isinstance(kw["status"], ast.Constant) and kw["status"].value == "failed":
                    return True
    return False


def _covers_reason(node: ast.AST) -> bool:
    """函数内是否已覆盖 failed_reason（显式赋值 failed_reason 或走 mark_failed）。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign):
            for t in sub.targets:
                if isinstance(t, ast.Attribute) and t.attr == "failed_reason":
                    return True
        elif isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name) and func.id == "mark_failed":
                return True
    return False


def test_models_failed_reason_has_server_default():
    from app.models import BreakdownJob

    col = BreakdownJob.__table__.c.failed_reason
    # 列允许空（done 状态无失败原因属正常语义），但必须有默认值兜底未来缺省
    assert col.nullable is True
    assert col.server_default is not None


def test_failed_paths_all_cover_failed_reason():
    bad: list[str] = []
    for rel in FAILED_SRC_FILES:
        p = BACKEND / rel
        src = p.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _sets_failed(node) and not _covers_reason(node):
                bad.append(f"{rel}:{node.lineno} {node.name} 置 failed 但未写 failed_reason")
    assert not bad, "置 failed 但未覆盖 failed_reason 的函数：\n" + "\n".join(bad)


def test_latest_migration_enforces_failed_reason_default():
    versions = BACKEND / "migrations" / "versions"
    files = sorted(versions.glob("*.py"))
    assert files, "migrations/versions 为空"
    latest = files[-1]
    src = latest.read_text(encoding="utf-8")
    assert "down_revision" in src
    assert "breakdown_jobs" in src
    assert "failed_reason" in src
    assert "server_default" in src
    assert "UPDATE breakdown_jobs" in src
