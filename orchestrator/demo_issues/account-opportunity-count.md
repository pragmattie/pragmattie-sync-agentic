# Account page: the Opportunities card shows how many deals there are

## Goal

On an account's page, the Opportunities card is titled just "Opportunities", so a rep has to count the rows to see how many deals are open or closed with the account. Show the count in the title.

## Spec

In apps/crm-web/src/views/AccountDetailView.vue, the Opportunities card (the card whose title is "Opportunities"):

- The title reads "Opportunities · N", where N is the number of opportunities shown for the account, for example "Opportunities · 4".
- With no opportunities, the title stays "Opportunities" (no "· 0"), and the table's "No opportunities yet" text is unchanged.
- The count is in its own element (data-test="opportunity-count") so it can be tested.
- Nothing else on the page changes.

## Acceptance criteria

- An account with 4 opportunities shows "Opportunities · 4".
- An account with 1 opportunity shows "Opportunities · 1".
- An account with none shows "Opportunities" and "No opportunities yet", and no opportunity-count element.
- src/tests/AccountDetailView.spec.js covers the three cases.
- npm test and npm run build pass in apps/crm-web.

## Not in scope

The Contacts card, other pages and the API.

## Part

PragMattie Sync CRM
