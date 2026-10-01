import { createRouter, createWebHistory } from "vue-router";

// Each page is its own chunk, so the Forecast page's chart library loads only there.
const HomeView = () => import("../views/HomeView.vue");
const LeadsView = () => import("../views/LeadsView.vue");
const AccountsView = () => import("../views/AccountsView.vue");
const AccountDetailView = () => import("../views/AccountDetailView.vue");
const PipelineView = () => import("../views/PipelineView.vue");
const ForecastView = () => import("../views/ForecastView.vue");
const AboutView = () => import("../views/AboutView.vue");

export const productName = "PragMattie Sync CRM";

export const navItems = [
  { title: "Home", to: "/", name: "home" },
  { title: "Leads", to: "/leads", name: "leads" },
  { title: "Accounts", to: "/accounts", name: "accounts" },
  { title: "Pipeline", to: "/pipeline", name: "pipeline" },
  { title: "Forecast", to: "/forecast", name: "forecast" },
];

export function pageTitle(route) {
  const page = route.meta?.title;
  return page ? `${page} · ${productName}` : productName;
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: HomeView, meta: { title: "Home" } },
    { path: "/leads", name: "leads", component: LeadsView, meta: { title: "Leads" } },
    { path: "/accounts", name: "accounts", component: AccountsView, meta: { title: "Accounts" } },
    {
      path: "/accounts/:id",
      name: "account",
      component: AccountDetailView,
      props: true,
      meta: { title: "Account" },
    },
    { path: "/pipeline", name: "pipeline", component: PipelineView, meta: { title: "Pipeline" } },
    { path: "/forecast", name: "forecast", component: ForecastView, meta: { title: "Forecast" } },
    { path: "/about", name: "about", component: AboutView, meta: { title: "About" } },
  ],
});

router.afterEach((to) => {
  document.title = pageTitle(to);
});
