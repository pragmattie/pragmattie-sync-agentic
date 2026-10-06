"""The shared poll loop: score each new commit once, then keep the ``risk-gate`` in step.

``ORCHESTRATOR_MODE`` switches all of it. ``off`` does nothing at all, not even a read. ``shadow``
does everything, but the status always passes and says what it would be. ``enforce`` makes the
gate real. It fails closed: an exception on one pull request leaves that PR as it was (gated),
and a risk agent that fails leaves a failing gate, never a pass.

``python -m sdlc.runner run`` polls forever, ``once`` polls once, ``dry-run <pr>`` shows the exact
request and a cost ceiling without calling the model or writing anything, and ``try <pr> --yes``
makes one real call and records only a trial row; it never writes to GitHub.
"""

import argparse
import dataclasses
import json
import logging
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc import agent_runs, gate_status
from sdlc.agents import overrides, pr_risk
from sdlc.agents.comment import Meta, comment_head, refresh, render, retier
from sdlc.agents.gate import Approvals, Gate, evaluate
from sdlc.agents.github_effects import Effects
from sdlc.agents.signoff import (
    COMMENT_MARKER,
    WINDOW_AGENT,
    ai_approval,
    approved_on_github,
    describe_window,
    objection_window,
    ticked,
    window_signoff_fields,
)
from sdlc.audit import TRIAL, decisions_for, record_decision
from sdlc.clock import utcnow
from sdlc.config import get_settings
from sdlc.db import get_engine
from sdlc.signals.github import Collector
from sdlc.tables import AgentDecision, PullRequest
from sdlc.tiers import Policy, load_policy

log = logging.getLogger("sdlc.runner")

MAX_ATTEMPTS = 3
RETRY_AFTER = timedelta(minutes=5)
FIRST_LOOKBACK = timedelta(days=30)
STATS_EVERY_SECONDS = 3600
APPROVER_NAME = "Simulated approver"


