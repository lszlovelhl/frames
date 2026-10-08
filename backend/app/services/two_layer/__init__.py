"""
两层简化 pipeline：
  L1：一次调用出整体框架（结构化 JSON）
  Harness：确定性后处理
  L2：一次调用出完整成稿（Markdown 编导脚本）
"""

from .pipeline import run_pipeline

__all__ = ["run_pipeline"]
