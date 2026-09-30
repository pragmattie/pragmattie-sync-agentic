"""reps, accounts and contacts

Revision ID: 0002_reps_accounts_contacts
Revises: 0001_initial
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_reps_accounts_contacts"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("region", sa.String(length=50), nullable=False),
        sa.Column("quarterly_quota", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_reps_email"),
    )
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("industry", sa.String(length=80), nullable=False),
        sa.Column("employee_count", sa.Integer(), nullable=False),
        sa.Column("annual_revenue", sa.Numeric(14, 2), nullable=False),
        sa.Column("region", sa.String(length=50), nullable=False),
        sa.Column("website", sa.String(length=200), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["reps.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_accounts_name", "accounts", ["name"])
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("first_name", sa.String(length=80), nullable=False),
        sa.Column("last_name", sa.String(length=80), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_contacts_account_id", "contacts", ["account_id"])


def downgrade() -> None:
    op.drop_table("contacts")
    op.drop_table("accounts")
    op.drop_table("reps")
