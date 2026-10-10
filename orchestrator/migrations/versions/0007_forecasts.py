"""forecasts

Saved sprint and epic forecasts, append-only, so a page can show how a date moved.

Revision ID: 0007_forecasts
Revises: 0006_epics
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_forecasts"
down_revision: str | None = "0006_epics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sdlc_forecasts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("subject", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("inputs_hash", sa.String(length=64), nullable=False),
        sa.Column("remaining_items", sa.Integer(), nullable=False),
        sa.Column("remaining_real", sa.Integer(), server_default="0", nullable=False),
        sa.Column("remaining_points", sa.Integer(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("p50", sa.Date(), nullable=True),
        sa.Column("p85", sa.Date(), nullable=True),
        sa.Column("on_time_probability", sa.Float(), nullable=True),
        sa.Column("throughput_mean", sa.Float(), nullable=False),
        sa.Column("history_days", sa.Integer(), nullable=False),
        sa.Column("runs", sa.Integer(), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("at_risk", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sdlc_forecasts_created_at", "sdlc_forecasts", ["created_at"])
    op.create_index("ix_sdlc_forecasts_subject", "sdlc_forecasts", ["subject"])


def downgrade() -> None:
    op.drop_table("sdlc_forecasts")
