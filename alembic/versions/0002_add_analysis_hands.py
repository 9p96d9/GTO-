"""add analysis_hands table

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-30

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analysis_hands",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "analysis_id",
            sa.Integer,
            sa.ForeignKey("analyses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("hand_number", sa.Integer, nullable=False),
        sa.Column("line", sa.String(10), nullable=False),
        sa.Column("category_label", sa.String(100), nullable=False),
        sa.Column("position", sa.String(10), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_analysis_hands_analysis", "analysis_hands", ["analysis_id"])
    op.create_index(
        "ix_analysis_hands_3d",
        "analysis_hands",
        ["line", "position", "captured_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_hands_3d", table_name="analysis_hands")
    op.drop_index("ix_analysis_hands_analysis", table_name="analysis_hands")
    op.drop_table("analysis_hands")
