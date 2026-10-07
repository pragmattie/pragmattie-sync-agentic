"""Grade the triage agent against a person's own labels, blind, with bars fixed in advance.

The bars below were fixed before the first run. A pattern of misses is a prompt fix, never a
reason to lower a bar or change the labels. Priority is reported but never gated.

Both sets are v1's issues, not this repository's: v1's backlog #6-#45 are renumbered
8006-8045 and the holdout is 9001-9020. There are no stored decisions to grade, so both are
always graded fresh, and their trial rows are recorded as simulated. ``agent_answers`` grades
stored decisions for a future set drawn from this repository's own issues.

A blind run re-classifies each issue's saved text with no labels shown, and with similar issues
drawn from simulated history only, so no other eval issue can appear as an example with its
labels. Each call is recorded as a trial row; it never writes to GitHub, and without ``yes`` it
calls nothing and only shows what it would send and cost.

``python -m sdlc.eval`` shows what a blind run of the backlog set would send and cost, and
``--yes`` runs it and grades those answers. ``--fresh`` is accepted and changes nothing.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc.agents import triage, triage_comment
from sdlc.audit import TRIAL, record_decision
from sdlc.db import get_engine
from sdlc.runner import MAX_ATTEMPTS
from sdlc.tables import AgentDecision

BARS = {"module": 0.85, "type": 0.90, "points_within_one": 0.70}
POINTS = triage.POINTS
DIMENSIONS = ("module", "type", "points", "priority")
CANDIDATE_SOURCE = "synthetic"
EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"


@dataclass(frozen=True)
class EvalSet:
    labels: Path
    issues: Path
    source: str  # the audit source of its trial rows


SETS = {
    # v1's backlog issues #6-#45, renumbered 8006-8045 (v1's #6 is 8006).
    "backlog": EvalSet(EVAL_DIR / "triage_eval_set.json", EVAL_DIR / "_issues.json", "synthetic"),
    # v1's holdout issues, 9001-9020.
    "holdout": EvalSet(
        EVAL_DIR / "triage_holdout_set.json", EVAL_DIR / "holdout_issues.json", "synthetic"
    ),
}


def points_step(a: int, b: int) -> int:
    """How many steps apart two values in ``POINTS`` are: 3 and 8 are two steps apart."""
    return abs(POINTS.index(a) - POINTS.index(b))


def load_labels(path: Path) -> list[dict[str, Any]]:
    """A JSON list of ``{number, module, type, points, priority?, note?}``."""
    return json.loads(path.read_text(encoding="utf-8"))


def load_issues(path: Path) -> dict[int, dict[str, Any]]:
    """A JSON object mapping each issue number to ``{title, body}``."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(number): issue for number, issue in raw.items()}


def _answer_of(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "module": output.get("module"),
        "type": output.get("type"),
        "points": output.get("estimate_points"),
        "priority": output.get("priority"),
    }


def agent_answers(db: Session) -> dict[int, dict[str, Any]]:
    """The agent's latest ``ok``, non-trial decision on each GitHub issue."""
    query = select(AgentDecision).where(
        AgentDecision.agent == triage.AGENT,
        AgentDecision.subject_type == "issue",
        AgentDecision.subject_source == "github",
        AgentDecision.status == "ok",
        AgentDecision.trigger != TRIAL,
    )
    answers: dict[int, dict[str, Any]] = {}
    for row in db.scalars(query.order_by(AgentDecision.created_at, AgentDecision.id)):
        answers[row.subject_id] = _answer_of(row.output or {})  # newest wins
    return answers


def _rate(hits: int, total: int) -> float | None:
    return hits / total if total else None


