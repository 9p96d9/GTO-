"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-28

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("firebase_uid", sa.String(128), nullable=False, unique=True),
        sa.Column("email", sa.String(256)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "hands",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hand_id", sa.String(256), nullable=False, unique=True),
        sa.Column("hand_json", sa.JSON, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_hands_user_saved", "hands", ["user_id", "saved_at"])

    op.create_table(
        "analyses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(256), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("hand_count", sa.Integer),
        sa.Column("blue_count", sa.Integer),
        sa.Column("red_count", sa.Integer),
        sa.Column("pf_count", sa.Integer),
        sa.Column("categories", sa.JSON),
        sa.Column("classified_snapshot", sa.Text, nullable=True),
        sa.Column("snapshot_encoding", sa.String(32), nullable=True),
        sa.Column("active_cart", sa.JSON, nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_analyses_user_created", "analyses", ["user_id", "created_at"])

    op.create_table(
        "ai_results",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("analysis_id", sa.Integer, sa.ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hand_number", sa.Integer, nullable=False),
        sa.Column("ai_text", sa.Text),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ai_results_analysis", "ai_results", ["analysis_id"])

    op.create_table(
        "carts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cart_id", sa.String(64), nullable=False, unique=True),
        sa.Column("job_id", sa.String(256), nullable=False),
        sa.Column("name", sa.String(256)),
        sa.Column("hand_numbers", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_carts_user_created", "carts", ["user_id", "created_at"])

    op.create_table(
        "user_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("encrypted_api_key", sa.Text, nullable=True),
        sa.Column("needs_api_auto_cart", sa.Boolean, default=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("user_settings")
    op.drop_table("carts")
    op.drop_table("ai_results")
    op.drop_table("analyses")
    op.drop_table("hands")
    op.drop_table("users")
