# About page: show demo rehearsal {n}

## Goal

A practice change for demo rehearsals: the About page shows the number of the latest rehearsal that reached the product. This is rehearsal {n}.

## Spec

In apps/crm-web/src/views/AboutView.vue:

- Below the version line, show one line reading "Demo rehearsal {n}" (data-test="rehearsal-note"), in the same small muted style as the version line.
- If that line is already there, change only its number to {n}. There is only ever one such line.
- Nothing else on the page changes.

## Acceptance criteria

- The About page shows "Demo rehearsal {n}" exactly once.
- src/tests/AboutView.spec.js asserts that the rehearsal-note text is "Demo rehearsal {n}" (updating the existing assertion if there is one).
- npm test and npm run build pass in apps/crm-web.

## Not in scope

The rest of the About page, other pages and the API.

## Part

PragMattie Sync CRM
