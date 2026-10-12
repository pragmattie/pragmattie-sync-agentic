"""forecast ahead

How many open items in earlier milestones a saved forecast was made after; old rows read 0.

Revision ID: 0008_forecast_ahead
Revises: 0007_forecasts
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_forecast_ahead"
down_revision: str | None = "0007_forecasts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sdlc_forecasts",
        sa.Column("ahead_items", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sdlc_forecasts", "ahead_items")
