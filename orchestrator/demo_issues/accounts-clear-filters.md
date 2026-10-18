# Accounts: a "Clear filters" button when no accounts match

## Goal

When a search or filter on the Accounts page finds nothing, the table says "No accounts match these filters" and gives the user no quick way back. Add a "Clear filters" button to that message that puts the search and filters back to their defaults.

## Spec

In apps/crm-web/src/views/AccountsView.vue:

- Replace the table's no-data-text with a no-data slot showing the same text, "No accounts match these filters", and below it a text button "Clear filters" (data-test="clear-filters").
- Clicking it sets: the search box empty (searchInput and search both ""), industry null, owner null.
- The table reloads once, on page 1, as any other filter change does.
- The button appears only in the no-results message, never above the table.

## Acceptance criteria

- With a search that matches nothing, the message and the "Clear filters" button both show.
- Clicking the button empties the search, clears industry and owner, and the accounts load again.
- The existing test "says no accounts match the filters when there are none" still passes.
- src/tests/AccountsView.spec.js covers the button and the reset.
- npm test and npm run build pass in apps/crm-web.

## Not in scope

Other pages, the API, and the filter controls themselves.

## Part

PragMattie Sync CRM