class Runner:
    def __init__(
        self,
        gh: Any,
        llm: Any,
        policy: Policy,
        mode: str,
        *,
        engine: Engine | None = None,
        diff_char_limit: int = 60000,
    ):
        self.gh = gh
        self.llm = llm
        self.policy = policy
        self.mode = mode
        self.effects = Effects(gh, mode)
        self.engine = engine
        self.diff_char_limit = diff_char_limit
        self._posted: dict[int, tuple[str, str, str]] = {}  # the last status posted, per PR
        self._open: set[int] | None = None  # the open PRs at the last poll
        self._comments_since: datetime | None = None

    def poll_once(self, now: datetime | None = None) -> dict[str, Any]:
        if self.mode == "off":
            return {"mode": "off"}
        now = now or utcnow()
        summary = {"mode": self.mode, "prs": 0, "assessed": 0, "failed": 0, "errors": 0}
        with Session(self.engine or get_engine()) as db:
            collector = Collector(db, self.gh)
            self._guarded(db, summary, "run records", lambda: self._record_runs(db, now))
            listed = self.gh.paginate("/repos/{repo}/pulls", state="open")
            for item in listed:
                if item.get("draft"):
                    continue
                summary["prs"] += 1
                self._guarded(
                    db,
                    summary,
                    f"PR #{item['number']}",
                    lambda item=item: self._poll_pr(db, collector, item, now, summary),
                )
            numbers = {item["number"] for item in listed}
            for number in sorted((self._open or set()) - numbers):
                self._posted.pop(number, None)
                self._guarded(
                    db,
                    summary,
                    f"PR #{number} (left the open list)",
                    lambda number=number: collector.collect_pull_request({"number": number}),
                )
            self._open = numbers
        return summary

    def _guarded(self, db: Session, summary: dict, what: str, step: Callable[[], Any]) -> None:
        """Run one step and commit it; an exception is logged, counted and rolled back."""
        try:
            step()
            db.commit()
        except Exception:
            db.rollback()
            summary["errors"] += 1
            log.exception("%s failed; it is left as it was", what)

    def _record_runs(self, db: Session, now: datetime) -> None:
        """Record the implementer's and reviewer's runs from the workflow bot's new comments."""
        since = self._comments_since or now - FIRST_LOOKBACK
        comments = self.gh.paginate(
            "/repos/{repo}/issues/comments",
            since=since.isoformat() + "Z",
            sort="created",
            direction="asc",
        )
        for comment in comments:
            if (comment.get("user") or {}).get("login") != agent_runs.WORKFLOW_BOT:
                continue
            if record := agent_runs.parse_run(comment.get("body")):
                agent_runs.record_run(db, record, comment, Collector.source)
            if record := agent_runs.parse_review(comment.get("body")):
                agent_runs.record_review(db, record, comment, Collector.source)
        self._comments_since = now

    def _poll_pr(
        self, db: Session, collector: Collector, item: dict, now: datetime, summary: dict
    ) -> None:
        sha = item["head"]["sha"]
        pr = collector.collect_pull_request(item)
        rows = decisions_for(db, pr_risk.AGENT, head_sha=sha, **_subject(pr))
        decision = next((row for row in reversed(rows) if row.status == "ok"), None)
        if decision is None and _may_attempt(rows, now):
            assessment = self._assess_commit(db, pr, item, sha, len(rows) + 1, now)
            summary["assessed"] += 1
            summary["failed"] += 0 if assessment.ok else 1
        else:
            self._recheck(db, pr, item, sha, decision or rows[-1], now)

    def _assess(self, db: Session, pr: PullRequest, item: dict, now: datetime):
        diff = self.effects.read_diff(pr.number)
        try:
            return pr_risk.assess(
                db,
                pr,
                llm=self.llm,
                policy=self.policy,
                description=item.get("body"),
                diff=diff,
                diff_char_limit=self.diff_char_limit,
                now=now,
            )
        except Exception as error:  # a crash is a failed run, gated like an LLMError
            log.exception("The risk agent crashed on PR #%s", pr.number)
            message = f"{type(error).__name__}: {error}"
            return pr_risk.failure(
                db, pr, llm=self.llm, policy=self.policy, message=message, now=now
            )

    def _assess_commit(
        self, db: Session, pr: PullRequest, item: dict, sha: str, attempt: int, now: datetime
    ):
        """Score a new commit, record it, and post the comment, the tier label and the status."""
        assessment = self._assess(db, pr, item, now)
        row = record_decision(
            db,
            agent=pr_risk.AGENT,
            agent_version=pr_risk.AGENT_VERSION,
            trigger="poll",
            now=now,
            head_sha=sha,
            attempt=attempt,
            **_subject(pr),
            **pr_risk.to_decision_fields(assessment),
        )
        rulings = _rulings(db, pr)
        floor = overrides.floor_of(self.policy, pr)
        # a raise made on an earlier commit sticks
        tier = overrides.effective_tier(assessment.assignment.tier, floor, rulings, sha)
        approvals = Approvals()  # a new commit starts with nothing signed off
        gate = self._evaluate(tier, assessment.ok, approvals)
        meta = Meta(row.id, self.mode, APPROVER_NAME, sha)
        body = render(
            assessment,
            gate,
            approvals,
            self.policy,
            meta,
            tier=tier,
            override_lines=_lines(rulings),
        )
        row.action_taken = {
            "comment": self.effects.upsert_comment(pr.number, COMMENT_MARKER, body),
            "label": self.effects.set_tier_label(pr.number, tier),
            "status": self._post_status(pr.number, sha, gate, force=True),
        }
        gate_status.upsert(db, pr, gate, tier=tier, mode=self.mode, now=now)
        return assessment

    def _recheck(
        self,
        db: Session,
        pr: PullRequest,
        item: dict,
        sha: str,
        decision: AgentDecision,
        now: datetime,
    ) -> None:
        """Recompute the gate for a commit already assessed, from what people and agents did.

        A missing or wrong ``tier:`` label, and a risk comment missing for this commit, are put
        right too: a write that failed on the first poll is repaired on a later one.

        New ``/tier`` commands are ruled on first, so the tier in force is the one gated on.
        """
        comments = self.effects.read_comments(pr.number)
        rulings = _rulings(db, pr)
        floor = overrides.floor_of(self.policy, pr)
        before = overrides.effective_tier(decision.tier, floor, rulings, sha)
        rulings += self._rule_new(db, pr, sha, decision.tier, floor, rulings, comments, now)
        tier = overrides.effective_tier(decision.tier, floor, rulings, sha)
        labels = [label["name"] for label in item.get("labels") or []]
        if [name for name in labels if name.startswith("tier:")] != [f"tier:{tier}"]:
            self.effects.set_tier_label(pr.number, tier)
        risk_comment = self.effects.find_comment(pr.number, COMMENT_MARKER, comments)
        boxed, qa_done = ticked(risk_comment.get("body") if risk_comment else None, sha)
        human = approved_on_github(self.effects.read_reviews(pr.number), sha)
        approval = ai_approval(_reviewer_rows(db, pr), sha)
        approvals = Approvals(
            signoff=boxed or human, qa_done=qa_done, ai_review=approval is not None
        )
        merges_at = None
        if approval is not None and not approvals.signoff:
            paths = [file["filename"] for file in self.effects.read_files(pr.number)]
            merges_at = objection_window(self.policy, tier, paths, approval.created_at, comments)
            if merges_at is not None and now >= merges_at:
                approvals.signoff = True
                self._record_window(db, pr, sha, tier, approval, now)
        gate = self._evaluate(tier, decision.status == "ok", approvals)
        if merges_at is not None and now < merges_at:
            gate = dataclasses.replace(gate, description=describe_window(gate, merges_at))
        self._post_status(pr.number, sha, gate)
        body = (risk_comment.get("body") or "") if risk_comment else ""
        if risk_comment and comment_head(body) == sha:
            refreshed = body
            if tier != before:
                refreshed = retier(
                    body,
                    self.policy,
                    tier=tier,
                    agent_tier=decision.tier,
                    approvals=approvals,
                    approver_name=APPROVER_NAME,
                    override_lines=_lines(rulings),
                )
            refreshed = refresh(refreshed, gate, approvals.simulated_approved)
            if refreshed != body:
                self.effects.upsert_comment(pr.number, COMMENT_MARKER, refreshed, comments)
        else:
            assessment = pr_risk.from_decision(db, pr, decision)
            meta = Meta(decision.id, self.mode, APPROVER_NAME, sha)
            lines = _lines(rulings)
            body = render(
                assessment, gate, approvals, self.policy, meta, tier=tier, override_lines=lines
            )
            self.effects.upsert_comment(pr.number, COMMENT_MARKER, body, comments)
        gate_status.upsert(db, pr, gate, tier=tier, mode=self.mode, now=now)

    def _rule_new(
        self,
        db: Session,
        pr: PullRequest,
        sha: str,
        agent_tier: str,
        floor: str,
        rulings: list[overrides.Ruling],
        comments: list[dict],
        now: datetime,
    ) -> list[overrides.Ruling]:
        """Rule on each command not ruled on before, in order, and record each as an audit row."""
        ruled = {ruling.comment_id for ruling in rulings}
        new: list[overrides.Ruling] = []
        for command in overrides.parse_commands(comments, [COMMENT_MARKER]):
            if command.comment_id in ruled:
                continue
            current = overrides.effective_tier(agent_tier, floor, [*rulings, *new], sha)
            ruling = overrides.rule(command, current=current, floor=floor, head_sha=sha)
            record_decision(
                db,
                agent=overrides.AGENT,
                agent_version=overrides.AGENT_VERSION,
                trigger="human",
                now=now,
                head_sha=f"comment-{command.comment_id}"[:40],
                tier=ruling.to_tier if ruling.accepted else None,
                human_override=ruling.as_record(),
                output={"url": command.url, "floor": floor},
                status="ok" if ruling.accepted else "rejected",
                **_subject(pr),
            )
            ruled.add(command.comment_id)
            new.append(ruling)
        return new

    def _record_window(
        self,
        db: Session,
        pr: PullRequest,
        sha: str,
        tier: str,
        approval: AgentDecision,
        now: datetime,
    ) -> None:
        """Record the window's sign-off once a commit, looked up by the head_sha it stores."""
        fields = window_signoff_fields(pr, sha, tier, approval, self.policy)
        if not decisions_for(db, WINDOW_AGENT, head_sha=fields["head_sha"], **_subject(pr)):
            record_decision(db, now=now, **fields)

    def _evaluate(self, tier: str, ok: bool, approvals: Approvals) -> Gate:
        return evaluate(
            self.policy, tier, ok=ok, approvals=approvals, mode=self.mode, needs_ai_review=True
        )

    def _post_status(self, number: int, sha: str, gate: Gate, force: bool = False) -> dict:
        """Post the status unless the same commit, state and description were the last posted."""
        posted = (sha, gate.state, gate.description)
        if not force and self._posted.get(number) == posted:
            return {"skipped": "unchanged"}
        result = self.effects.set_status(sha, gate.state, gate.description)
        if "done" in result:
            self._posted[number] = posted
        return result


