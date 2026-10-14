"""forecast gate days

How many working days for gates and specs a saved forecast includes; old rows read 0.

Revision ID: 0009_forecast_gate_days
Revises: 0008_forecast_ahead
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_forecast_gate_days"
down_revision: str | None = "0008_forecast_ahead"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sdlc_forecasts",
        sa.Column("gate_days", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sdlc_forecasts", "gate_days")
