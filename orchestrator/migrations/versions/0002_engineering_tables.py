"""engineering tables

Engineers, sprints, issues, pull requests, CI runs, deployments and incidents, each row marked
with its source (synthetic or github).

Revision ID: 0002_engineering_tables
Revises: 0001_initial
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_engineering_tables"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _source() -> sa.Column:
    return sa.Column("source", sa.String(length=20), server_default="synthetic", nullable=False)


def _external_id() -> sa.Column:
    return sa.Column("external_id", sa.String(length=64), nullable=True)


def _flag(name: str) -> sa.Column:
    return sa.Column(name, sa.Boolean(), server_default=sa.false(), nullable=False)


def _count(name: str, default: int = 0) -> sa.Column:
    return sa.Column(name, sa.Integer(), server_default=str(default), nullable=False)


def _external_id_unique(table: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint("source", "external_id", name=f"uq_{table}_source_external_id")


def upgrade() -> None:
    op.create_table(
        "sdlc_engineers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("login", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=60), nullable=True),
        _source(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "login", name="uq_sdlc_engineers_source_login"),
    )
    op.create_table(
        "sdlc_sprints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("goal", sa.String(length=200), nullable=True),
        _source(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sdlc_sprints_start_date", "sdlc_sprints", ["start_date"])
    op.create_table(
        "sdlc_issues",
        sa.Column("id", sa.Integer(), nullable=False),
        _source(),
        _external_id(),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("module", sa.String(length=30), nullable=True),
        sa.Column("type", sa.String(length=20), server_default="feature", nullable=False),
        sa.Column("priority", sa.String(length=10), nullable=True),
        sa.Column("estimate_points", sa.Integer(), nullable=True),
        sa.Column("actual_days", sa.Float(), nullable=True),
        sa.Column("state", sa.String(length=10), server_default="open", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("sprint_id", sa.Integer(), nullable=True),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column("epic", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(["sprint_id"], ["sdlc_sprints.id"]),
        sa.ForeignKeyConstraint(["assignee_id"], ["sdlc_engineers.id"]),
        sa.PrimaryKeyConstraint("id"),
        _external_id_unique("sdlc_issues"),
    )
    op.create_index("ix_sdlc_issues_source", "sdlc_issues", ["source"])
    op.create_index("ix_sdlc_issues_module", "sdlc_issues", ["module"])
    op.create_index("ix_sdlc_issues_created_at", "sdlc_issues", ["created_at"])
    op.create_index("ix_sdlc_issues_epic", "sdlc_issues", ["epic"])
    op.create_table(
        "sdlc_pull_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        _source(),
        _external_id(),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("issue_id", sa.Integer(), nullable=False),
        sa.Column("module", sa.String(length=30), nullable=False),
        _count("files_changed"),
        _count("additions"),
        _count("deletions"),
        _flag("touches_migration"),
        _count("test_files_changed"),
        _flag("docs_only"),
        _count("modules_touched", 1),
        _flag("touches_governance"),
        _count("review_count"),
        sa.Column("first_review_hours", sa.Float(), nullable=True),
        _count("rework_commits"),
        sa.Column("state", sa.String(length=10), server_default="open", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("merged_at", sa.DateTime(), nullable=True),
        sa.Column("merge_commit_sha", sa.String(length=40), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        _flag("caused_incident"),
        _flag("reverted"),
        sa.ForeignKeyConstraint(["author_id"], ["sdlc_engineers.id"]),
        sa.ForeignKeyConstraint(["issue_id"], ["sdlc_issues.id"]),
        sa.PrimaryKeyConstraint("id"),
        _external_id_unique("sdlc_pull_requests"),
    )
    op.create_index("ix_sdlc_pull_requests_source", "sdlc_pull_requests", ["source"])
    op.create_index("ix_sdlc_pull_requests_module", "sdlc_pull_requests", ["module"])
    op.create_index("ix_sdlc_pull_requests_created_at", "sdlc_pull_requests", ["created_at"])
    op.create_table(
        "sdlc_ci_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        _source(),
        _external_id(),
        sa.Column("pull_request_id", sa.Integer(), nullable=True),
        sa.Column("suite", sa.String(length=40), nullable=False),
        sa.Column("conclusion", sa.String(length=20), nullable=False),
        _flag("flaky"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        _count("duration_seconds"),
        sa.ForeignKeyConstraint(["pull_request_id"], ["sdlc_pull_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
        _external_id_unique("sdlc_ci_runs"),
    )
    op.create_index("ix_sdlc_ci_runs_source", "sdlc_ci_runs", ["source"])
    op.create_index("ix_sdlc_ci_runs_suite", "sdlc_ci_runs", ["suite"])
    op.create_index("ix_sdlc_ci_runs_started_at", "sdlc_ci_runs", ["started_at"])
    op.create_table(
        "sdlc_deployments",
        sa.Column("id", sa.Integer(), nullable=False),
        _source(),
        _external_id(),
        sa.Column("sha", sa.String(length=40), nullable=True),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("deployed_at", sa.DateTime(), nullable=False),
        _count("pr_count"),
        sa.Column("status", sa.String(length=20), server_default="success", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        _external_id_unique("sdlc_deployments"),
    )
    op.create_index("ix_sdlc_deployments_source", "sdlc_deployments", ["source"])
    op.create_index("ix_sdlc_deployments_deployed_at", "sdlc_deployments", ["deployed_at"])
    op.create_table(
        "sdlc_incidents",
        sa.Column("id", sa.Integer(), nullable=False),
        _source(),
        _external_id(),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("module", sa.String(length=30), nullable=True),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("caused_by_pr_id", sa.Integer(), nullable=True),
        sa.Column("deployment_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["caused_by_pr_id"], ["sdlc_pull_requests.id"]),
        sa.ForeignKeyConstraint(["deployment_id"], ["sdlc_deployments.id"]),
        sa.PrimaryKeyConstraint("id"),
        _external_id_unique("sdlc_incidents"),
    )
    op.create_index("ix_sdlc_incidents_source", "sdlc_incidents", ["source"])
    op.create_index("ix_sdlc_incidents_opened_at", "sdlc_incidents", ["opened_at"])


def downgrade() -> None:
    op.drop_table("sdlc_incidents")
    op.drop_table("sdlc_deployments")
    op.drop_table("sdlc_ci_runs")
    op.drop_table("sdlc_pull_requests")
    op.drop_table("sdlc_issues")
    op.drop_table("sdlc_sprints")
    op.drop_table("sdlc_engineers")
