"""add credit accounts and transactions (点数计费账户与流水)

Revision ID: b2f4e6a8d0c2
Revises: a1b2c3d4e5f6
Create Date: 2026-09-08 10:30:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b2f4e6a8d0c2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "credit_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("balance_points", sa.Integer(), nullable=False),
        sa.Column("total_recharged_points", sa.Integer(), nullable=False),
        sa.Column("total_consumed_points", sa.Integer(), nullable=False),
        sa.Column("free_claimed", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "credit_transactions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=True),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("amount_cny", sa.Float(), nullable=True),
        sa.Column("ref_type", sa.String(length=32), nullable=True),
        sa.Column("ref_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["account_id"], ["credit_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_credit_accounts_user_id"), "credit_accounts", ["user_id"], unique=False)
    op.create_index(op.f("ix_credit_transactions_account_id"), "credit_transactions", ["account_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_credit_transactions_account_id"), table_name="credit_transactions")
    op.drop_index(op.f("ix_credit_accounts_user_id"), table_name="credit_accounts")
    op.drop_table("credit_transactions")
    op.drop_table("credit_accounts")
