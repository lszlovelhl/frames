"""帧间 数据模型 — v0.3
对齐 docs/04-data-model.md（6 域 16 张表）

设计要点：
- 元素为中心：elements 是拆解/标注/变异/创作的枢纽
- 观察与推断分离：source_type(observed/inferred) + confidence
- 岗位打标：role_view 标记主要受益岗位（编导/运营/拍摄/剪辑）
- 证据可回溯：时间戳、原文引用、附件路径均保留
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db import Base


class TimestampMixin:
    """统一时间戳"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ============================================================
# A 域 · 账户与配置
# ============================================================

class User(TimestampMixin, Base):
    """账号（预留多端/多平台身份）"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    display_name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32), default="全能")  # 编导/运营/拍摄/剪辑/全能
    platforms: Mapped[list] = mapped_column(JSONB, default=list)  # 关注平台


class ApiKey(TimestampMixin, Base):
    """模型 API Key（端侧加密存储）"""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32))  # deepseek / openai / ollama ...
    key_encrypted: Mapped[str] = mapped_column(Text)  # 加密后内容
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_default: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PromptTemplate(TimestampMixin, Base):
    """提示词模板：分层 × 岗位 × 平台，同 code 多版本，active 唯一"""

    __tablename__ = "prompt_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(64), index=True)  # 稳定代号 layer4_content_refine
    name: Mapped[str] = mapped_column(String(128))
    layer: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5，创作类可空
    role_scope: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    platform_scope: Mapped[list] = mapped_column(ARRAY(Text), default=list)  # 空=通用
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/draft/archived
    group_key: Mapped[str | None] = mapped_column(String(32), nullable=True)  # A/B


class CategoryTemplate(TimestampMixin, Base):
    """品类模板：结构套路 + 元素优先级 + 样例视频"""

    __tablename__ = "category_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    category: Mapped[str] = mapped_column(String(64), index=True)  # 知识口播/剧情短剧/美妆测评...
    name: Mapped[str] = mapped_column(String(128))
    structure: Mapped[dict] = mapped_column(JSONB, default=dict)  # 段落顺序、时长占比建议
    element_priorities: Mapped[dict] = mapped_column(JSONB, default=dict)
    sample_video_ids: Mapped[list] = mapped_column(JSONB, default=list)
    owner_role: Mapped[str] = mapped_column(String(32), default="编导")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ============================================================
# B 域 · 素材建档（外围）
# ============================================================

class Video(TimestampMixin, Base):
    """一条被拆解的视频主记录"""

    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    platform: Mapped[str] = mapped_column(String(32), index=True)  # bilibili/douyin/...
    platform_video_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(512))
    author_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    author_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publish_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    stats_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)  # 建档时快照
    subtitle_source: Mapped[str] = mapped_column(String(32), default="")  # 平台CC/语音转写/手动
    raw_files: Mapped[dict] = mapped_column(JSONB, default=dict)  # 本地缓存路径
    category_guess: Mapped[str | None] = mapped_column(String(64), nullable=True)


class VideoStat(Base):
    """数据表现时间序列（运营回流统计）"""

    __tablename__ = "video_stats_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    play_count: Mapped[int] = mapped_column(Integer, default=0)
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    collect_count: Mapped[int] = mapped_column(Integer, default=0)
    share_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    danmaku_count: Mapped[int] = mapped_column(Integer, default=0)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)  # 平台附加指标


# ============================================================
# C 域 · 拆解
# ============================================================

class Analysis(TimestampMixin, Base):
    """一次拆解任务主记录"""

    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    category_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("category_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/done/reviewed
    current_layer: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)  # 选题/主题/一句话结论
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviewed_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)  # 模型/提示词版本/耗时


class AnalysisLayer(TimestampMixin, Base):
    """五层拆解的层级产出（一层多步，step 区分）"""

    __tablename__ = "analysis_layers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    layer: Mapped[int] = mapped_column(Integer)  # 1-5
    step: Mapped[str] = mapped_column(String(64), default="main")
    role_view: Mapped[str] = mapped_column(String(32), default="全员")
    content: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_type: Mapped[str] = mapped_column(String(16), default="inferred")  # observed/inferred
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)  # 审计用


class Segment(TimestampMixin, Base):
    """结构线（第 3 层主产物）：时间轴段落"""

    __tablename__ = "segments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, default=0)
    seg_type: Mapped[str] = mapped_column(String(32))  # 钩子/铺垫/冲突/转折/高潮/干货/CTA
    title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    start_ms: Mapped[int] = mapped_column(Integer, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, default=0)
    hook_point: Mapped[bool] = mapped_column(Boolean, default=False)
    payoff_point: Mapped[bool] = mapped_column(Boolean, default=False)
    emotion_curve: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-10
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_view: Mapped[str] = mapped_column(String(32), default="编导")


class AnalysisNote(TimestampMixin, Base):
    """精拆细节（第 4 层主产物）：时间轴上的一条具体观察"""

    __tablename__ = "analysis_notes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("segments.id", ondelete="SET NULL"), nullable=True
    )
    note_type: Mapped[str] = mapped_column(String(32))  # transcript/shot/audio/text_overlay/rhythm/frame
    start_ms: Mapped[int] = mapped_column(Integer, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, default=0)
    role_view: Mapped[str] = mapped_column(String(32), default="全员")
    content: Mapped[str] = mapped_column(Text)
    attachment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)


# ============================================================
# D 域 · 元素与变异（知识层 / 飞轮心脏）
# ============================================================

class Element(TimestampMixin, Base):
    """元素卡片：第 5 层提炼 / 人工标注 / 跨片归纳，一切创作变异的原料"""

    __tablename__ = "elements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(32), index=True)  # 选题/钩子/结构/话术/情绪/视觉/剪辑手法/声音设计/运营策略
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    formula: Mapped[str | None] = mapped_column(Text, nullable=True)  # 可复述配方
    source_type: Mapped[str] = mapped_column(String(16), default="inferred")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    role_view: Mapped[str] = mapped_column(String(32), default="全员")
    evidence: Mapped[list] = mapped_column(JSONB, default=list)  # [{analysis_id, seg, note_id, time, quote}]
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/accepted/adjusted/rejected
    tags: Mapped[list] = mapped_column(JSONB, default=list)


class ElementVersion(TimestampMixin, Base):
    """元素变异记录：改编/换壳/组合的每次变化"""

    __tablename__ = "element_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    element_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("elements.id", ondelete="CASCADE"), index=True
    )
    version_seq: Mapped[int] = mapped_column(Integer, default=1)
    change_kind: Mapped[str] = mapped_column(String(32))  # 换场景/换对象/换话术/跨平台适配/组合
    new_formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    used_by_creation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("creations.id", ondelete="SET NULL"),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Annotation(TimestampMixin, Base):
    """人工标注样本（AI 之外的眼睛），用于校准与微调"""

    __tablename__ = "annotations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    video_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=True
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=True,
    )
    annotator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    element_category: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(Text)
    start_ms: Mapped[int] = mapped_column(Integer, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual/ai_adjusted


# ============================================================
# E 域 · 创作
# ============================================================

class Creation(TimestampMixin, Base):
    """创作项目主记录：从元素组合出发"""

    __tablename__ = "creations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(256))
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 涨粉/带货/品牌曝光...
    core_elements: Mapped[list] = mapped_column(JSONB, default=list)  # [{element_id, role, order}]
    status: Mapped[str] = mapped_column(String(16), default="idea")  # idea/drafting/produced/published/archived
    ref_video_ids: Mapped[list] = mapped_column(JSONB, default=list)


class CreationAsset(TimestampMixin, Base):
    """创作资产：多岗位中间与最终产物"""

    __tablename__ = "creation_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    creation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("creations.id", ondelete="CASCADE"), index=True
    )
    asset_type: Mapped[str] = mapped_column(String(32))  # topic_plan/script/shot_list/shooting_guide/edit_guide/title_options/publish_copy/thumbnail_idea
    role_view: Mapped[str] = mapped_column(String(32), default="编导")
    content: Mapped[dict] = mapped_column(JSONB, default=dict)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("creation_assets.id", ondelete="CASCADE"),
        nullable=True,
    )
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)


class CreationPublish(TimestampMixin, Base):
    """发布与回流：作品上线数据接回飞轮"""

    __tablename__ = "creation_publishes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    creation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("creations.id", ondelete="CASCADE"), index=True
    )
    publish_platform: Mapped[str] = mapped_column(String(32))
    publish_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    stats_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
