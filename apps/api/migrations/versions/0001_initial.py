"""initial

Empty starting point for the CRM history. Real tables arrive with later revisions.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-29
"""

from collections.abc import Sequence

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