def grade(labels: list[dict[str, Any]], answers: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Each issue against the person's labels; a missing answer counts as a miss."""
    rows = []
    hits: Counter = Counter()
    totals: Counter = Counter()
    disagreements: dict[tuple, list[int]] = defaultdict(list)
    for label in labels:
        number = label["number"]
        agent = answers.get(number)
        person = {key: label.get(key) for key in DIMENSIONS}
        match: dict[str, bool | None] = {}
        for key in DIMENSIONS:
            if person[key] is None:
                match[key] = None  # unlabelled priority is not graded
                continue
            totals[key] += 1
            match[key] = agent is not None and agent.get(key) == person[key]
            hits[key] += match[key]
            if agent is not None and not match[key]:
                disagreements[(key, person[key], agent.get(key))].append(number)
        within = (
            agent is not None
            and agent.get("points") in POINTS
            and points_step(person["points"], agent["points"]) <= 1
        )
        match["points_within_one"] = within
        hits["points_within_one"] += within
        totals["points_within_one"] += 1
        rows.append({"number": number, "person": person, "agent": agent, "match": match})

    rates = {key: _rate(hits[key], totals[key]) for key in (*DIMENSIONS, "points_within_one")}
    bars = {
        key: {"rate": rates[key], "bar": bar, "passed": (rates[key] or 0.0) >= bar}
        for key, bar in BARS.items()
    }
    order = {key: index for index, key in enumerate(DIMENSIONS)}
    patterns = [
        {
            "dimension": key,
            "person": person,
            "agent": agent,
            "count": len(numbers),
            "issues": numbers,
        }
        for (key, person, agent), numbers in disagreements.items()
    ]
    patterns.sort(
        key=lambda p: (-p["count"], order[p["dimension"]], str(p["person"]), str(p["agent"]))
    )
    return {
        "issues": len(labels),
        "rows": rows,
        "rates": rates,
        "bars": bars,
        "passed": all(bar["passed"] for bar in bars.values()),
        "missing": [row["number"] for row in rows if row["agent"] is None],
        "patterns": patterns,
    }


def _highest_attempt(db: Session, source: str, number: int, version: str) -> int:
    query = select(func.max(AgentDecision.attempt)).where(
        AgentDecision.agent == triage.AGENT,
        AgentDecision.subject_type == "issue",
        AgentDecision.subject_source == source,
        AgentDecision.subject_id == number,
        AgentDecision.head_sha == version,
    )
    return db.scalar(query) or 0


def run_blind(db: Session, llm: Any, set_name: str, *, yes: bool) -> dict[str, Any]:
    """Re-run the agent on each issue's saved text, blind; without ``yes``, only the cost.

    No labels are shown and similar issues come from simulated history only. Each call is
    recorded as a trial row with the set's source and committed at once, so a paid call is
    never lost. Nothing is written to GitHub.
    """
    chosen = SETS[set_name]
    numbers = [label["number"] for label in load_labels(chosen.labels)]
    issues = load_issues(chosen.issues)
    if not yes:
        ceiling = sum(
            triage.dry_run(
                db,
                llm=llm,
                title=issues[number]["title"],
                body=issues[number].get("body"),
                labels=[],
                candidate_source=CANDIDATE_SOURCE,
            )["max_cost_usd"]
            for number in numbers
        )
        db.rollback()  # a preview writes nothing
        return {
            "set": set_name,
            "calls": len(numbers),
            "max_cost_usd": round(ceiling, 6),
            "issues": [{"number": n, "title": issues[n]["title"]} for n in numbers],
        }

    answers: dict[int, dict[str, Any]] = {}
    cost = 0.0
    for number in numbers:
        issue = issues[number]
        try:
            assessment = triage.assess(
                db,
                llm=llm,
                title=issue["title"],
                body=issue.get("body"),
                labels=[],
                candidate_source=CANDIDATE_SOURCE,
            )
        except Exception as error:  # a crash is a failed run, counted as a miss
            assessment = triage.failure(llm, f"{type(error).__name__}: {error}")
        version = triage_comment.content_version(issue["title"], issue.get("body"))
        attempt = max(_highest_attempt(db, chosen.source, number, version), MAX_ATTEMPTS) + 1
        record_decision(
            db,
            agent=triage.AGENT,
            agent_version=triage.AGENT_VERSION,
            subject_type="issue",
            subject_source=chosen.source,
            subject_id=number,
            trigger=TRIAL,
            head_sha=version,
            attempt=attempt,
            **triage.to_decision_fields(assessment),
        )
        db.commit()
        if assessment.llm:
            cost += assessment.llm.cost_usd or 0.0
        if assessment.ok:
            answers[number] = {
                "module": assessment.module,
                "type": assessment.type,
                "points": assessment.estimate_points,
                "priority": assessment.priority,
            }
    return {"set": set_name, "calls": len(numbers), "cost_usd": round(cost, 6), "answers": answers}


def _percent(rate: float | None) -> str:
    return "n/a" if rate is None else f"{rate:.0%}"


def describe(report: dict[str, Any]) -> str:
    """Plain text: each rate against its bar, priority, then the patterns of misses."""
    names = {"module": "Module", "type": "Type", "points_within_one": "Points within one step"}
    lines = [f"Graded {report['issues']} issues."]
    for key, bar in report["bars"].items():
        verdict = "PASS" if bar["passed"] else "FAIL"
        lines.append(f"  {names[key]}: {_percent(bar['rate'])} (bar {bar['bar']:.0%}) {verdict}")
    lines.append(f"  Points exact: {_percent(report['rates']['points'])}")
    lines.append(f"  Priority: {_percent(report['rates']['priority'])} (reported, not gated)")
    if report["missing"]:
        missing = ", ".join(f"#{number}" for number in report["missing"])
        lines.append(f"Missing answers (counted as misses): {missing}")
    if report["patterns"]:
        lines.append("Patterns (person -> agent):")
        for p in report["patterns"]:
            issues = ", ".join(f"#{number}" for number in p["issues"])
            lines.append(
                f"  {p['count']}x {p['dimension']}: {p['person']} -> {p['agent']} ({issues})"
            )
    else:
        lines.append("No disagreements.")
    return "\n".join(lines)


def main(argv: list[str] | None = None, engine: Engine | None = None, llm: Any = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sdlc.eval", description=__doc__.split("\n")[0])
    parser.add_argument("--set", choices=sorted(SETS), default="backlog", dest="set_name")
    parser.add_argument("--fresh", action="store_true", help="accepted; every set runs fresh")
    parser.add_argument("--yes", action="store_true", help="confirm the real, paid calls")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    labels = load_labels(SETS[args.set_name].labels)
    with Session(engine or get_engine()) as db:
        run = run_blind(db, llm or triage.triage_llm(), args.set_name, yes=args.yes)
    if not args.yes:
        if args.json:
            print(json.dumps(run))
        else:
            print(
                f"A blind run of the {args.set_name} set makes {run['calls']} real, paid model "
                f"calls, costing at most {run['max_cost_usd']} USD. It sends each issue's title "
                "and body, with no labels and similar issues from simulated history only:"
            )
            for issue in run["issues"]:
                print(f"  #{issue['number']} {issue['title']}")
            print("Nothing is written to GitHub. Run it again with --yes to go ahead.")
        return 0
    report = grade(labels, run["answers"])
    if args.json:
        print(
            json.dumps(
                {**report, "set": args.set_name, "calls": run["calls"], "cost_usd": run["cost_usd"]}
            )
        )
    else:
        print(describe(report))
        print(
            f"Ran {run['calls']} blind calls for {run['cost_usd']} USD, recorded as trial "
            "rows. Nothing was written to GitHub."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
