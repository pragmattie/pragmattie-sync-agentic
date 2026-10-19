# About page: show build {n}

## Goal

The About page shows the product's version but not which build is running, so support can't tell two installs of the same version apart. Show the build number under the version. This is build {n}.

## Spec

In apps/crm-web/src/views/AboutView.vue:

- Below the version line, show one line reading "Build {n}" (data-test="build-number"), in the same style as the version line.
- If that line is already there, change only its number to {n}. There is only ever one such line.
- Nothing else on the page changes.

## Acceptance criteria

- The About page shows "Build {n}" exactly once, below the version.
- src/tests/AboutView.spec.js asserts that the build-number text is "Build {n}" (updating the existing assertion if there is one).
- npm test and npm run build pass in apps/crm-web, including the build check.

## Not in scope

The rest of the About page, other pages and the API.

## Part

PragMattie Sync CRM
