"""drop needs_api_auto_cart column

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-08

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("user_settings", "needs_api_auto_cart")


def downgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column("needs_api_auto_cart", sa.Boolean, nullable=True, server_default="false"),
    )
