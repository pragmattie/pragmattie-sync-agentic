"""Engineering history the orchestrator learns from.

Every row says where it came from: ``synthetic`` rows are generated history, ``github`` rows are
collected from a real repository. Items that have a real-world id keep it in ``external_id``,
which is unique within its source.
"""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sdlc.db import Base

MODULES = (
    "leads",
    "accounts",
    "pipeline",
    "forecasting",
    "integrations",
    "billing_auth",
    "orchestrator",
    "platform",
)
SOURCES = ("synthetic", "github")


def _source(*, index: bool = False) -> Mapped[str]:
    return mapped_column(String(20), default="synthetic", server_default="synthetic", index=index)


def _flag() -> Mapped[bool]:
    return mapped_column(Boolean, default=False, server_default=false())


def _count(default: int = 0) -> Mapped[int]:
    return mapped_column(default=default, server_default=str(default))


def _external_id_unique(table: str) -> UniqueConstraint:
    return UniqueConstraint("source", "external_id", name=f"uq_{table}_source_external_id")


class Engineer(Base):
    __tablename__ = "sdlc_engineers"
    __table_args__ = (UniqueConstraint("source", "login", name="uq_sdlc_engineers_source_login"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str | None] = mapped_column(String(60))
    source: Mapped[str] = _source()


class Sprint(Base):
    __tablename__ = "sdlc_sprints"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date)
    goal: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = _source()


class Issue(Base):
    __tablename__ = "sdlc_issues"
    __table_args__ = (_external_id_unique(__tablename__),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = _source(index=True)
    external_id: Mapped[str | None] = mapped_column(String(64))
    number: Mapped[int | None]
    title: Mapped[str] = mapped_column(String(300))
    module: Mapped[str | None] = mapped_column(String(30), index=True)
    type: Mapped[str] = mapped_column(String(20), default="feature", server_default="feature")
    priority: Mapped[str | None] = mapped_column(String(10))
    estimate_points: Mapped[int | None]
    actual_days: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(10), default="open", server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    sprint_id: Mapped[int | None] = mapped_column(ForeignKey("sdlc_sprints.id"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("sdlc_engineers.id"))
    epic: Mapped[str | None] = mapped_column(String(80), index=True)

    assignee: Mapped[Engineer | None] = relationship()


class PullRequest(Base):
    __tablename__ = "sdlc_pull_requests"
    __table_args__ = (_external_id_unique(__tablename__),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = _source(index=True)
    external_id: Mapped[str | None] = mapped_column(String(64))
    number: Mapped[int | None]
    title: Mapped[str] = mapped_column(String(300))
    author_id: Mapped[int | None] = mapped_column(ForeignKey("sdlc_engineers.id"))
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("sdlc_issues.id"))
    module: Mapped[str | None] = mapped_column(String(30), index=True)
    files_changed: Mapped[int] = _count()
    additions: Mapped[int] = _count()
    deletions: Mapped[int] = _count()
    touches_migration: Mapped[bool] = _flag()
    test_files_changed: Mapped[int] = _count()
    docs_only: Mapped[bool] = _flag()
    modules_touched: Mapped[int] = _count(1)
    touches_governance: Mapped[bool] = _flag()
    review_count: Mapped[int] = _count()
    first_review_hours: Mapped[float | None] = mapped_column(Float)
    rework_commits: Mapped[int] = _count()
    state: Mapped[str] = mapped_column(String(10), default="open", server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime)
    merge_commit_sha: Mapped[str | None] = mapped_column(String(40))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    caused_incident: Mapped[bool] = _flag()
    reverted: Mapped[bool] = _flag()

    author: Mapped[Engineer | None] = relationship()
    issue: Mapped[Issue | None] = relationship()


class CIRun(Base):
    __tablename__ = "sdlc_ci_runs"
    __table_args__ = (_external_id_unique(__tablename__),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = _source(index=True)
    external_id: Mapped[str | None] = mapped_column(String(64))
    pull_request_id: Mapped[int | None] = mapped_column(ForeignKey("sdlc_pull_requests.id"))
    suite: Mapped[str] = mapped_column(String(40), index=True)
    conclusion: Mapped[str] = mapped_column(String(20))
    flaky: Mapped[bool] = _flag()
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_seconds: Mapped[int] = _count()

    pull_request: Mapped[PullRequest | None] = relationship()


class Deployment(Base):
    __tablename__ = "sdlc_deployments"
    __table_args__ = (_external_id_unique(__tablename__),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = _source(index=True)
    external_id: Mapped[str | None] = mapped_column(String(64))
    sha: Mapped[str | None] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(40))
    deployed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    pr_count: Mapped[int] = _count()
    status: Mapped[str] = mapped_column(String(20), default="success", server_default="success")


class Incident(Base):
    __tablename__ = "sdlc_incidents"
    __table_args__ = (_external_id_unique(__tablename__),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = _source(index=True)
    external_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    severity: Mapped[str] = mapped_column(String(10))
    module: Mapped[str | None] = mapped_column(String(30))
    opened_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    caused_by_pr_id: Mapped[int | None] = mapped_column(ForeignKey("sdlc_pull_requests.id"))
    deployment_id: Mapped[int | None] = mapped_column(ForeignKey("sdlc_deployments.id"))

    caused_by_pr: Mapped[PullRequest | None] = relationship()
    deployment: Mapped[Deployment | None] = relationship()
