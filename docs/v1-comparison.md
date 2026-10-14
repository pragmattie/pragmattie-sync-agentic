# Comparing v2 with v1 before cutover

While v2 observes this repository (7.1), v1 and v2 both score the same PRs. `sdlc.compare_v1`
compares their decisions so the cutover decision (7.5) can be made from a report rather than an
impression.

## Running it

1. Export v1's decisions about this repository, on v1's side:
   `python -m sdlc.export_rebuild --out v1-export.json`. This repository never reads v1's code or
   database; the file is the only thing it sees.
2. Compare, in the orchestrator (with `GITHUB_REPO` set to this repository):
   `python -m sdlc.compare_v1 v1-export.json [--since 2026-10-01] [--markdown report.md]`.

The export is refused if it is for another repository ("This export is for {x}, not {y}.") or a
decision lacks a field. The comparison is read-only: it writes nothing to the database or GitHub.

## What it compares

v1's rows from the export are paired with v2's own rows (source `github`, trial rows left out) from
the day v2 started observing (its first `pr_risk` row) or `--since`:

- **PR risk**, per PR and commit: each side's newest ok row. Tiers equal count as agreement; a
  difference shows both scores and the signals and inputs that differ, since each side scores from
  its own history.
- **Test selector**, per PR and commit: the newest recommendation's suites, with both sides'
  reasons. Where both sides settled a commit's CI, the results must match; a mismatch is a bug.
- **Triage**, per issue and content hash: module, type, priority and points, each counted alone.
- **Release gate**, per deploy commit: each side's final verdict and tier.

Rows only one side has are counted, with the reason when it is known (v2 wasn't observing yet, or a
failed run). `/tier` rulings and objection-window sign-offs aren't compared: v2 posts nothing
while it observes.

## The bar for cutover

Fixed on 2026-10-14, before any comparison was run, so it can't be tuned to the result:

- tiers agree on at least 90% of commits both scored;
- no commit where v2's tier is lower than v1's on a PR v1 floored (v1's row lists a floor in
  `output["floors"]`, or, when it lists none, v2's row for the same commit does);
- release verdicts agree on every deploy both judged.

The report shows it as met or not met. It is an aid: the decision is made at 7.5.
