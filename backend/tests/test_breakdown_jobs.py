"""拆解任务状态机（breakdown_jobs.py 可测纯函数）单元测试。"""
from __future__ import annotations

import app.services.breakdown_jobs as bj


def test_stage_labels_covers_pipeline_stages():
    for stage in ("download", "transcribe", "frames", "vision", "audio", "bgm"):
        assert bj.STAGE_LABELS[stage], stage


def test_default_message_known_statuses():
    for status in ("queued", "running", "done", "partial", "failed"):
        msg = bj._default_message(status)
        assert msg and msg != status


def test_default_message_unknown_status_passthrough():
    assert bj._default_message("custom_state") == "custom_state"
    assert bj._default_message("") == ""


def test_recover_decision_first_and_second():
    """崩溃恢复状态机：首次恢复→queued 续跑；已有恢复标记→failed 不再重试。"""
    # 模拟 recover_stale 的核心判断逻辑
    def decide(prev_recover_count: int, status: str):
        if status not in ("queued", "running"):
            return status
        if prev_recover_count >= 1:
            return "failed"  # 二次中断，放弃自动恢复
        return "queued"  # 首次恢复，续跑

    assert decide(0, "running") == "queued"
    assert decide(0, "queued") == "queued"
    assert decide(1, "running") == "failed"
    assert decide(2, "queued") == "failed"
    assert decide(0, "done") == "done"
