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
