"""drop api_keys 死表 (A5)

api_keys 表从未被业务使用：实际密钥存于 ai_providers.api_key；
表内无数据且无任何外键引用，删除模型/路由后随迁移清除表结构。

Revision ID: c3d5e7f9a1b3
Revises: b2f4e6a8d0c2
Create Date: 2026-09-09 17:30:00
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d5e7f9a1b3"
down_revision = "b2f4e6a8d0c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table("api_keys")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "api_keys",
        op.Column("id", sa.UUID(), nullable=False),
        op.Column("user_id", sa.UUID(), nullable=True),
        op.Column("provider", sa.String(length=32), nullable=False),
        op.Column("key_encrypted", sa.Text(), nullable=False),
        op.Column("base_url", sa.String(length=255), nullable=True),
        op.Column("model_default", sa.String(length=64), nullable=True),
        op.Column("is_active", sa.Boolean(), nullable=False),
        op.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        op.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
