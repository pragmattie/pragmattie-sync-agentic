"""Grade the risk score against history before it may gate anything.

Every merged pull request is scored as it would have been when it was opened (4.2), tiered, and
compared with what happened: did it cause an incident? One history is a noisy judge, so the bars
are read on many generated histories pooled together.

The bars were fixed on 2026-09-20, before the first pooled run:

1. The T0 incident rate is at most a quarter of the overall incident rate (``T0_RATE_LIMIT``).
2. The top decile by score holds more than half of the incident PRs (``TOP_DECILE_CAPTURE``).

A failing bar is fixed by tuning the weights and recording the change, never by changing outcomes
(the history, the generator or the bars themselves).
"""

import math
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from sdlc import scoring, synth
from sdlc.db import Base
from sdlc.governance import assign_tier, facts_of
from sdlc.tables import PullRequest
from sdlc.tiers import TIER_IDS, Policy

GENERATED_NOW = datetime(2026, 9, 18, 12, 0)
T0_RATE_LIMIT = 0.25
TOP_DECILE_CAPTURE = 0.5
THRESHOLD_TIERS = ("T1", "T2", "T3")


# The locked pooled grading (PR #162, 2026-10-05): ``calibrate_many`` over seeds 1..30.
POOLED_RESULT = {
    "graded_on": "2026-10-05",
    "pull_request": 162,
    "histories": 30,
    "merged_prs": 15044,
    "incident_prs": 451,
    "overall_rate": 0.03,
    "t0_rate": 0.0018,
    "top_decile": {"capture": 0.5787, "lowest": 0.375, "highest": 0.8},
    "bars": {
        "t0_rate_at_most_a_quarter_of_overall": True,
        "top_decile_captures_majority_pooled": True,
    },
}
"""The pooled grading is the judge of the risk score; one history is noisy.

A single history holds about 500 merged PRs and 15 incident PRs, so its bars swing with
chance: across the 30 histories the top decile caught anywhere from 37.5% to 80.0% of incident
PRs. Read a single history's report beside this one, never instead of it.
"""


def score_history(
    db: Session, policy: Policy, source: str | None = None
) -> list[tuple[int | None, int, str, bool]]:
    """Every merged PR, by number, as ``(number, score, tier, incident)``; one source if given."""
    query = select(PullRequest).where(PullRequest.merged_at.is_not(None))
    if source is not None:
        query = query.where(PullRequest.source == source)
    prs = db.scalars(query.order_by(PullRequest.number, PullRequest.id)).all()
    rows = []
    for pr in prs:
        score = scoring.score_pull_request(db, pr).total
        tier = assign_tier(policy, score, facts_of(pr)).tier
        rows.append((pr.number, score, tier, bool(pr.caused_incident)))
    return rows


def _ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _threshold(flagged: int, caught: int, incidents: int) -> dict:
    return {
        "flagged": flagged,
        "incidents": caught,
        "precision": _ratio(caught, flagged),
        "recall": _ratio(caught, incidents),
    }


def summarize(rows: list[tuple[int | None, int, str, bool]]) -> dict:
    """The calibration report for scored rows, as ``score_history`` returns them."""
    incidents = sum(1 for *_, incident in rows if incident)
    by_tier = {tier: {"prs": 0, "incidents": 0} for tier in TIER_IDS}
    for _, _, tier, incident in rows:
        by_tier[tier]["prs"] += 1
        by_tier[tier]["incidents"] += int(incident)

    size = math.ceil(len(rows) / 10)
    ranked = sorted(rows, key=lambda row: (-row[1], row[0] is None, row[0] or 0))
    decile_incidents = sum(1 for *_, incident in ranked[:size] if incident)

    thresholds = {}
    for tier in THRESHOLD_TIERS:
        at_or_above = TIER_IDS[TIER_IDS.index(tier) :]
        flagged = sum(by_tier[t]["prs"] for t in at_or_above)
        caught = sum(by_tier[t]["incidents"] for t in at_or_above)
        thresholds[tier] = _threshold(flagged, caught, incidents)

    return {
        "merged_prs": len(rows),
        "incident_prs": incidents,
        "by_tier": by_tier,
        "top_decile": {
            "size": size,
            "incidents": decile_incidents,
            "capture": _ratio(decile_incidents, incidents),
        },
        "thresholds": thresholds,
        "bars": {
            "t0_has_no_incidents": by_tier["T0"]["incidents"] == 0,
            "top_decile_captures_majority": decile_incidents > incidents / 2,
        },
    }


def calibrate(db: Session, policy: Policy, source: str | None = None) -> dict:
    return summarize(score_history(db, policy, source))