def _subject(pr: PullRequest) -> dict[str, Any]:
    return {"subject_type": "pr", "subject_source": pr.source, "subject_id": pr.number}


def _may_attempt(rows: list[AgentDecision], now: datetime) -> bool:
    """None tried yet, or fewer than the limit and the last at least five minutes ago."""
    if not rows:
        return True
    return len(rows) < MAX_ATTEMPTS and now - rows[-1].created_at >= RETRY_AFTER


def _reviewer_rows(db: Session, pr: PullRequest) -> list[AgentDecision]:
    query = select(AgentDecision).where(
        AgentDecision.agent == "reviewer",
        AgentDecision.subject_type == "pr",
        AgentDecision.subject_source == pr.source,
        AgentDecision.subject_id == pr.number,
    )
    return list(db.scalars(query))


def _rulings(db: Session, pr: PullRequest) -> list[overrides.Ruling]:
    """The ``/tier`` rulings on this PR, oldest first, read back from their audit rows."""
    query = select(AgentDecision).where(
        AgentDecision.agent == overrides.AGENT,
        AgentDecision.subject_type == "pr",
        AgentDecision.subject_source == pr.source,
        AgentDecision.subject_id == pr.number,
        AgentDecision.trigger != TRIAL,
    )
    rows = db.scalars(query.order_by(AgentDecision.created_at, AgentDecision.id))
    return [overrides.Ruling.from_record(row.human_override) for row in rows]


