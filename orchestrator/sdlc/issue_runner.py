"""The triage agent's poll loop: classify each open issue once per version of its text.

It runs in the same loop and mode as the PR risk agent. ``off`` does nothing at all. Each issue
gets its labels and one comment explaining them; an exception on one issue leaves it as it was.
A dimension a person has changed since the agent's last run is left alone, now and later. The
agent never closes, assigns or edits an issue, and never removes ``needs-info`` or
``possible-duplicate``: a person does. A ``retriage`` label runs it again whatever the version.

``python -m sdlc.issue_runner once`` polls once, ``dry-run <n>`` shows the exact request and a
cost ceiling without calling the model or writing anything, and ``try <n> --yes`` makes one real
call and records only a trial row; it never writes to GitHub.
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc.agents import triage, triage_comment
from sdlc.agents.github_effects import Effects
from sdlc.audit import TRIAL, decisions_for, latest_decision, record_decision
from sdlc.backlog import PREFIX_COLOURS
from sdlc.clock import utcnow
from sdlc.config import get_settings
from sdlc.db import get_engine
from sdlc.runner import MAX_ATTEMPTS, guarded, may_attempt
from sdlc.signals.github import Collector, labels_of
from sdlc.tables import AgentDecision

log = logging.getLogger("sdlc.issue_runner")

DIMENSIONS = ("module", "type", "priority", "points")
RETRIAGE = "retriage"
INCIDENT = "incident"
NEEDS_INFO = (
    "needs-info",
    "F9A03F",
    "The triage agent needs more detail before it can classify this",
)
POSSIBLE_DUPLICATE = (
    "possible-duplicate",
    "B60205",
    "The triage agent found a similar existing issue",
)


class IssueRunner:
    def __init__(self, gh: Any, llm: Any, mode: str, *, engine: Engine | None = None):
        self.gh = gh
        self.llm = llm
        self.mode = mode
        self.effects = Effects(gh, mode)
        self.engine = engine

    def poll_once(self, now: datetime | None = None) -> dict[str, Any]:
        if self.mode == "off":
            return {"mode": "off"}
        now = now or utcnow()
        summary = {"mode": self.mode, "issues": 0, "triaged": 0, "failed": 0, "errors": 0}
        with Session(self.engine or get_engine()) as db:
            collector = Collector(db, self.gh)
            for item in self.gh.paginate("/repos/{repo}/issues", state="open"):
                if "pull_request" in item or INCIDENT in _names(item):
                    continue
                summary["issues"] += 1
                guarded(
                    db,
                    summary,
                    f"Issue #{item['number']}",
                    lambda item=item: self._poll_issue(db, collector, item, now, summary),
                )
        return summary

    def _poll_issue(
        self, db: Session, collector: Collector, item: dict, now: datetime, summary: dict
    ) -> None:
        collector.collect_issue(item)  # a person's label changes are kept on the row
        version = triage_comment.content_version(item["title"], item.get("body"))
        retriage = RETRIAGE in _names(item)
        rows = decisions_for(db, triage.AGENT, head_sha=version, **_subject(item["number"]))
        done = any(row.status == "ok" for row in rows)
        if not retriage and (done or not may_attempt(rows, now)):
            return
        assessment = self._triage(db, item, version, retriage, now)
        summary["triaged"] += 1
        summary["failed"] += 0 if assessment.ok else 1

    def _assess(self, db: Session, item: dict) -> triage.Assessment:
        try:
            return triage.assess(
                db,
                llm=self.llm,
                title=item["title"],
                body=item.get("body"),
                labels=_names(item),
                number=item["number"],
            )
        except Exception as error:  # a crash is a failed run
            log.exception("The triage agent crashed on issue #%s", item["number"])
            return triage.failure(self.llm, f"{type(error).__name__}: {error}")

    def _triage(
        self, db: Session, item: dict, version: str, retriage: bool, now: datetime
    ) -> triage.Assessment:
        """Classify one version, write the labels, record the decision and upsert the comment."""
        number = item["number"]
        subject = _subject(number)
        last = latest_decision(db, triage.AGENT, **subject)
        assessment = self._assess(db, item)
        left = _overrides(last, labels_of(item)) if assessment.ok else []
        actions: dict[str, Any] = {}
        if assessment.ok:
            proposed = _proposed(assessment)
            for key in DIMENSIONS:
                if key in left:
                    actions[key] = {"skipped": "set by a person"}
                else:
                    actions[key] = self.effects.set_dimension_label(
                        number, key, proposed[key], PREFIX_COLOURS[key]
                    )
            if assessment.needs_info:
                actions[NEEDS_INFO[0]] = self.effects.add_label_if_absent(number, *NEEDS_INFO)
            if assessment.duplicate_of:
                actions[POSSIBLE_DUPLICATE[0]] = self.effects.add_label_if_absent(
                    number, *POSSIBLE_DUPLICATE
                )
        if retriage and assessment.ok:
            actions[RETRIAGE] = self.effects.remove_label(number, RETRIAGE)
        if retriage:
            trigger = "retriage"
        else:
            trigger = "edit" if last is not None else "opened"
        row = record_decision(
            db,
            agent=triage.AGENT,
            agent_version=triage.AGENT_VERSION,
            trigger=trigger,
            now=now,
            head_sha=version,
            attempt=_highest_attempt(db, number, version) + 1,
            human_override={"dimensions": left} if left else None,
            **subject,
            **triage.to_decision_fields(assessment),
        )
        body = triage_comment.render(assessment, triage_comment.Meta(row.id, version, tuple(left)))
        actions["comment"] = self.effects.upsert_comment(number, triage_comment.MARKER, body)
        row.action_taken = actions
        return assessment


def _names(item: dict) -> list[str]:
    return [label["name"] for label in item.get("labels") or []]


def _subject(number: int) -> dict[str, Any]:
    return {"subject_type": "issue", "subject_source": Collector.source, "subject_id": number}


def _proposed(assessment: triage.Assessment) -> dict[str, str]:
    """The label value the agent proposes for each dimension."""
    return {
        "module": assessment.module,
        "type": assessment.type,
        "priority": assessment.priority,
        "points": str(assessment.estimate_points),
    }


def _overrides(last: AgentDecision | None, labels: dict[str, str]) -> list[str]:
    """The dimensions a person has set: changed since the agent's last run, or left alone then.

    A label the agent failed to write doesn't count as a person's change.
    """
    if last is None:
        return []
    output = last.output or {}
    earlier = set((last.human_override or {}).get("dimensions", []))
    actions = last.action_taken or {}
    written = {
        "module": output.get("module"),
        "type": output.get("type"),
        "priority": output.get("priority"),
        "points": None if output.get("estimate_points") is None else str(output["estimate_points"]),
    }
    changed = {
        key
        for key in DIMENSIONS
        if "done" in (actions.get(key) or {}) and labels.get(key) != written[key]
    }
    return [key for key in DIMENSIONS if key in earlier | changed]


def _highest_attempt(db: Session, number: int, version: str) -> int:
    """The highest attempt recorded at this version, trial rows included, so none collide."""
    query = select(func.max(AgentDecision.attempt)).where(
        AgentDecision.agent == triage.AGENT,
        AgentDecision.subject_type == "issue",
        AgentDecision.subject_source == Collector.source,
        AgentDecision.subject_id == number,
        AgentDecision.head_sha == version,
    )
    return db.scalar(query) or 0


def _dry_run(runner: IssueRunner, db: Session, number: int) -> int:
    item = runner.effects.read_issue(number)
    result = triage.dry_run(
        db,
        llm=runner.llm,
        title=item["title"],
        body=item.get("body"),
        labels=_names(item),
        number=number,
    )
    db.rollback()  # a dry run writes nothing
    print(json.dumps(result, indent=2))
    return 0


def _try(runner: IssueRunner, db: Session, number: int) -> int:
    item = runner.effects.read_issue(number)
    version = triage_comment.content_version(item["title"], item.get("body"))
    assessment = runner._assess(db, item)
    row = record_decision(
        db,
        agent=triage.AGENT,
        agent_version=triage.AGENT_VERSION,
        trigger=TRIAL,
        head_sha=version,
        attempt=max(_highest_attempt(db, number, version), MAX_ATTEMPTS) + 1,
        **_subject(number),
        **triage.to_decision_fields(assessment),
    )
    db.commit()
    a = assessment
    print(f"Issue #{number} at version {version}: {a.status}")
    if a.ok:
        print(f"{a.module} / {a.type} ({a.priority}), {a.estimate_points} points")
        print(f"Confidence {a.confidence}; needs info: {a.needs_info}")
        if a.duplicate_of:
            print(f"Possibly a duplicate of #{a.duplicate_of}")
        if a.rationale:
            print(a.rationale)
        for question in a.questions:
            print(f"  ? {question}")
    if a.error:
        print(f"Error: {a.error}")
    cost = a.llm.cost_usd if a.llm else None
    print(f"Recorded as trial decision {row.id}; cost {cost} USD. Nothing was written to GitHub.")
    return 0


def build(mode: str, gh: Any = None, engine: Engine | None = None) -> IssueRunner:
    from sdlc.github_client import GitHubClient

    return IssueRunner(gh or GitHubClient(), triage.triage_llm(), mode, engine=engine)


def main(argv: list[str] | None = None, runner: IssueRunner | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.issue_runner", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("once", help="poll once and print the summary")
    dry = commands.add_parser("dry-run", help="show the request and its cost; call nothing")
    dry.add_argument("issue", type=int)
    trial = commands.add_parser("try", help="make one real call; write nothing to GitHub")
    trial.add_argument("issue", type=int)
    trial.add_argument("--yes", action="store_true", help="confirm the real, paid call")
    args = parser.parse_args(argv)

    if args.command == "try" and not args.yes:
        print("This makes one real, paid model call. Run it again with --yes to go ahead.")
        return 1
    runner = runner or build(get_settings().orchestrator_mode)
    if args.command == "once":
        print(json.dumps(runner.poll_once()))
        return 0
    with Session(runner.engine or get_engine()) as db:
        if args.command == "dry-run":
            return _dry_run(runner, db, args.issue)
        return _try(runner, db, args.issue)


if __name__ == "__main__":
    sys.exit(main())