def pool(runs: list[dict]) -> dict:
    """Pools single-history reports into one."""
    merged = sum(run["merged_prs"] for run in runs)
    incidents = sum(run["incident_prs"] for run in runs)
    by_tier = {
        tier: {
            "prs": sum(run["by_tier"][tier]["prs"] for run in runs),
            "incidents": sum(run["by_tier"][tier]["incidents"] for run in runs),
        }
        for tier in TIER_IDS
    }
    overall_rate = _ratio(incidents, merged) or 0.0
    t0_rate = _ratio(by_tier["T0"]["incidents"], by_tier["T0"]["prs"]) or 0.0

    decile_size = sum(run["top_decile"]["size"] for run in runs)
    decile_incidents = sum(run["top_decile"]["incidents"] for run in runs)
    captures = [
        run["top_decile"]["capture"] for run in runs if run["top_decile"]["capture"] is not None
    ]
    capture = _ratio(decile_incidents, incidents) or 0.0

    thresholds = {}
    for tier in THRESHOLD_TIERS:
        flagged = sum(run["thresholds"][tier]["flagged"] for run in runs)
        caught = sum(run["thresholds"][tier]["incidents"] for run in runs)
        thresholds[tier] = _threshold(flagged, caught, incidents)

    return {
        "histories": len(runs),
        "merged_prs": merged,
        "incident_prs": incidents,
        "by_tier": by_tier,
        "overall_rate": overall_rate,
        "t0_rate": t0_rate,
        "top_decile": {
            "size": decile_size,
            "incidents": decile_incidents,
            "capture": capture,
            "lowest": min(captures, default=None),
            "highest": max(captures, default=None),
        },
        "histories_without_t0_incident": sum(
            1 for run in runs if run["bars"]["t0_has_no_incidents"]
        ),
        "thresholds": thresholds,
        "bars": {
            "t0_rate_at_most_a_quarter_of_overall": t0_rate <= T0_RATE_LIMIT * overall_rate,
            "top_decile_captures_majority_pooled": capture > TOP_DECILE_CAPTURE,
        },
    }


def calibrate_many(policy: Policy, histories: int = 30, now: datetime = GENERATED_NOW) -> dict:
    """Generates seeds 1..``histories``, each in its own in-memory database, and pools them."""
    runs = []
    for seed in range(1, histories + 1):
        engine = create_engine("sqlite://")
        try:
            Base.metadata.create_all(engine)
            with Session(engine) as db:
                synth.build(db, now=now, seed=seed)
                runs.append(calibrate(db, policy))
        finally:
            engine.dispose()
    return pool(runs)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def _verdict(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _tier_table(by_tier: dict) -> list[str]:
    lines = [f"{'Tier':<6}{'PRs':>8}{'Incidents':>11}{'Rate':>10}"]
    for tier, counts in by_tier.items():
        rate = _ratio(counts["incidents"], counts["prs"])
        lines.append(f"{tier:<6}{counts['prs']:>8}{counts['incidents']:>11}{_pct(rate):>10}")
    return lines


def _threshold_table(thresholds: dict) -> list[str]:
    lines = [f"{'Flag at':<9}{'Flagged':>9}{'Incidents':>11}{'Precision':>11}{'Recall':>9}"]
    for tier, row in thresholds.items():
        lines.append(
            f"{tier + '+':<9}{row['flagged']:>9}{row['incidents']:>11}"
            f"{_pct(row['precision']):>11}{_pct(row['recall']):>9}"
        )
    return lines


def format_report(report: dict) -> str:
    decile = report["top_decile"]
    bars = report["bars"]
    lines = [
        f"Merged PRs: {report['merged_prs']}   Incident PRs: {report['incident_prs']}",
        "",
        *_tier_table(report["by_tier"]),
        "",
        f"Top decile: {decile['size']} PRs hold {decile['incidents']} of "
        f"{report['incident_prs']} incident PRs ({_pct(decile['capture'])})",
        "",
        *_threshold_table(report["thresholds"]),
        "",
        f"Bar 1 (no incident PR in T0) ... {_verdict(bars['t0_has_no_incidents'])}",
        f"Bar 2 (top decile holds more than half of incident PRs) ... "
        f"{_verdict(bars['top_decile_captures_majority'])}",
    ]
    return "\n".join(lines)


def format_many(report: dict) -> str:
    decile = report["top_decile"]
    bars = report["bars"]
    lines = [
        f"Histories: {report['histories']}   Merged PRs: {report['merged_prs']}   "
        f"Incident PRs: {report['incident_prs']}",
        "",
        *_tier_table(report["by_tier"]),
        "",
        f"Overall incident rate: {_pct(report['overall_rate'])}   "
        f"T0 incident rate: {_pct(report['t0_rate'])}",
        f"Histories with no T0 incident: {report['histories_without_t0_incident']} of "
        f"{report['histories']}",
        f"Top decile: {decile['size']} PRs hold {decile['incidents']} of "
        f"{report['incident_prs']} incident PRs ({_pct(decile['capture'])}; single histories "
        f"{_pct(decile['lowest'])} to {_pct(decile['highest'])})",
        "",
        *_threshold_table(report["thresholds"]),
        "",
        f"Bar 1 (T0 rate at most {T0_RATE_LIMIT:.0%} of overall) ... "
        f"{_verdict(bars['t0_rate_at_most_a_quarter_of_overall'])}",
        f"Bar 2 (pooled top decile holds more than {TOP_DECILE_CAPTURE:.0%} of incident PRs) ... "
        f"{_verdict(bars['top_decile_captures_majority_pooled'])}",
    ]
    return "\n".join(lines)
