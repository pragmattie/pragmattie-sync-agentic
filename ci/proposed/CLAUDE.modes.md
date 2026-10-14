# Proposed: the "Modes" paragraph of `CLAUDE.md` (rebuild item 7.1)

`CLAUDE.md` can't be edited by an agent. A person replaces the "### Modes" subsection of its
"## Governance" section with the text below. ADR 0006 can't be edited either; the new mode is
recorded in [ADR 0010](../../docs/adr/0010-observe-mode.md).

---

### Modes

- `ORCHESTRATOR_MODE` is `off`, `observe`, `shadow` or `enforce`, one switch for every agent;
  shadow comes before enforce ([ADR 0006](docs/adr/0006-polling-with-off-shadow-enforce.md)).
- `observe` decides and records exactly as shadow but writes nothing to GitHub
  ([ADR 0010](docs/adr/0010-observe-mode.md)). Beside v1, v2's poll loop (the `agents` service)
  runs in observe until cutover (7.5); v1 keeps governing this repository until then.
- Don't switch v2 to shadow or enforce on this repository in a change.
- The one live exception is the board sync, a scheduled workflow that needs no database
  ([ADR 0008](docs/adr/0008-board-computed-from-github.md)).
