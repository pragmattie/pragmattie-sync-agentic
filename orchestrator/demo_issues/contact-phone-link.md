# Account page: a contact's phone number can be tapped to call

## Goal

On an account's page, each contact's email is a link but their phone number is plain text, so on a phone a rep has to copy it to call. Make the phone number a call link, like the email.

## Spec

In apps/crm-web/src/views/AccountDetailView.vue, the Contacts card's list:

- When a contact has a phone number, show it as a link whose address is "tel:" followed by the number with spaces removed (for example "tel:+15125550142"), with the number as written as its text, and data-test="contact-phone".
- Keep the " · " separator between the email and the phone, and the same muted styling around it.
- A contact without a phone shows no phone and no link, as today.
- Nothing else on the page changes.

## Acceptance criteria

- A contact with phone "+1 512 555 0142" shows a link with text "+1 512 555 0142" and address "tel:+15125550142".
- A contact without a phone shows no contact-phone element.
- The email link is unchanged.
- src/tests/AccountDetailView.spec.js covers both cases.
- npm test and npm run build pass in apps/crm-web.

## Not in scope

Other pages, the API, and how phone numbers are stored.

## Part

PragMattie Sync CRM
