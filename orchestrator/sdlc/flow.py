"""How work moved through the delivery stages over time: daily stage counts, throughput, cycle time.

Pure computation over the signal tables, for one source at a time. No stage is stored: like the
delivery board, each issue's stage on a day is derived from what had happened by the end of it.

- **Backlog** at creation.
- **Triaged:** a synthetic issue is born triaged. A real issue is triaged at its first ok
  ``triage`` decision, or, without one, at creation when its module and points are set.
- **In progress** when a pull request linked to it is opened, **In review** at that PR's first ok
  ``pr_risk`` decision, **Merged** at its merge and **Production** when a deployment ships it
  (``sdlc.shipping``: the earliest successful deployment of the same source whose commit
  includes the merge, so a deploy of an older commit never ships it). A PR closed unmerged
  stops counting when it closes, and when several PRs are linked the most advanced decides.
  Gated has no history, so it is In review here.

An issue closed without a merged PR leaves the flow when it closes; one with no close time can't
be placed in time, so it is left out.

``recently_merged`` lists the newest merged pull requests of both sources, each with the release
verdict that names it, if any: the newest non-trial ``release_gate`` row whose ``prs`` include it.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sdlc.agents import release_gate
from sdlc.audit import TRIAL
from sdlc.metrics import _midnight, _monday, _percentile, _round
from sdlc.shipping import deploys, shipped_at
from sdlc.tables import SOURCES, AgentDecision, Issue, PullRequest

STAGES = ("Backlog", "Triaged", "In progress", "In review", "Merged", "Production")
BACKLOG, TRIAGED, IN_PROGRESS, IN_REVIEW, MERGED, PRODUCTION = range(len(STAGES))
RECENTLY_MERGED = 10


@dataclass
class _Pr:
    opened: datetime
    reviewed: datetime | None
    merged: datetime | None
    deployed: datetime | None
    closed: datetime | None

    def stage(self, end: datetime) -> int | None:
        """This PR's stage for events before ``end``, or None when it doesn't count."""
        if self.merged is not None and self.merged < end:
            return PRODUCTION if self.deployed is not None and self.deployed < end else MERGED
        if self.closed is not None and self.closed < end:
            return None
        if self.reviewed is not None and self.reviewed < end:
            return IN_REVIEW
        return IN_PROGRESS if self.opened < end else None


@dataclass
class _Item:
    created: datetime
    triaged: datetime | None
    left: datetime | None  # closed without a merged PR
    prs: list[_Pr] = field(default_factory=list)

    def stage(self, end: datetime) -> int | None:
        """The issue's stage at ``end``, or None when it isn't in the flow then."""
        if self.created >= end or (self.left is not None and self.left < end):
            return None
        base = TRIAGED if self.triaged is not None and self.triaged < end else BACKLOG
        return max([base, *(s for pr in self.prs if (s := pr.stage(end)) is not None)])

    @property
    def started(self) -> datetime | None:
        return min((pr.opened for pr in self.prs), default=None)

    @property
    def merged(self) -> datetime | None:
        return min((pr.merged for pr in self.prs if pr.merged is not None), default=None)


def _first_ok(db: Session, agent: str, subject_type: str, source: str) -> dict[int, datetime]:
    rows = db.execute(
        select(AgentDecision.subject_id, func.min(AgentDecision.created_at))
        .where(
            AgentDecision.agent == agent,
            AgentDecision.subject_type == subject_type,
            AgentDecision.subject_source == source,
            AgentDecision.status == "ok",
        )
        .group_by(AgentDecision.subject_id)
    )
    return dict(rows.all())


def _items(db: Session, source: str) -> list[_Item]:
    triaged = _first_ok(db, "triage", "issue", source) if source != "synthetic" else {}
    reviewed = _first_ok(db, "pr_risk", "pr", source)
    shipping = deploys(db, source)

    prs_by_issue = defaultdict(list)
    for pr in db.scalars(
        select(PullRequest).where(PullRequest.source == source, PullRequest.issue_id.is_not(None))
    ):
        prs_by_issue[pr.issue_id].append(
            _Pr(
                opened=pr.created_at,
                reviewed=reviewed.get(pr.number) if pr.number is not None else None,
                merged=pr.merged_at,
                deployed=shipped_at(pr.merged_at, shipping),
                closed=pr.closed_at,
            )
        )

    items = []
    for issue in db.scalars(select(Issue).where(Issue.source == source)):
        prs = prs_by_issue[issue.id]
        if source == "synthetic":
            triaged_at = issue.created_at
        elif issue.number is not None and issue.number in triaged:
            triaged_at = triaged[issue.number]
        elif issue.module and issue.estimate_points is not None:
            triaged_at = issue.created_at
        else:
            triaged_at = None
        has_merge = any(pr.merged is not None for pr in prs)
        closed_unmerged = issue.state == "closed" and not has_merge
        if closed_unmerged and issue.closed_at is None:
            continue  # it can't be placed in time
        left = issue.closed_at if closed_unmerged else None
        items.append(_Item(issue.created_at, triaged_at, left, prs))
    return items


def available(db: Session) -> dict[str, int]:
    """How many items each source has."""
    counts = dict(
        db.execute(select(Issue.source, func.count(Issue.id)).group_by(Issue.source)).all()
    )
    return {source: counts.get(source, 0) for source in SOURCES}


def flow(db: Session, *, source: str, days: int, today: date) -> dict:
    """Stage counts at the end of each of the ``days`` days to ``today``, with throughput and
    cycle time for items merged in that window."""
    items = _items(db, source)
    dates = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    start, end = _midnight(dates[0]), _midnight(today + timedelta(days=1))

    series = []
    present = set()
    for day in dates:
        counts = dict.fromkeys(STAGES, 0)
        for index, item in enumerate(items):
            stage = item.stage(_midnight(day + timedelta(days=1)))
            if stage is not None:
                counts[STAGES[stage]] += 1
                present.add(index)
        series.append({"date": day, "counts": counts})

    weeks = {}
    monday = _monday(dates[0])
    while monday <= today:
        weeks[monday] = 0
        monday += timedelta(days=7)
    cycle_days = []
    for item in items:
        merged = item.merged
        if merged is None or not start <= merged < end:
            continue
        weeks[_monday(merged.date())] += 1
        if item.started is not None:
            cycle_days.append((merged - item.started).total_seconds() / 86400)

    return {
        "source": source,
        "days": days,
        "stages": list(STAGES),
        "series": series,
        "throughput": [{"week": week, "merged": merged} for week, merged in weeks.items()],
        "cycle_time_days": {
            "median": _round(_percentile(cycle_days, 0.5)),
            "p85": _round(_percentile(cycle_days, 0.85)),
        },
        "items": len(present),
    }


def _release(row: AgentDecision) -> dict:
    """The verdict's state and description; simulated rows keep them in ``output``.

    In shadow mode the posted state always passes, so the state is the verdict's own
    (``would_be``) and ``shadow`` says it was never enforced.
    """
    action, output = row.action_taken or {}, row.output or {}
    shadow = output.get("mode") == "shadow"
    verdict_state = output.get("would_be") or release_gate.STATES.get(output.get("verdict"))
    return {
        "state": verdict_state if shadow else action.get("state") or verdict_state,
        "description": action.get("description") or output.get("description"),
        "shadow": shadow,
    }


def recently_merged(db: Session, limit: int = RECENTLY_MERGED) -> list[dict]:
    """The ``limit`` newest merged PRs, real and simulated, each with its release verdict."""
    prs = db.scalars(
        select(PullRequest)
        .where(PullRequest.merged_at.is_not(None))
        .order_by(PullRequest.merged_at.desc(), PullRequest.id.desc())
        .limit(limit)
    ).all()
    if not prs:
        return []
    wanted = {(pr.source, pr.number) for pr in prs}
    verdicts: dict[tuple[str, int], dict] = {}
    rows = db.scalars(
        select(AgentDecision)
        .where(
            AgentDecision.agent == release_gate.AGENT,
            AgentDecision.trigger != TRIAL,
            # a verdict naming a PR comes after its merge
            AgentDecision.created_at >= min(pr.merged_at for pr in prs),
        )
        .order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc())
    )
    for row in rows:
        for number in (row.output or {}).get("prs") or []:
            key = (row.subject_source, number)
            if key in wanted and key not in verdicts:
                verdicts[key] = _release(row)
        if len(verdicts) == len(wanted):
            break
    return [
        {
            "source": pr.source,
            "number": pr.number,
            "title": pr.title,
            "merged_at": pr.merged_at,
            "release": verdicts.get((pr.source, pr.number)),
        }
        for pr in prs
    ]
