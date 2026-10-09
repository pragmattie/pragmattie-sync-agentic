"""epics

Epics, one row a GitHub milestone, with the milestone's due date as the epic's target.

Revision ID: 0006_epics
Revises: 0005_approvals
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_epics"
down_revision: str | None = "0005_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sdlc_epics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), server_default="synthetic", nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=10), server_default="open", nullable=False),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "external_id", name="uq_sdlc_epics_source_external_id"),
    )
    op.create_index("ix_sdlc_epics_source", "sdlc_epics", ["source"], unique=False)


def downgrade() -> None:
    op.drop_table("sdlc_epics")
