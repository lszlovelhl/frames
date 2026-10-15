"""公共 fixture：三层校验器 / 判定对象构造器。

全部测试走内存隔离：不连真实 PostgreSQL、不写 data/ 下任何业务文件、
不读 .env 密钥。白名单直接用 ENUM_FALLBACK 兜底表 + role_view 固定集，
不依赖 lib_tag 数据库行。
"""
from __future__ import annotations

import pytest

from app.services.three_layer.validate import ENUM_FALLBACK, Validator, Verdict


@pytest.fixture
def whitelists() -> dict[str, set[str]]:
    wl = {k: set(v) for k, v in ENUM_FALLBACK.items()}
    wl["role_view"] = {"编导", "运营", "拍摄", "剪辑", "全员"}
    return wl


@pytest.fixture
def make_validator(whitelists):
    def _make(**kw) -> Validator:
        base = dict(duration_ms=60_000, whitelists=whitelists)
        base.update(kw)
        return Validator(**base)

    return _make


@pytest.fixture
def make_verdict():
    def _make(row_type: str = "lib_method", label: str = "测试积木") -> Verdict:
        return Verdict(row_type=row_type, label=label)

    return _make