def _lines(rulings: list[overrides.Ruling]) -> list[str]:
    return [overrides.describe(ruling) for ruling in rulings]


def run(
    runner: Runner,
    poll_seconds: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    polls: int | None = None,
) -> None:
    """Poll every ``poll_seconds``, forever unless ``polls`` is given. Nothing stops the loop."""
    stats_at = clock()
    limited_logged = None
    done = 0
    while polls is None or done < polls:
        done += 1
        if runner.gh.rate_limited():
            if runner.gh.limited_until != limited_logged:
                limited_logged = runner.gh.limited_until
                log.warning(
                    "GitHub's rate limit is spent; skipping polls until %s UTC", limited_logged
                )
        else:
            try:
                log.info("Poll: %s", runner.poll_once())
            except Exception:
                log.exception("The poll failed; the next one runs as usual")
        if clock() - stats_at >= STATS_EVERY_SECONDS:
            stats_at = clock()
            log.info(
                "GitHub calls sent: %s, of which free 304s: %s",
                runner.gh.sent,
                runner.gh.not_modified,
            )
        sleep(poll_seconds)


def _stored_pr(db: Session, collector: Collector, item: dict) -> PullRequest:
    """The stored PR, collected first when it isn't stored yet."""
    pr = db.scalar(
        select(PullRequest).where(
            PullRequest.source == Collector.source,
            PullRequest.external_id == f"pr-{item['number']}",
        )
    )
    return pr or collector.collect_pull_request(item)


