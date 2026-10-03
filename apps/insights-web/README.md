# apps/insights-web

The Delivery Insights web app: Vue 3, Vite, Vuetify, Pinia, Vue Router, Chart.js via vue-chartjs.
Development metrics for the PM and stakeholders — engineering signals, delivery forecast,
prediction accuracy, decision log, release status, delivery flow — served by `orchestrator/`.

A separate app with its own address. Always says what is simulated, and carries the "fictional
demo company, created by PragMattie Growth Partners" credit. No product screens belong here.

Agent-built; see `CLAUDE.md` at the repository root for the rules that govern it.

## Running it

With the whole stack (`docker compose up --build` from the repository root), the app is served on
<http://localhost:5174>. It reads from the orchestrator at `VITE_ORCH_URL`, which defaults to
<http://localhost:8001>.

To run it on its own, with Node 22:

```sh
npm install
npm run dev       # http://localhost:5174
```

## Tests and build

```sh
npm test          # Vitest, in jsdom
npm run build     # production bundle in dist/
npm run preview   # serves the built bundle
```

## Layout

- `src/api.js`: `buildUrl` and `getJson` for the orchestrator's API
- `src/router/index.js`: the routes, and `navItems`, the pages the navigation drawer lists
- `src/components/`: the app bar mark, the footer credit, and the shared `PageHeader`, `LoadError`
  (emits `retry`) and `LoadingBar`
- `src/views/`: one component per page

Nothing here is imported from `apps/crm-web`, and nothing links into the CRM.
