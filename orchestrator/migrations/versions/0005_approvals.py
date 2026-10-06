"""approvals

Requests to the simulated second approver, and its answers, one row a pull request and approver.
Every row is simulated: it is never a real person's approval.

Revision ID: 0005_approvals
Revises: 0004_gate_status
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_approvals"
down_revision: str | None = "0004_gate_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sdlc_approvals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), server_default="simulated", nullable=False),
        sa.Column("pull_request_id", sa.Integer(), nullable=False),
        sa.Column("approver_id", sa.String(length=60), nullable=False),
        sa.Column("tier", sa.String(length=2), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="pending", nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["pull_request_id"], ["sdlc_pull_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pull_request_id", "approver_id", name="uq_sdlc_approvals_pull_request_approver"
        ),
    )
    op.create_index(
        "ix_sdlc_approvals_pull_request_id", "sdlc_approvals", ["pull_request_id"], unique=False
    )
    op.create_index("ix_sdlc_approvals_status", "sdlc_approvals", ["status"], unique=False)


def downgrade() -> None:
    op.drop_table("sdlc_approvals")
