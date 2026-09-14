"""add breakdown_jobs (queue/progress) + lib_*.review_status (element QC 4-state)

Revision ID: f1a2b3c4d5e6
Revises: e2f3a4b5c6d7
Create Date: 2026-09-13 17:20:00.000000

第 7 章清空已将 A 级旧表 analyses / analysis_layers / segments / analysis_notes /
elements / element_versions / annotations / category_templates 全部 DROP。
本次迁移完成应用层迁移所需的最后两块结构：

1. ``breakdown_jobs``：拆解队列 + 进度 + 证据的唯一承载表（替代旧 analyses）；
2. ``lib_topic/lib_hook/lib_copywriting/lib_quote/lib_method/lib_combo`` 增加
   ``review_status`` 列（draft/accepted/adjusted/rejected），承载元素质控四态，
   使元素库接口可改读三层积木库而不依赖已删的 elements 表。

边界：只新增表与列，不改动、不迁移、不清空任何既有数据；既有 lib_* 行的
review_status 统一初始化为 'draft'（与旧元素链路"新提炼元素待质控"语义一致）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = dict(server_default=sa.func.now())
_LIB_REVIEW_TABLES = [
    'lib_topic',
    'lib_hook',
    'lib_copywriting',
    'lib_quote',
    'lib_method',
    'lib_combo',
]


def upgrade() -> None:
    """Upgrade schema：新增拆解任务表 + 积木库质控列。"""
    op.create_table(
        'breakdown_jobs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('video_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False,
                  server_default=sa.text("'queued'")),
        sa.Column('stage', sa.String(length=32), nullable=False,
                  server_default=sa.text("''")),
        sa.Column('message', sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column('pct', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('model', sa.String(length=64), nullable=True),
        sa.Column('script_id', sa.Uuid(), nullable=True),
        sa.Column('evidence', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('failed_reason', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint(
            "status IN ('queued','running','done','partial','failed')",
            name='ck_breakdown_job_status',
        ),
        sa.ForeignKeyConstraint(['video_id'], ['videos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_breakdown_job_video', 'breakdown_jobs', ['video_id', 'created_at'])
    op.create_index('ix_breakdown_job_status', 'breakdown_jobs', ['status'])

    for table in _LIB_REVIEW_TABLES:
        op.add_column(
            table,
            sa.Column(
                'review_status',
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'draft'"),
            ),
        )

    # AI 组合/变异产物的落库载体（旧 elements 表已 DROP，需承接 source_type=combo 元素）
    op.create_table(
        'lib_mix_draft',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False,
                  server_default=sa.text("'综合'")),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('formula', sa.Text(), nullable=True),
        sa.Column('source_type', sa.String(length=32), nullable=False,
                  server_default=sa.text("'combo'")),
        sa.Column('role_view', sa.String(length=32), nullable=False,
                  server_default=sa.text("'编导'")),
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('review_status', sa.String(length=16), nullable=False,
                  server_default=sa.text("'draft'")),
        sa.Column('tags', sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('ref_element_ids', sa.JSON(), nullable=False,
                  server_default=sa.text("'[]'")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, **_TS),
        sa.CheckConstraint(
            "review_status IN ('draft','accepted','adjusted','rejected')",
            name='ck_lib_mix_draft_status',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_lib_mix_draft_status', 'lib_mix_draft', ['review_status'])


def downgrade() -> None:
    """Downgrade schema：回退新增表与列（不动既有数据）。"""
    op.drop_index('ix_lib_mix_draft_status', table_name='lib_mix_draft')
    op.drop_table('lib_mix_draft')

    for table in _LIB_REVIEW_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column('review_status')

    op.drop_index('ix_breakdown_job_status', table_name='breakdown_jobs')
    op.drop_index('ix_breakdown_job_video', table_name='breakdown_jobs')
    op.drop_table('breakdown_jobs')
