"""add ai_usage_logs (F域 AI 用量与计费可视化)

Revision ID: a1b2c3d4e5f6
Revises: 7df68a881fa7
Create Date: 2026-09-05 19:40:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = "7df68a881fa7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ai_usage_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scene", sa.String(length=32), nullable=False),
        sa.Column("ref_type", sa.String(length=32), nullable=True),
        sa.Column("ref_id", sa.UUID(), nullable=True),
        sa.Column("alias", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_cny", sa.Float(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_usage_logs_ref_id"), "ai_usage_logs", ["ref_id"], unique=False)
    op.create_index(op.f("ix_ai_usage_logs_scene"), "ai_usage_logs", ["scene"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_ai_usage_logs_scene"), table_name="ai_usage_logs")
    op.drop_index(op.f("ix_ai_usage_logs_ref_id"), table_name="ai_usage_logs")
    op.drop_table("ai_usage_logs")
