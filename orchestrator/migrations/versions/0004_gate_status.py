"""gate status

The current risk-gate state of each pull request, one row a PR, so pages can show what each PR
is waiting for without asking GitHub.

Revision ID: 0004_gate_status
Revises: 0003_agent_decisions
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_gate_status"
down_revision: str | None = "0003_agent_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sdlc_gate_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pull_request_id", sa.Integer(), nullable=False),
        sa.Column("tier", sa.String(length=2), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("would_be", sa.String(length=10), nullable=False),
        sa.Column("missing", sa.JSON(), nullable=False),
        sa.Column("description", sa.String(length=140), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["pull_request_id"], ["sdlc_pull_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sdlc_gate_status_pull_request_id",
        "sdlc_gate_status",
        ["pull_request_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("sdlc_gate_status")
