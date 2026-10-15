# Proposed CLAUDE.md wording: the objection window covers every T1 PR (7.9)

Apply together with `ci/proposed/tiers.yaml`, which sets the window to `minutes: 15` and
`paths: ["*"]`. Two sentences change; nothing else in `CLAUDE.md` does.

## "How you work", the governance bullet

Replace:

> A T1 pull request that changes only the web apps (`apps/crm-web/`, `apps/insights-web/`) and
> that the reviewer approved merges itself an hour later, unless a person comments `/hold`.

with:

> Any T1 pull request the reviewer approved merges itself 15 minutes later, unless a person
> comments `/hold`.

## "Overrides and `/hold`"

Replace:

> - `/hold` stops the objection window: a front-end-only T1 PR the reviewer approved otherwise
>   counts as signed off an hour later.

with:

> - `/hold` stops the objection window: a T1 PR the reviewer approved otherwise counts as signed
>   off 15 minutes later.
