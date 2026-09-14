"""add three-layer split: raw_*/script_*/lib_*/ref_* (20 tables) + lib_tag seed

Revision ID: e2f3a4b5c6d7
Revises: d1a0b2c3e4f5
Create Date: 2026-09-11 10:00:00.000000

依据《帧间拆解重构设计方案与DDL草案》第 3~5 章：
- 第一层 raw_*（5 表）：保真原料，只存事实
- 第二层 script_*（6 表）：本片脚本，不跨片
- 第三层 lib_*（8 表）：可复用积木（6 类内容 + lib_tag 词表 + lib_combo_slot 槽位）
- 溯源 ref_element_source（1 表）
边界：仅增量新建，不改动任何既有表结构与数据。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import uuid as _uuid


# revision identifiers, used by Alembic.
revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, Sequence[str], None] = 'd1a0b2c3e4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_TABLES = [
    # 建表顺序（父表在前）
    'raw_media_manifest',
    'raw_transcript_sentence',
    'raw_shot',
    'raw_audio_profile',
    'raw_audio_energy_sample',
    'script_script',
    'script_segment',
    'script_sentence',
    'script_emotion_curve',
    'script_turn_point',
    'script_evidence',
    'lib_tag',
    'lib_topic',
    'lib_hook',
    'lib_copywriting',
    'lib_quote',
    'lib_method',
    'lib_combo',
    'lib_combo_slot',
    'ref_element_source',
]

_TS = dict(server_default=sa.func.now())
_ARR = dict(server_default=sa.text("'[]'"))
_ZERO = dict(server_default=sa.text('0'))
_SHAPE = "shape IN ('单峰','双峰','递进上升','波浪','骤升缓降','前高后低','平缓')"
_LIB_STATUS = "status IN ('active','archived')"

# ---------------------------------------------------------------- 受控词表种子
# 全部取值来自设计文档 5.2 / 5.3 / 7.2 的受控枚举，code 采用 "{dimension}.{label}"
_TAG_SEED: dict[str, list[str]] = {
    'topic_type': ['痛点型', '好奇型', '利益型', '身份型', '反常识型'],
    'hook_type': ['结果前置', '抛冲突', '提问', '悬念', '金句', '否定式', '视觉中断', '利益承诺'],
    'copy_type': ['口播', '字幕', '标题', 'CTA', '封面文案'],
    'rhetoric': ['设问', '排比', '对比', '夸张', '比喻', '反问', '递进', '白描', '数字锚定'],
    'method': ['叙事', '结构', '修辞', '视听', '节奏', '互动', '运营'],
    'seg_type': ['钩子', '铺垫', '冲突', '转折', '高潮', '干货', 'CTA'],
    'turn_type': ['峰', '谷', '反转', '悬念'],
    'shape': ['单峰', '双峰', '递进上升', '波浪', '骤升缓降', '前高后低', '平缓'],
    'slot_role': ['固定', '可替换'],
    'intent': ['涨粉', '带货', '种草', '引流', '科普', '情绪共鸣'],
    'function_label': [
        '钩子', '铺垫', '冲突', '转折', '高潮', '干货', 'CTA',
        '开头', '结尾', '引入', '介绍', '总结', '升华',
        '情绪', '悬念', '反差', '冲突点', '记忆点', '价值点', '共鸣', '互动',
    ],
}


def upgrade() -> None:
    """Upgrade schema: 新建三层分库 20 张表 + 预置 lib_tag 受控词表。"""
    # ---------------- 第一层 · 保真原料层 raw_* ----------------
    op.create_table(
        'raw_media_manifest',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('video_id', sa.Uuid(), nullable=False),
        sa.Column('platform', sa.String(length=32), nullable=False),
        sa.Column('work_dir', sa.Text(), nullable=False),
        sa.Column('video_path', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('subtitle_source', sa.String(length=32), nullable=False),
        sa.Column('asr_model', sa.String(length=64), nullable=True),
        sa.Column('asr_pipeline_version', sa.String(length=16), nullable=True),
        sa.Column('warnings', sa.JSON(), nullable=False, **_ARR),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.ForeignKeyConstraint(['video_id'], ['videos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('video_id', name='uq_raw_media_manifest_video'),
    )
    op.create_table(
        'raw_transcript_sentence',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('video_id', sa.Uuid(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('start_ms', sa.Integer(), nullable=False),
        sa.Column('end_ms', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('proofread', sa.Integer(), nullable=False, **_ZERO),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint("length(trim(text)) > 0", name='ck_raw_sentence_text'),
        sa.CheckConstraint("source IN ('asr','manual','ocr')", name='ck_raw_sentence_source'),
        sa.ForeignKeyConstraint(['video_id'], ['videos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('video_id', 'seq', name='uq_raw_sentence_video_seq'),
    )
    op.create_index('ix_raw_sentence_video_time', 'raw_transcript_sentence', ['video_id', 'start_ms'])
    op.create_table(
        'raw_shot',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('video_id', sa.Uuid(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('start_ms', sa.Integer(), nullable=False),
        sa.Column('end_ms', sa.Integer(), nullable=False),
        sa.Column('desc', sa.Text(), nullable=False),
        sa.Column('path', sa.Text(), nullable=True),
        sa.Column('text_overlay', sa.Text(), nullable=True),
        sa.Column('style', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint("kind IN ('anchor','key','frame')", name='ck_raw_shot_kind'),
        sa.ForeignKeyConstraint(['video_id'], ['videos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('video_id', 'seq', name='uq_raw_shot_video_seq'),
    )
    op.create_table(
        'raw_audio_profile',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('video_id', sa.Uuid(), nullable=False),
        sa.Column('bpm', sa.Integer(), nullable=True),
        sa.Column('mean_volume_db', sa.Float(), nullable=True),
        sa.Column('has_bgm', sa.Integer(), nullable=False, **_ZERO),
        sa.Column('bgm_note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.ForeignKeyConstraint(['video_id'], ['videos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('video_id', name='uq_raw_audio_profile_video'),
    )
    op.create_table(
        'raw_audio_energy_sample',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('video_id', sa.Uuid(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('t_ms', sa.Integer(), nullable=False),
        sa.Column('energy', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.ForeignKeyConstraint(['video_id'], ['videos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('video_id', 'seq', name='uq_raw_energy_video_seq'),
    )

    # ---------------- 第二层 · 本片脚本层 script_* ----------------
    op.create_table(
        'script_script',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('video_id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=True),
        sa.Column('platform', sa.String(length=32), nullable=True),
        sa.Column('category', sa.String(length=64), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('core_idea', sa.Text(), nullable=False),
        sa.Column('content_trend', sa.Text(), nullable=False),
        sa.Column('target_audience', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('sentence_count', sa.Integer(), nullable=False, **_ZERO),
        sa.Column('status', sa.String(length=16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint('length(trim(core_idea)) > 0', name='ck_script_script_core_idea'),
        sa.CheckConstraint("status IN ('draft','active','archived')", name='ck_script_script_status'),
        sa.ForeignKeyConstraint(['video_id'], ['videos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('video_id', name='uq_script_script_video'),
    )
    op.create_table(
        'script_segment',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('script_id', sa.Uuid(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('seg_type', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=128), nullable=True),
        sa.Column('start_ms', sa.Integer(), nullable=False),
        sa.Column('end_ms', sa.Integer(), nullable=False),
        sa.Column('start_sentence_seq', sa.Integer(), nullable=False),
        sa.Column('end_sentence_seq', sa.Integer(), nullable=False),
        sa.Column('purpose', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('hook_point', sa.Integer(), nullable=False, **_ZERO),
        sa.Column('payoff_point', sa.Integer(), nullable=False, **_ZERO),
        sa.Column('emotion_peak', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint("seg_type IN ('钩子','铺垫','冲突','转折','高潮','干货','CTA')", name='ck_script_segment_seg_type'),
        sa.ForeignKeyConstraint(['script_id'], ['script_script.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('script_id', 'seq', name='uq_script_segment_script_seq'),
    )
    op.create_index('ix_script_segment_script', 'script_segment', ['script_id', 'seq'])
    op.create_table(
        'script_sentence',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('script_id', sa.Uuid(), nullable=False),
        sa.Column('segment_id', sa.Uuid(), nullable=True),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('raw_sentence_id', sa.Uuid(), nullable=False),
        sa.Column('source_video_id', sa.Uuid(), nullable=False),
        sa.Column('quote', sa.Text(), nullable=False),
        sa.Column('start_ms', sa.Integer(), nullable=False),
        sa.Column('end_ms', sa.Integer(), nullable=False),
        sa.Column('sentence_function', sa.String(length=32), nullable=False),
        sa.Column('function_reason', sa.Text(), nullable=False),
        sa.Column('method_refs', sa.JSON(), nullable=False, **_ARR),
        sa.Column('emotion_intensity', sa.Float(), nullable=False),
        sa.Column('is_hook', sa.Integer(), nullable=False, **_ZERO),
        sa.Column('is_turn', sa.Integer(), nullable=False, **_ZERO),
        sa.Column('is_peak', sa.Integer(), nullable=False, **_ZERO),
        sa.Column('variants', sa.JSON(), nullable=False, **_ARR),
        sa.Column('imagination', sa.Text(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint('length(trim(quote)) >= 8', name='ck_script_sentence_quote'),
        sa.CheckConstraint(
            "sentence_function IN ('钩子','铺垫','冲突','转折','高潮','干货','CTA','过渡','收尾')",
            name='ck_script_sentence_function',
        ),
        sa.CheckConstraint('emotion_intensity >= 0 AND emotion_intensity <= 10', name='ck_script_sentence_intensity'),
        sa.CheckConstraint('end_ms = 0 OR end_ms >= start_ms', name='ck_script_sentence_time'),
        sa.ForeignKeyConstraint(['raw_sentence_id'], ['raw_transcript_sentence.id'], ),
        sa.ForeignKeyConstraint(['script_id'], ['script_script.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['segment_id'], ['script_segment.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('script_id', 'seq', name='uq_script_sentence_script_seq'),
    )
    op.create_index('ix_script_sentence_script', 'script_sentence', ['script_id', 'seq'])
    op.create_index('ix_script_sentence_video', 'script_sentence', ['source_video_id'])
    op.create_index('ix_script_sentence_seg', 'script_sentence', ['segment_id'])
    op.create_table(
        'script_emotion_curve',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('script_id', sa.Uuid(), nullable=False),
        sa.Column('sample_interval_ms', sa.Integer(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('sample_count', sa.Integer(), nullable=False),
        sa.Column('intensity_series', sa.JSON(), nullable=False),
        sa.Column('series_min', sa.Float(), nullable=True),
        sa.Column('series_max', sa.Float(), nullable=True),
        sa.Column('shape', sa.String(length=32), nullable=False),
        sa.Column('peak_position_ratio', sa.Float(), nullable=False),
        sa.Column('peak_count', sa.Integer(), nullable=False, **_ZERO),
        sa.Column('valley_count', sa.Integer(), nullable=False, **_ZERO),
        sa.Column('baseline_intensity', sa.Float(), nullable=True),
        sa.Column('method', sa.String(length=16), nullable=False, server_default=sa.text("'model'")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint(_SHAPE, name='ck_script_curve_shape'),
        sa.CheckConstraint("method IN ('model','audio_energy','hybrid')", name='ck_script_curve_method'),
        sa.ForeignKeyConstraint(['script_id'], ['script_script.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('script_id', name='uq_script_curve_script'),
    )
    op.create_table(
        'script_turn_point',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('script_id', sa.Uuid(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('turn_type', sa.String(length=32), nullable=False),
        sa.Column('t_ms', sa.Integer(), nullable=False),
        sa.Column('intensity', sa.Float(), nullable=False),
        sa.Column('sentence_id', sa.Uuid(), nullable=True),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint("turn_type IN ('峰','谷','反转','悬念')", name='ck_script_turn_type'),
        sa.ForeignKeyConstraint(['script_id'], ['script_script.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sentence_id'], ['script_sentence.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('script_id', 'seq', name='uq_script_turn_script_seq'),
    )
    op.create_table(
        'script_evidence',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('script_id', sa.Uuid(), nullable=False),
        sa.Column('sentence_id', sa.Uuid(), nullable=False),
        sa.Column('evidence_type', sa.String(length=16), nullable=False),
        sa.Column('raw_sentence_id', sa.Uuid(), nullable=True),
        sa.Column('raw_shot_id', sa.Uuid(), nullable=True),
        sa.Column('source_video_id', sa.Uuid(), nullable=False),
        sa.Column('quote', sa.Text(), nullable=False),
        sa.Column('start_ms', sa.Integer(), nullable=False),
        sa.Column('end_ms', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint(
            "evidence_type IN ('transcript','ocr','frame','audio')",
            name='ck_script_evidence_type',
        ),
        sa.ForeignKeyConstraint(['script_id'], ['script_script.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sentence_id'], ['script_sentence.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_script_evidence_sent', 'script_evidence', ['sentence_id'])

    # ---------------- 第三层 · 可复用积木库层 lib_* ----------------
    op.create_table(
        'lib_tag',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=64), nullable=False),
        sa.Column('dimension', sa.String(length=32), nullable=False),
        sa.Column('is_controlled', sa.Integer(), nullable=False, **_ZERO),
        sa.Column('is_emergent', sa.Integer(), nullable=False, **_ZERO),
        sa.Column('parent_code', sa.String(length=64), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_lib_tag_code'),
    )
    op.create_index('ix_lib_tag_dimension', 'lib_tag', ['dimension', 'is_controlled'])
    op.create_table(
        'lib_topic',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('topic_type', sa.String(length=32), nullable=False),
        sa.Column('audience', sa.Text(), nullable=False),
        sa.Column('pain_point', sa.Text(), nullable=False),
        sa.Column('angle', sa.Text(), nullable=False),
        sa.Column('value_type', sa.String(length=16), nullable=False),
        sa.Column('applicable_category', sa.String(length=64), nullable=True),
        sa.Column('keywords', sa.JSON(), nullable=False, **_ARR),
        sa.Column('mechanism', sa.Text(), nullable=False),
        sa.Column('variants', sa.JSON(), nullable=False),
        sa.Column('imagination', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default=sa.text("'active'")),
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint("value_type IN ('实用','情绪','娱乐')", name='ck_lib_topic_value_type'),
        sa.CheckConstraint(_LIB_STATUS, name='ck_lib_topic_status'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_lib_topic_code'),
    )
    op.create_index('ix_lib_topic_status_type', 'lib_topic', ['status', 'topic_type'])
    op.create_table(
        'lib_hook',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('hook_type', sa.String(length=32), nullable=False),
        sa.Column('position', sa.String(length=16), nullable=False),
        sa.Column('sentence_pattern', sa.Text(), nullable=False),
        sa.Column('variables', sa.JSON(), nullable=False, **_ARR),
        sa.Column('expected_effect', sa.Text(), nullable=False),
        sa.Column('mechanism', sa.Text(), nullable=False),
        sa.Column('variants', sa.JSON(), nullable=False),
        sa.Column('imagination', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default=sa.text("'active'")),
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint("position IN ('前3秒','片中','结尾')", name='ck_lib_hook_position'),
        sa.CheckConstraint(_LIB_STATUS, name='ck_lib_hook_status'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_lib_hook_code'),
    )
    op.create_index('ix_lib_hook_status_type', 'lib_hook', ['status', 'hook_type'])
    op.create_table(
        'lib_copywriting',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('copy_type', sa.String(length=32), nullable=False),
        sa.Column('sentence_pattern', sa.Text(), nullable=False),
        sa.Column('rhetoric', sa.String(length=32), nullable=False),
        sa.Column('example_text', sa.Text(), nullable=False),
        sa.Column('mechanism', sa.Text(), nullable=False),
        sa.Column('variants', sa.JSON(), nullable=False),
        sa.Column('imagination', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default=sa.text("'active'")),
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint(_LIB_STATUS, name='ck_lib_copywriting_status'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_lib_copywriting_code'),
    )
    op.create_index('ix_lib_copywriting_status_type', 'lib_copywriting', ['status', 'copy_type'])
    op.create_table(
        'lib_quote',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('structure', sa.Text(), nullable=False),
        sa.Column('rewrite_template', sa.Text(), nullable=False),
        sa.Column('applicable_scene', sa.Text(), nullable=False),
        sa.Column('mechanism', sa.Text(), nullable=False),
        sa.Column('variants', sa.JSON(), nullable=False),
        sa.Column('imagination', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default=sa.text("'active'")),
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint(_LIB_STATUS, name='ck_lib_quote_status'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_lib_quote_code'),
    )
    op.create_index('ix_lib_quote_status', 'lib_quote', ['status'])
    op.create_table(
        'lib_method',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('controlled_tag', sa.String(length=32), nullable=False),
        sa.Column('is_emergent', sa.Integer(), nullable=False, **_ZERO),
        sa.Column('emergent_parent_code', sa.String(length=64), nullable=True),
        sa.Column('mechanism', sa.Text(), nullable=False),
        sa.Column('abstraction_level', sa.String(length=16), nullable=False),
        sa.Column('usage_steps', sa.Text(), nullable=False),
        sa.Column('counter_example', sa.Text(), nullable=False),
        sa.Column('variants', sa.JSON(), nullable=False),
        sa.Column('imagination', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default=sa.text("'active'")),
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint(
            "abstraction_level IN ('句法级','段落级','全片级')",
            name='ck_lib_method_abstraction',
        ),
        sa.CheckConstraint(_LIB_STATUS, name='ck_lib_method_status'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_lib_method_code'),
    )
    op.create_index('ix_lib_method_status_tag', 'lib_method', ['status', 'controlled_tag'])
    op.create_table(
        'lib_combo',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('intent', sa.String(length=32), nullable=False),
        sa.Column('core_idea_alignment', sa.Text(), nullable=False),
        sa.Column('content_trend', sa.Text(), nullable=False),
        sa.Column('sequence_desc', sa.Text(), nullable=False),
        sa.Column('emotion_shape', sa.String(length=32), nullable=False),
        sa.Column('emotion_curve', sa.JSON(), nullable=False),
        sa.Column('duration_ratio', sa.JSON(), nullable=False),
        sa.Column('applicable_category', sa.String(length=64), nullable=True),
        sa.Column('slot_count', sa.Integer(), nullable=False),
        sa.Column('fixed_slot_count', sa.Integer(), nullable=False),
        sa.Column('swap_slot_count', sa.Integer(), nullable=False),
        sa.Column('mechanism', sa.Text(), nullable=False),
        sa.Column('variants', sa.JSON(), nullable=False),
        sa.Column('imagination', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default=sa.text("'active'")),
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint(
            "emotion_shape IN ('单峰','双峰','递进上升','波浪','骤升缓降','前高后低','平缓')",
            name='ck_lib_combo_shape',
        ),
        sa.CheckConstraint(_LIB_STATUS, name='ck_lib_combo_status'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_lib_combo_code'),
    )
    op.create_index('ix_lib_combo_status_intent', 'lib_combo', ['status', 'intent'])
    op.create_table(
        'lib_combo_slot',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('combo_id', sa.Uuid(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('slot_role', sa.String(length=16), nullable=False),
        sa.Column('method_id', sa.Uuid(), nullable=True),
        sa.Column('method_code', sa.String(length=64), nullable=False),
        sa.Column('expected_function', sa.Text(), nullable=False),
        sa.Column('position_ratio_start', sa.Float(), nullable=False),
        sa.Column('position_ratio_end', sa.Float(), nullable=False),
        sa.Column('duration_ratio', sa.Float(), nullable=False),
        sa.Column('swap_alternatives', sa.JSON(), nullable=False, **_ARR),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint("slot_role IN ('固定','可替换')", name='ck_lib_combo_slot_role'),
        sa.ForeignKeyConstraint(['combo_id'], ['lib_combo.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('combo_id', 'seq', name='uq_lib_combo_slot_combo_seq'),
    )

    # ---------------- 溯源表 ref_* ----------------
    op.create_table(
        'ref_element_source',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('element_table', sa.String(length=32), nullable=False),
        sa.Column('element_id', sa.Uuid(), nullable=False),
        sa.Column('source_script_id', sa.Uuid(), nullable=False),
        sa.Column('source_video_id', sa.Uuid(), nullable=False),
        sa.Column('source_sentence_id', sa.Uuid(), nullable=True),
        sa.Column('source_segment_id', sa.Uuid(), nullable=True),
        sa.Column('quote', sa.Text(), nullable=False),
        sa.Column('start_ms', sa.Integer(), nullable=False),
        sa.Column('end_ms', sa.Integer(), nullable=False),
        sa.Column('source_platform', sa.String(length=32), nullable=True),
        sa.Column('extracted_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint(
            "element_table IN ('lib_topic','lib_hook','lib_copywriting','lib_quote','lib_method','lib_combo')",
            name='ck_ref_element_table',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ref_element', 'ref_element_source', ['element_table', 'element_id'])
    op.create_index('ix_ref_video', 'ref_element_source', ['source_video_id'])

    # ---------------- lib_tag 受控词表种子 ----------------
    lib_tag = sa.table(
        'lib_tag',
        sa.column('id', sa.CHAR(32)),
        sa.column('code', sa.String(64)),
        sa.column('label', sa.String(64)),
        sa.column('dimension', sa.String(32)),
        sa.column('is_controlled', sa.Integer()),
        sa.column('is_emergent', sa.Integer()),
    )
    rows = []
    for dimension, labels in _TAG_SEED.items():
        for label in labels:
            rows.append(
                {
                    'id': _uuid.uuid4().hex,
                    'code': f'{dimension}.{label}',
                    'label': label,
                    'dimension': dimension,
                    'is_controlled': 1,
                    'is_emergent': 0,
                }
            )
    op.bulk_insert(lib_tag, rows)


def downgrade() -> None:
    """Downgrade schema: 仅删除本次新增的 20 张表，不触碰既有表。"""
    op.drop_index('ix_ref_video', table_name='ref_element_source')
    op.drop_index('ix_ref_element', table_name='ref_element_source')
    op.drop_table('ref_element_source')

    op.drop_table('lib_combo_slot')
    op.drop_index('ix_lib_combo_status_intent', table_name='lib_combo')
    op.drop_table('lib_combo')
    op.drop_index('ix_lib_method_status_tag', table_name='lib_method')
    op.drop_table('lib_method')
    op.drop_index('ix_lib_quote_status', table_name='lib_quote')
    op.drop_table('lib_quote')
    op.drop_index('ix_lib_copywriting_status_type', table_name='lib_copywriting')
    op.drop_table('lib_copywriting')
    op.drop_index('ix_lib_hook_status_type', table_name='lib_hook')
    op.drop_table('lib_hook')
    op.drop_index('ix_lib_topic_status_type', table_name='lib_topic')
    op.drop_table('lib_topic')
    op.drop_index('ix_lib_tag_dimension', table_name='lib_tag')
    op.drop_table('lib_tag')

    op.drop_index('ix_script_evidence_sent', table_name='script_evidence')
    op.drop_table('script_evidence')
    op.drop_table('script_turn_point')
    op.drop_table('script_emotion_curve')
    op.drop_index('ix_script_sentence_seg', table_name='script_sentence')
    op.drop_index('ix_script_sentence_video', table_name='script_sentence')
    op.drop_index('ix_script_sentence_script', table_name='script_sentence')
    op.drop_table('script_sentence')
    op.drop_index('ix_script_segment_script', table_name='script_segment')
    op.drop_table('script_segment')
    op.drop_table('script_script')

    op.drop_table('raw_audio_energy_sample')
    op.drop_table('raw_audio_profile')
    op.drop_table('raw_shot')
    op.drop_index('ix_raw_sentence_video_time', table_name='raw_transcript_sentence')
    op.drop_table('raw_transcript_sentence')
    op.drop_table('raw_media_manifest')
