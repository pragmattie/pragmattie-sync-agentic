"""agent decisions

The append-only audit trail of every agent decision: what the agent saw, what it decided, what it
did and what it cost.

Revision ID: 0003_agent_decisions
Revises: 0002_engineering_tables
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_agent_decisions"
down_revision: str | None = "0002_engineering_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sdlc_agent_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("agent", sa.String(length=40), nullable=False),
        sa.Column("agent_version", sa.String(length=20), nullable=False),
        sa.Column("model_id", sa.String(length=80), nullable=True),
        sa.Column("prompt_version", sa.String(length=20), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("subject_type", sa.String(length=10), nullable=False),
        sa.Column("subject_source", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("inputs_digest", sa.JSON(), nullable=True),
        sa.Column("signals", sa.JSON(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("action_taken", sa.JSON(), nullable=True),
        sa.Column("human_override", sa.JSON(), nullable=True),
        sa.Column("raw_score", sa.Integer(), nullable=True),
        sa.Column("adjustment", sa.Integer(), nullable=True),
        sa.Column("final_score", sa.Integer(), nullable=True),
        sa.Column("tier", sa.String(length=2), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="ok", nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["supersedes_id"], ["sdlc_agent_decisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent",
            "subject_type",
            "subject_source",
            "subject_id",
            "head_sha",
            "attempt",
            name="uq_sdlc_agent_decisions_attempt",
        ),
    )
    op.create_index("ix_sdlc_agent_decisions_created_at", "sdlc_agent_decisions", ["created_at"])
    op.create_index("ix_sdlc_agent_decisions_agent", "sdlc_agent_decisions", ["agent"])
    op.create_index("ix_sdlc_agent_decisions_subject_id", "sdlc_agent_decisions", ["subject_id"])
    op.create_index("ix_sdlc_agent_decisions_status", "sdlc_agent_decisions", ["status"])


def downgrade() -> None:
    op.drop_table("sdlc_agent_decisions")
