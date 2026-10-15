# Proposed: the "### Modes" subsection of CLAUDE.md after cutover (7.6)

For Geoff to apply at step 7 of [`docs/cutover.md`](../../docs/cutover.md), once v2 governs this
repository. It replaces the whole "### Modes" subsection of `CLAUDE.md`'s "## Governance" section
(as applied from `CLAUDE.modes.md` in 7.1); nothing else in `CLAUDE.md` changes. How to run things
is in the README ("Before cutover (beside v1)" and "After cutover"), so no section is added for it.

What changes: v2's poll loop now runs in `enforce` on this repository and v1 is archived, so the
"beside v1" bullet becomes a cutover bullet, and "Don't switch v2 to shadow or enforce" becomes
"don't change the mode in a change".

The replacement text, between the lines:

---

### Modes

- `ORCHESTRATOR_MODE` is `off`, `observe`, `shadow` or `enforce`, one switch for every agent;
  shadow comes before enforce ([ADR 0006](docs/adr/0006-polling-with-off-shadow-enforce.md)).
- `observe` decides and records exactly as shadow but writes nothing to GitHub
  ([ADR 0010](docs/adr/0010-observe-mode.md)).
- Since cutover (7.6, [`docs/cutover.md`](docs/cutover.md)), v2 governs this repository: its poll
  loop (the `agents` service) runs in `enforce` with its own write token, and v1
  (`geoffsmattie/pragmattie-sync`) is archived read-only. The rollback is the runbook's step 8.
- Don't change the mode on this repository in a change; a person sets it in `.env`.
- The one live exception is the board sync, a scheduled workflow that needs no database
  ([ADR 0008](docs/adr/0008-board-computed-from-github.md)).

---