def _dry_run(runner: Runner, db: Session, number: int) -> int:
    item = runner.effects.read_pr(number)
    pr = _stored_pr(db, Collector(db, runner.gh), item)
    result = pr_risk.dry_run(
        db,
        pr,
        llm=runner.llm,
        policy=runner.policy,
        description=item.get("body"),
        diff=runner.effects.read_diff(number),
        diff_char_limit=runner.diff_char_limit,
    )
    db.rollback()  # a dry run writes nothing
    print(json.dumps(result, indent=2))
    return 0


def _try(runner: Runner, db: Session, number: int) -> int:
    item = runner.effects.read_pr(number)
    sha = item["head"]["sha"]
    pr = Collector(db, runner.gh).collect_pull_request(item)  # fresh, even when stored
    assessment = runner._assess(db, pr, item, utcnow())
    highest = db.scalar(
        select(func.max(AgentDecision.attempt)).where(
            AgentDecision.agent == pr_risk.AGENT,
            AgentDecision.subject_type == "pr",
            AgentDecision.subject_source == pr.source,
            AgentDecision.subject_id == pr.number,
            AgentDecision.head_sha == sha,
        )
    )
    row = record_decision(
        db,
        agent=pr_risk.AGENT,
        agent_version=pr_risk.AGENT_VERSION,
        trigger=TRIAL,
        head_sha=sha,
        attempt=max(highest or 0, MAX_ATTEMPTS) + 1,  # clear of the poll's attempts
        **_subject(pr),
        **pr_risk.to_decision_fields(assessment),
    )
    db.commit()
    a = assessment
    print(f"PR #{number} at {sha[:7]}: {a.status}")
    print(f"Score {a.final_score}/100 (rubric {a.raw_score}, adjustment {a.adjustment})")
    print(f"Tier: {a.assignment.tier}")
    for reason in a.top_reasons:
        print(f"  - {reason}")
    if a.justification:
        print(a.justification)
    if a.error:
        print(f"Error: {a.error}")
    cost = a.llm.cost_usd if a.llm else None
    print(f"Recorded as trial decision {row.id}; cost {cost} USD. Nothing was written to GitHub.")
    return 0


def _build(mode: str) -> Runner:
    from sdlc.agents.llm import StructuredLLM
    from sdlc.github_client import GitHubClient

    settings = get_settings()
    return Runner(
        GitHubClient(),
        StructuredLLM(),
        load_policy(),
        mode,
        diff_char_limit=settings.diff_char_limit,
    )


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.runner", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="poll forever")
    commands.add_parser("once", help="poll once and print the summary")
    dry = commands.add_parser("dry-run", help="show the request and its cost; call nothing")
    dry.add_argument("pr", type=int)
    trial = commands.add_parser("try", help="make one real call; write nothing to GitHub")
    trial.add_argument("pr", type=int)
    trial.add_argument("--yes", action="store_true", help="confirm the real, paid call")
    args = parser.parse_args(argv)

    if args.command == "try" and not args.yes:
        print("This makes one real, paid model call. Run it again with --yes to go ahead.")
        return 1
    settings = get_settings()
    runner = runner or _build(settings.orchestrator_mode)
    if args.command == "run":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        log.info("Polling every %ss in %s mode", settings.poll_seconds, runner.mode)
        run(runner, settings.poll_seconds)
        return 0
    if args.command == "once":
        print(json.dumps(runner.poll_once()))
        return 0
    with Session(runner.engine or get_engine()) as db:
        if args.command == "dry-run":
            return _dry_run(runner, db, args.pr)
        return _try(runner, db, args.pr)


if __name__ == "__main__":
    sys.exit(main())
