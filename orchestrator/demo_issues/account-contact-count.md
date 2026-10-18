# Account page: the Contacts card shows how many contacts there are

## Goal

On an account's page, the Contacts card is titled just "Contacts", so a rep has to scroll the list to see how many people they know at the account. Show the count in the title.

## Spec

In apps/crm-web/src/views/AccountDetailView.vue, the Contacts card (data-test="contacts"):

- The title reads "Contacts · N", where N is the number of contacts on the account (account.contacts.length), for example "Contacts · 3".
- With no contacts, the title stays "Contacts" (no "· 0"), and the "No contacts yet" text below it is unchanged.
- The count is in its own element (data-test="contact-count") so it can be tested.
- Nothing else on the page changes.

## Acceptance criteria

- An account with 3 contacts shows "Contacts · 3".
- An account with 1 contact shows "Contacts · 1".
- An account with no contacts shows "Contacts" and "No contacts yet", and no contact-count element.
- src/tests/AccountDetailView.spec.js covers the three cases.
- npm test and npm run build pass in apps/crm-web.

## Not in scope

The Opportunities card, other pages and the API.

## Part

PragMattie Sync CRM
