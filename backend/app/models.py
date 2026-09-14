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
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid as _SAUuid,
    UniqueConstraint,
    func,
    text as _sa_text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db import Base


class Uuid(_SAUuid):
    """SQLite 通用 Uuid：bind 层兼容 str 入参（原 PG asyncpg 可直绑 uuid 字符串）"""

    def bind_processor(self, dialect):
        parent = super().bind_processor(dialect)

        def process(value):
            if value is None:
                return None
            if isinstance(value, str):
                value = uuid.UUID(value)
            return parent(value)

        return process


class TimestampMixin:
    """统一时间戳"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CreatedAtMixin:
    """仅创建时间（原料层、句级证据等只追加不改写的数据使用）"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ============================================================
# A 域 · 账户与配置
# ============================================================

class User(TimestampMixin, Base):
    """账号（预留多端/多平台身份）"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    display_name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32), default="全能")  # 编导/运营/拍摄/剪辑/全能
    platforms: Mapped[list] = mapped_column(JSON, default=list)  # 关注平台


class CreditAccount(TimestampMixin, Base):
    """点数账户（1 用户 1 行；当前单机取首个 user 为默认账户）"""

    __tablename__ = "credit_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    balance_points: Mapped[int] = mapped_column(Integer, default=0)
    total_recharged_points: Mapped[int] = mapped_column(Integer, default=0)
    total_consumed_points: Mapped[int] = mapped_column(Integer, default=0)
    free_claimed: Mapped[bool] = mapped_column(Boolean, default=False)


class CreditTransaction(TimestampMixin, Base):
    """点数流水：充值 / 扣点 / 赠送统一记账"""

    __tablename__ = "credit_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("credit_accounts.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String(16))  # recharge / consume / free_grant / refund
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)  # breakdown_short/...
    points: Mapped[int] = mapped_column(Integer)  # 正负：充值+ / 扣点-
    amount_cny: Mapped[float | None] = mapped_column(Float, nullable=True)
    ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ref_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PromptTemplate(TimestampMixin, Base):
    """提示词模板：分层 × 岗位 × 平台，同 code 多版本，active 唯一"""

    __tablename__ = "prompt_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(64), index=True)  # 稳定代号 layer4_content_refine
    name: Mapped[str] = mapped_column(String(128))
    layer: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5，创作类可空
    role_scope: Mapped[list] = mapped_column(JSON, default=list)
    platform_scope: Mapped[list] = mapped_column(JSON, default=list)  # 空=通用
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/draft/archived
    group_key: Mapped[str | None] = mapped_column(String(32), nullable=True)  # A/B


class Product(TimestampMixin, Base):
    """产品库条目：创作标的物（参数 + 卖点 + 内容赛道对齐）

    specs / selling_points 结构约定（前端与创作注入依赖）：
    - specs: [{ "k": "屏幕", "v": "6.7 英寸 OLED" }, ...]  保持顺序
    - selling_points: [{ "title": "...", "detail": "..." }, ...]
    category_tags 与内容赛道对齐（多赛道），industry 为产品行业一级分类。
    """

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    industry: Mapped[str] = mapped_column(String(32), index=True)  # 汽车/数码3C/美妆个护...
    category_tags: Mapped[list] = mapped_column(JSON, default=list)  # 适用内容赛道（对齐 CATEGORY_TAXONOMY）
    brand: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(256))  # 型号/产品名（如「问界 M9」「iPhone 17 Pro」）
    series: Mapped[str | None] = mapped_column(String(256), nullable=True)  # 系列/别名/细分版本
    headline: Mapped[str] = mapped_column(String(512))  # 一句话种草点/核心记忆点
    price_range: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 价格区间文本，如 46.98-56.98 万
    specs: Mapped[list] = mapped_column(JSON, default=list)
    selling_points: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/draft/archived
    source: Mapped[str] = mapped_column(String(16), default="seed")  # seed/manual/ai


# ============================================================
# B 域 · 素材建档（外围）
# ============================================================

class Video(TimestampMixin, Base):
    """一条被拆解的视频主记录"""

    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    platform: Mapped[str] = mapped_column(String(32), index=True)  # bilibili/douyin/...
    platform_video_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(512))
    author_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    author_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    author_avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_fans: Mapped[int | None] = mapped_column(Integer, nullable=True)
    author_likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publish_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tags: Mapped[list] = mapped_column(JSON, default=list)
    stats_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # 首轮抓取基准快照
    stats_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stats_baseline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    subtitle_source: Mapped[str] = mapped_column(String(32), default="")  # 平台CC/语音转写/手动
    raw_files: Mapped[dict] = mapped_column(JSON, default=dict)  # 本地缓存路径
    category_guess: Mapped[str | None] = mapped_column(String(64), nullable=True)


class VideoStat(Base):
    """数据表现时间序列（运营回流统计）"""

    __tablename__ = "video_stats_history"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("videos.id", ondelete="CASCADE"), index=True
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
    extra: Mapped[dict] = mapped_column(JSON, default=dict)  # 平台附加指标


class VideoComment(Base):
    """平台热评快照（每次刷新 upsert，保留最近抓取的热评）"""

    __tablename__ = "video_comments"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    comment_id: Mapped[str] = mapped_column(String(64))  # 平台评论 id（如 rpid）
    user_id: Mapped[str] = mapped_column(String(64), default="")
    user_name: Mapped[str] = mapped_column(String(128), default="")
    user_avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    is_top: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否 UP 置顶
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("video_id", "comment_id", name="uq_video_comment"),
    )


# ============================================================
# C 域 · 拆解（三层分库链路：队列与进度承载）
# ============================================================

class BreakdownJob(TimestampMixin, Base):
    """一次三层分库拆解任务：队列 + 进度 + 证据的唯一承载表。

    第 7 章清空时已 DROP 旧表 ``analyses``（连同 analysis_layers / segments /
    analysis_notes / elements / element_versions / annotations / category_templates），
    拆解任务改由本表承载：

    - 队列：status = queued → running → done / partial / failed
    - 进度：progress 写入 stage / message / pct 三列（前端轮询 /api/analyses/active 常驻展示）
    - 结果：脚本产物落在 script_* / lib_* / ref_*（见 G 域），evidence 只存校验证据
    """

    __tablename__ = "breakdown_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default="queued", server_default=_sa_text("'queued'")
    )  # queued/running/done/partial/failed
    stage: Mapped[str] = mapped_column(
        String(32), default="", server_default=_sa_text("''")
    )  # media / three_l1 / ... / done
    message: Mapped[str] = mapped_column(
        Text, default="", server_default=_sa_text("''")
    )
    pct: Mapped[int] = mapped_column(Integer, default=0, server_default=_sa_text("0"))
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    script_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    evidence: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=_sa_text("'{}'")
    )
    failed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','done','partial','failed')",
            name="ck_breakdown_job_status",
        ),
        Index("ix_breakdown_job_video", "video_id", "created_at"),
        Index("ix_breakdown_job_status", "status"),
    )


# ============================================================
# F 域 · AI 用量与计费
# ============================================================

class AiProvider(TimestampMixin, Base):
    """AI 服务商（api-key 集合）：用量卡片按此聚合展示。

    每服务商持有自己的 base_url + api_key + 模型列表(models JSON)。
    业务层请求只传档位 alias(flash/pro/vision/其他)，由网关按
    provider_priority 顺序取首个已启用且带 key 的服务商内匹配 kind 的模型。
    """

    __tablename__ = "ai_providers"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # deepseek/doubao/moonshot/...
    name: Mapped[str] = mapped_column(String(64))  # 展示名：DeepSeek / 豆包 / Kimi...
    base_url: Mapped[str] = mapped_column(String(256))  # OpenAI 兼容地址（不含 /chat/completions）
    api_key: Mapped[str] = mapped_column(Text, default="")
    models: Mapped[list] = mapped_column(JSON, default=list)
    # [{"id": "deepseek-v4-flash", "kind": "flash", "label": "DeepSeek V4 Flash"}]
    priority: Mapped[int] = mapped_column(Integer, default=100)  # 越小越优先
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    brand_color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    topup_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # 官方充值入口
    balance_cny: Mapped[float | None] = mapped_column(Float, nullable=True)  # 最近一次查询余额（元）
    balance_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    balance_manual: Mapped[bool] = mapped_column(Boolean, default=False)  # True=手动维护余额
    balance_warn_threshold: Mapped[float] = mapped_column(Float, default=10.0)  # 不足预警线（元）


class AiUsageLog(TimestampMixin, Base):
    """AI 网关每次调用的用量日志（token 数与估算成本，供计费可视化）"""

    __tablename__ = "ai_usage_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(32), default="deepseek", index=True)
    scene: Mapped[str] = mapped_column(String(32), default="misc", index=True)  # breakdown/creation/creation_edit/vision/element_mix/manual...
    ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # analysis/creation/video/...
    ref_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True, index=True
    )
    alias: Mapped[str] = mapped_column(String(32))  # flash/pro/vision（请求档位）
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 实际返回模型名
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_cny: Mapped[float | None] = mapped_column(Float, nullable=True)  # 估算成本（元）
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


# ============================================================
# E 域 · 创作
# ============================================================

class Creation(TimestampMixin, Base):
    """创作项目主记录：从元素组合出发"""

    __tablename__ = "creations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(256))
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 涨粉/带货/品牌曝光...
    core_elements: Mapped[list] = mapped_column(JSON, default=list)  # [{element_id, role, order}]
    status: Mapped[str] = mapped_column(String(16), default="idea")  # idea/drafting/produced/published/archived
    ref_video_ids: Mapped[list] = mapped_column(JSON, default=list)


class CreationAsset(TimestampMixin, Base):
    """创作资产：多岗位中间与最终产物"""

    __tablename__ = "creation_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    creation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("creations.id", ondelete="CASCADE"), index=True
    )
    asset_type: Mapped[str] = mapped_column(String(32))  # topic_plan/script/shot_list/shooting_guide/edit_guide/title_options/publish_copy/thumbnail_idea
    role_view: Mapped[str] = mapped_column(String(32), default="编导")
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("creation_assets.id", ondelete="CASCADE"),
        nullable=True,
    )
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)


class CreationPublish(TimestampMixin, Base):
    """发布与回流：作品上线数据接回飞轮"""

    __tablename__ = "creation_publishes"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    creation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("creations.id", ondelete="CASCADE"), index=True
    )
    publish_platform: Mapped[str] = mapped_column(String(32))
    publish_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    stats_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ============================================================
# G 域 · 三层物理分库（重构）
# 第一层 raw_*（保真原料，只存事实）／第二层 script_*（本片脚本，不跨片）
# 第三层 lib_*（可复用积木，跨片沉淀）＋ ref_*（溯源）
# 依据《帧间拆解重构设计方案与DDL草案》第 3~5 章；旧表原地保留，本域只增量新建
# ============================================================

# ---------------- 第一层 · 保真原料层 raw_* ----------------

class RawMediaManifest(TimestampMixin, Base):
    """一条原视频的采集清单与文件落点（1 视频 : 1 清单）"""

    __tablename__ = "raw_media_manifest"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("videos.id", ondelete="CASCADE"), unique=True
    )
    platform: Mapped[str] = mapped_column(String(32))
    work_dir: Mapped[str] = mapped_column(Text)
    video_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    subtitle_source: Mapped[str] = mapped_column(String(32))
    asr_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    asr_pipeline_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    warnings: Mapped[list] = mapped_column(
        JSON, default=list, server_default=_sa_text("'[]'")
    )


class RawTranscriptSentence(CreatedAtMixin, Base):
    """逐句转写文本 + 起止毫秒（原料层唯一事实源，禁改写）"""

    __tablename__ = "raw_transcript_sentence"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("videos.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)  # 1-based，与提示词 #03 行号一一对应
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))  # asr/manual/ocr
    proofread: Mapped[int] = mapped_column(Integer, default=0, server_default=_sa_text("0"))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("video_id", "seq", name="uq_raw_sentence_video_seq"),
        CheckConstraint("length(trim(text)) > 0", name="ck_raw_sentence_text"),
        CheckConstraint(
            "source IN ('asr','manual','ocr')", name="ck_raw_sentence_source"
        ),
        Index("ix_raw_sentence_video_time", "video_id", "start_ms"),
    )


class RawShot(CreatedAtMixin, Base):
    """镜头/关键帧序号、时点与画面客观描述（画面描述只允许存在于此表）"""

    __tablename__ = "raw_shot"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("videos.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # anchor/key/frame
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    desc: Mapped[str] = mapped_column(Text)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_overlay: Mapped[str | None] = mapped_column(Text, nullable=True)
    style: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("video_id", "seq", name="uq_raw_shot_video_seq"),
        CheckConstraint("kind IN ('anchor','key','frame')", name="ck_raw_shot_kind"),
    )


class RawAudioProfile(CreatedAtMixin, Base):
    """音频物理量（BPM、平均音量、BGM 可用性），1 视频 : 1 行"""

    __tablename__ = "raw_audio_profile"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("videos.id", ondelete="CASCADE"), unique=True
    )
    bpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mean_volume_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_bgm: Mapped[int] = mapped_column(Integer, default=0, server_default=_sa_text("0"))
    bgm_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class RawAudioEnergySample(CreatedAtMixin, Base):
    """音频能量等间隔采样序列（情绪曲线的客观锚）"""

    __tablename__ = "raw_audio_energy_sample"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("videos.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    t_ms: Mapped[int] = mapped_column(Integer)
    energy: Mapped[float] = mapped_column(Float)  # 归一化 0~1

    __table_args__ = (
        UniqueConstraint("video_id", "seq", name="uq_raw_energy_video_seq"),
    )


# ---------------- 第二层 · 本片脚本层 script_* ----------------

class ScriptScript(TimestampMixin, Base):
    """本片脚本主表：中心思想、内容走向、时长（1 视频 : 1 脚本）"""

    __tablename__ = "script_script"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("videos.id", ondelete="CASCADE"), unique=True
    )
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    core_idea: Mapped[str] = mapped_column(Text)
    content_trend: Mapped[str] = mapped_column(Text)
    target_audience: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    sentence_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=_sa_text("0")
    )
    status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default=_sa_text("'draft'")
    )
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "length(trim(core_idea)) > 0", name="ck_script_script_core_idea"
        ),
        CheckConstraint(
            "status IN ('draft','active','archived')", name="ck_script_script_status"
        ),
    )


class ScriptSegment(CreatedAtMixin, Base):
    """时间轴段落切分（钩子/铺垫/…/CTA），对齐 script_segment"""

    __tablename__ = "script_segment"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    script_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("script_script.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    seg_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    start_sentence_seq: Mapped[int] = mapped_column(Integer)
    end_sentence_seq: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    hook_point: Mapped[int] = mapped_column(Integer, default=0, server_default=_sa_text("0"))
    payoff_point: Mapped[int] = mapped_column(
        Integer, default=0, server_default=_sa_text("0")
    )
    emotion_peak: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("script_id", "seq", name="uq_script_segment_script_seq"),
        CheckConstraint(
            "seg_type IN ('钩子','铺垫','冲突','转折','高潮','干货','CTA')",
            name="ck_script_segment_seg_type",
        ),
        Index("ix_script_segment_script", "script_id", "seq"),
    )


class ScriptSentence(TimestampMixin, Base):
    """句子级还原（最小资产单元）：原话 + 秒级时间锚 + 原料回指三件套"""

    __tablename__ = "script_sentence"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    script_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("script_script.id", ondelete="CASCADE")
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("script_segment.id", ondelete="SET NULL"),
        nullable=True,
    )
    seq: Mapped[int] = mapped_column(Integer)
    raw_sentence_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("raw_transcript_sentence.id")
    )
    source_video_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    quote: Mapped[str] = mapped_column(Text)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    sentence_function: Mapped[str] = mapped_column(String(32))
    function_reason: Mapped[str] = mapped_column(Text)
    method_refs: Mapped[list] = mapped_column(
        JSON, default=list, server_default=_sa_text("'[]'")
    )
    emotion_intensity: Mapped[float] = mapped_column(Float)
    is_hook: Mapped[int] = mapped_column(Integer, default=0, server_default=_sa_text("0"))
    is_turn: Mapped[int] = mapped_column(Integer, default=0, server_default=_sa_text("0"))
    is_peak: Mapped[int] = mapped_column(Integer, default=0, server_default=_sa_text("0"))
    variants: Mapped[list] = mapped_column(
        JSON, default=list, server_default=_sa_text("'[]'")
    )
    imagination: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("script_id", "seq", name="uq_script_sentence_script_seq"),
        CheckConstraint("length(trim(quote)) >= 8", name="ck_script_sentence_quote"),
        CheckConstraint(
            "sentence_function IN ('钩子','铺垫','冲突','转折','高潮','干货','CTA','过渡','收尾')",
            name="ck_script_sentence_function",
        ),
        CheckConstraint(
            "emotion_intensity >= 0 AND emotion_intensity <= 10",
            name="ck_script_sentence_intensity",
        ),
        CheckConstraint(
            "end_ms = 0 OR end_ms >= start_ms", name="ck_script_sentence_time"
        ),
        Index("ix_script_sentence_script", "script_id", "seq"),
        Index("ix_script_sentence_video", "source_video_id"),
        Index("ix_script_sentence_seg", "segment_id"),
    )


class ScriptEmotionCurve(TimestampMixin, Base):
    """连续情绪曲线：等间隔采样序列 + 峰谷统计 + 形状枚举 + 峰值落点"""

    __tablename__ = "script_emotion_curve"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    script_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("script_script.id", ondelete="CASCADE"), unique=True
    )
    sample_interval_ms: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    sample_count: Mapped[int] = mapped_column(Integer)
    intensity_series: Mapped[list] = mapped_column(JSON)
    series_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    series_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    shape: Mapped[str] = mapped_column(String(32))
    peak_position_ratio: Mapped[float] = mapped_column(Float)
    peak_count: Mapped[int] = mapped_column(Integer, default=0, server_default=_sa_text("0"))
    valley_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=_sa_text("0")
    )
    baseline_intensity: Mapped[float | None] = mapped_column(Float, nullable=True)
    method: Mapped[str] = mapped_column(
        String(16), default="model", server_default=_sa_text("'model'")
    )

    __table_args__ = (
        CheckConstraint(
            "shape IN ('单峰','双峰','递进上升','波浪','骤升缓降','前高后低','平缓')",
            name="ck_script_curve_shape",
        ),
        CheckConstraint(
            "method IN ('model','audio_energy','hybrid')", name="ck_script_curve_method"
        ),
    )


class ScriptTurnPoint(CreatedAtMixin, Base):
    """峰谷点、情绪反转点与对应句子"""

    __tablename__ = "script_turn_point"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    script_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("script_script.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    turn_type: Mapped[str] = mapped_column(String(32))  # 峰/谷/反转/悬念
    t_ms: Mapped[int] = mapped_column(Integer)
    intensity: Mapped[float] = mapped_column(Float)
    sentence_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("script_sentence.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("script_id", "seq", name="uq_script_turn_script_seq"),
        CheckConstraint(
            "turn_type IN ('峰','谷','反转','悬念')", name="ck_script_turn_type"
        ),
    )


class ScriptEvidence(CreatedAtMixin, Base):
    """句子级证据链（原话 + 秒数 + 原料回指）"""

    __tablename__ = "script_evidence"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    script_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("script_script.id", ondelete="CASCADE")
    )
    sentence_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("script_sentence.id", ondelete="CASCADE")
    )
    evidence_type: Mapped[str] = mapped_column(String(16))
    raw_sentence_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    raw_shot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    source_video_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    quote: Mapped[str] = mapped_column(Text)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint(
            "evidence_type IN ('transcript','ocr','frame','audio')",
            name="ck_script_evidence_type",
        ),
        Index("ix_script_evidence_sent", "sentence_id"),
    )


# ---------------- 第三层 · 可复用积木库层 lib_* ----------------

class LibTag(CreatedAtMixin, Base):
    """受控词表：所有枚举类字段的唯一权威登记处（含 emergent 开放位）"""

    __tablename__ = "lib_tag"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(64))
    dimension: Mapped[str] = mapped_column(String(32))
    is_controlled: Mapped[int] = mapped_column(
        Integer, default=0, server_default=_sa_text("0")
    )
    is_emergent: Mapped[int] = mapped_column(
        Integer, default=0, server_default=_sa_text("0")
    )
    parent_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_lib_tag_dimension", "dimension", "is_controlled"),)


class LibTopic(TimestampMixin, Base):
    """选题库：选题角度、人群、痛点、价值类型"""

    __tablename__ = "lib_topic"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    topic_type: Mapped[str] = mapped_column(String(32))
    audience: Mapped[str] = mapped_column(Text)
    pain_point: Mapped[str] = mapped_column(Text)
    angle: Mapped[str] = mapped_column(Text)
    value_type: Mapped[str] = mapped_column(String(16))
    applicable_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    keywords: Mapped[list] = mapped_column(
        JSON, default=list, server_default=_sa_text("'[]'")
    )
    mechanism: Mapped[str] = mapped_column(Text)
    variants: Mapped[list] = mapped_column(JSON)
    imagination: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default=_sa_text("'active'")
    )
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default=_sa_text("'draft'")
    )  # 元素质控：draft/accepted/adjusted/rejected（前端元素库四态）

    __table_args__ = (
        CheckConstraint(
            "value_type IN ('实用','情绪','娱乐')", name="ck_lib_topic_value_type"
        ),
        CheckConstraint(
            "status IN ('active','archived')", name="ck_lib_topic_status"
        ),
        Index("ix_lib_topic_status_type", "status", "topic_type"),
    )


class LibHook(TimestampMixin, Base):
    """钩子库：钩型、句式模板、机制、变体"""

    __tablename__ = "lib_hook"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    hook_type: Mapped[str] = mapped_column(String(32))
    position: Mapped[str] = mapped_column(String(16))
    sentence_pattern: Mapped[str] = mapped_column(Text)
    variables: Mapped[list] = mapped_column(
        JSON, default=list, server_default=_sa_text("'[]'")
    )
    expected_effect: Mapped[str] = mapped_column(Text)
    mechanism: Mapped[str] = mapped_column(Text)
    variants: Mapped[list] = mapped_column(JSON)
    imagination: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default=_sa_text("'active'")
    )
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default=_sa_text("'draft'")
    )  # 元素质控：draft/accepted/adjusted/rejected（前端元素库四态）

    __table_args__ = (
        CheckConstraint(
            "position IN ('前3秒','片中','结尾')", name="ck_lib_hook_position"
        ),
        CheckConstraint("status IN ('active','archived')", name="ck_lib_hook_status"),
        Index("ix_lib_hook_status_type", "status", "hook_type"),
    )


class LibCopywriting(TimestampMixin, Base):
    """文案库：口播/字幕/标题/CTA 句式与修辞"""

    __tablename__ = "lib_copywriting"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    copy_type: Mapped[str] = mapped_column(String(32))
    sentence_pattern: Mapped[str] = mapped_column(Text)
    rhetoric: Mapped[str] = mapped_column(String(32))
    example_text: Mapped[str] = mapped_column(Text)
    mechanism: Mapped[str] = mapped_column(Text)
    variants: Mapped[list] = mapped_column(JSON)
    imagination: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default=_sa_text("'active'")
    )
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default=_sa_text("'draft'")
    )  # 元素质控：draft/accepted/adjusted/rejected（前端元素库四态）

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','archived')", name="ck_lib_copywriting_status"
        ),
        Index("ix_lib_copywriting_status_type", "status", "copy_type"),
    )


class LibQuote(TimestampMixin, Base):
    """金句库：金句原句快照 + 句式结构 + 改写模板"""

    __tablename__ = "lib_quote"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    text: Mapped[str] = mapped_column(Text)
    structure: Mapped[str] = mapped_column(Text)
    rewrite_template: Mapped[str] = mapped_column(Text)
    applicable_scene: Mapped[str] = mapped_column(Text)
    mechanism: Mapped[str] = mapped_column(Text)
    variants: Mapped[list] = mapped_column(JSON)
    imagination: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default=_sa_text("'active'")
    )
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default=_sa_text("'draft'")
    )  # 元素质控：draft/accepted/adjusted/rejected（前端元素库四态）

    __table_args__ = (
        CheckConstraint("status IN ('active','archived')", name="ck_lib_quote_status"),
        Index("ix_lib_quote_status", "status"),
    )


class LibMethod(TimestampMixin, Base):
    """手法库（核心）：受控标签 + emergent 开放位 + 机制 + 操作步骤 + 发散"""

    __tablename__ = "lib_method"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)  # {类}.{域}.{短名}
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32))
    controlled_tag: Mapped[str] = mapped_column(String(32))
    is_emergent: Mapped[int] = mapped_column(
        Integer, default=0, server_default=_sa_text("0")
    )
    emergent_parent_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mechanism: Mapped[str] = mapped_column(Text)
    abstraction_level: Mapped[str] = mapped_column(String(16))
    usage_steps: Mapped[str] = mapped_column(Text)
    counter_example: Mapped[str] = mapped_column(Text)
    variants: Mapped[list] = mapped_column(JSON)
    imagination: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default=_sa_text("'active'")
    )
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default=_sa_text("'draft'")
    )  # 元素质控：draft/accepted/adjusted/rejected（前端元素库四态）

    __table_args__ = (
        CheckConstraint(
            "abstraction_level IN ('句法级','段落级','全片级')",
            name="ck_lib_method_abstraction",
        ),
        CheckConstraint("status IN ('active','archived')", name="ck_lib_method_status"),
        Index("ix_lib_method_status_tag", "status", "controlled_tag"),
    )


class LibCombo(TimestampMixin, Base):
    """组合模板库：意图 + 中心思想对齐 + 整体曲线 + 内容走向 + 槽位统计"""

    __tablename__ = "lib_combo"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    intent: Mapped[str] = mapped_column(String(32))
    core_idea_alignment: Mapped[str] = mapped_column(Text)
    content_trend: Mapped[str] = mapped_column(Text)
    sequence_desc: Mapped[str] = mapped_column(Text)
    emotion_shape: Mapped[str] = mapped_column(String(32))
    emotion_curve: Mapped[list] = mapped_column(JSON)
    duration_ratio: Mapped[list] = mapped_column(JSON)
    applicable_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    slot_count: Mapped[int] = mapped_column(Integer)
    fixed_slot_count: Mapped[int] = mapped_column(Integer)
    swap_slot_count: Mapped[int] = mapped_column(Integer)
    mechanism: Mapped[str] = mapped_column(Text)
    variants: Mapped[list] = mapped_column(JSON)
    imagination: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default=_sa_text("'active'")
    )
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default=_sa_text("'draft'")
    )  # 元素质控：draft/accepted/adjusted/rejected（前端元素库四态）

    __table_args__ = (
        CheckConstraint(
            "emotion_shape IN ('单峰','双峰','递进上升','波浪','骤升缓降','前高后低','平缓')",
            name="ck_lib_combo_shape",
        ),
        CheckConstraint("status IN ('active','archived')", name="ck_lib_combo_status"),
        Index("ix_lib_combo_status_intent", "status", "intent"),
    )


class LibComboSlot(CreatedAtMixin, Base):
    """组合槽位：有序槽位 + 固定位/可替换位 + 位置与时长占比"""

    __tablename__ = "lib_combo_slot"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    combo_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("lib_combo.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    slot_role: Mapped[str] = mapped_column(String(16))  # 固定/可替换
    method_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    method_code: Mapped[str] = mapped_column(String(64))
    expected_function: Mapped[str] = mapped_column(Text)
    position_ratio_start: Mapped[float] = mapped_column(Float)
    position_ratio_end: Mapped[float] = mapped_column(Float)
    duration_ratio: Mapped[float] = mapped_column(Float)
    swap_alternatives: Mapped[list] = mapped_column(
        JSON, default=list, server_default=_sa_text("'[]'")
    )

    __table_args__ = (
        UniqueConstraint("combo_id", "seq", name="uq_lib_combo_slot_combo_seq"),
        CheckConstraint(
            "slot_role IN ('固定','可替换')", name="ck_lib_combo_slot_role"
        ),
    )


class RefElementSource(Base):
    """积木 → 原片/脚本/句子的来源溯源（逻辑引用，不设 FK）"""

    __tablename__ = "ref_element_source"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    element_table: Mapped[str] = mapped_column(String(32))
    element_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_script_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_video_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_sentence_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    source_segment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    quote: Mapped[str] = mapped_column(Text)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    source_platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "element_table IN ('lib_topic','lib_hook','lib_copywriting','lib_quote','lib_method','lib_combo')",
            name="ck_ref_element_table",
        ),
        Index("ix_ref_element", "element_table", "element_id"),
        Index("ix_ref_video", "source_video_id"),
    )


class LibMixDraft(TimestampMixin, Base):
    """AI 组合 / 变异产出的元素草稿（旧 elements 表 DROP 后的落库载体）

    质控四态与积木库保持一致（review_status）；来源表 ref_element_source 不覆盖
    本表（CheckConstraint 仅允许 6 张积木库表），溯源信息存 ref_element_ids。
    """

    __tablename__ = "lib_mix_draft"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    category: Mapped[str] = mapped_column(String(32), default="综合")
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), default="combo")
    role_view: Mapped[str] = mapped_column(String(32), default="编导")
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default=_sa_text("'draft'")
    )
    tags: Mapped[list] = mapped_column(JSON, default=list, server_default=_sa_text("'[]'"))
    ref_element_ids: Mapped[list] = mapped_column(
        JSON, default=list, server_default=_sa_text("'[]'")
    )

    __table_args__ = (
        CheckConstraint(
            "review_status IN ('draft','accepted','adjusted','rejected')",
            name="ck_lib_mix_draft_status",
        ),
        Index("ix_lib_mix_draft_status", "review_status"),
    )
