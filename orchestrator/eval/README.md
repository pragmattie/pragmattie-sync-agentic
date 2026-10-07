# Triage evaluation sets

The ground truth for `python -m sdlc.eval`: a person's own module, type and points (and priority,
not gated) for issues the triage agent classifies, labelled blind, before any agent saw the
answers. Added by a person, not an agent, from the first version of this system (v1).

| Set | Files | Issues | Labelled | Source of trial rows |
| --- | --- | --- | --- | --- |
| `backlog` | `triage_eval_set.json`, `_issues.json` | 40, numbered 8006–8045 | by Geoff, blind, 2026-09-23 | simulated |
| `holdout` | `triage_holdout_set.json`, `holdout_issues.json` | 20 invented, numbered 9001–9020 | by Geoff, blind, 2026-09-24 | simulated |

**Numbering.** The backlog issues are v1's real backlog issues #6–#45, renumbered by adding 8000
(v1's #6 is 8006), because #6–#45 in this repository are different issues. Their text is as v1's
triage agent saw it. The holdout issues were invented for the evaluation and exist nowhere else.

**Both sets are spent for tuning.** The backlog set was used to tune v1's prompt (`triage-v2`), and
the holdout set graded `triage-v3`. Neither can judge a later prompt fairly: a prompt change needs a
fresh holdout set, labelled blind before any agent sees it. Here they serve as regression checks
and for the demo, always graded fresh (`--yes` to spend; about 60 Haiku calls for both).

**Bars** (fixed before the first run): module 85%, type 90%, points within one step 70%.

**v1's recorded results**, for comparison:

| Prompt | Set | Module | Type | Points within one step |
| --- | --- | --- | --- | --- |
| `triage-v1` | backlog | 88% pass | 85% fail | 82% pass |
| `triage-v2` | backlog | 90% pass | 82% fail | 82% pass |
| `triage-v3` | holdout | 90% pass | 75% fail | 80% pass |

Type has failed its bar on every run. Geoff decided (2026-09-23, again 2026-09-24) to accept and
record that, not to lower the bar or change the labels: the misses follow a gap between the written
definition of a chore and the labels, and type gates nothing.

**Format** (what `sdlc/eval.py` reads):
- a labels file is a JSON list of `{number, module, type, points, priority?, note?}`;
- an issue-text file is a JSON object mapping each number to `{title, body}`.
