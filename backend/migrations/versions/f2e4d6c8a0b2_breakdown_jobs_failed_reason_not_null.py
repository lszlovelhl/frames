"""breakdown_jobs.failed_reason 禁止 NULL

体检清单 #2：让拆解失败可复盘。
- 存量回填：failed 且 failed_reason 为 NULL/空 的记录按聚类（three_L6）回填兜底原因。
- 列约束：NOT NULL + server_default，未来缺省一律落 unknown_error。

Revision ID: f2e4d6c8a0b2
Revises: f1a2b3c4d5e6
Create Date: 2026-10-15

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "f2e4d6c8a0b2"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 存量回填：failed 且 failed_reason 为 NULL/空 → 聚类兜底值（13 条，均为 three_L6）
    op.execute(
        "UPDATE breakdown_jobs SET failed_reason = "
        "'三层拆解失败（历史存量：L6 阶段，原因为空）' "
        "WHERE status = 'failed' AND (failed_reason IS NULL OR trim(failed_reason) = '')"
    )
    # 2) 列默认值兜底：未来 INSERT 缺 failed_reason 时落 unknown_error。
    #    列保持 nullable（done 状态无失败原因属正常语义，不做列级 NOT NULL）
    with op.batch_alter_table("breakdown_jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "failed_reason",
            existing_type=sa.Text(),
            nullable=True,
            server_default="unknown_error",
        )


def downgrade() -> None:
    with op.batch_alter_table("breakdown_jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "failed_reason",
            existing_type=sa.Text(),
            nullable=True,
            server_default=None,
        )
