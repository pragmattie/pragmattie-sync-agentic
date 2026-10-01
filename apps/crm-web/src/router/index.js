import { createRouter, createWebHistory } from "vue-router";
import AccountDetailView from "../views/AccountDetailView.vue";
import AccountsView from "../views/AccountsView.vue";
import HomeView from "../views/HomeView.vue";
import LeadsView from "../views/LeadsView.vue";
import PlaceholderView from "../views/PlaceholderView.vue";

export const navItems = [
  { title: "Home", to: "/", name: "home" },
  { title: "Leads", to: "/leads", name: "leads" },
  { title: "Accounts", to: "/accounts", name: "accounts" },
  { title: "Pipeline", to: "/pipeline", name: "pipeline" },
  { title: "Forecast", to: "/forecast", name: "forecast" },
];

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: HomeView },
    { path: "/leads", name: "leads", component: LeadsView },
    { path: "/accounts", name: "accounts", component: AccountsView },
    {
      path: "/accounts/:id",
      name: "account",
      component: AccountDetailView,
      props: true,
    },
    {
      path: "/pipeline",
      name: "pipeline",
      component: PlaceholderView,
      props: { title: "Pipeline" },
    },
    {
      path: "/forecast",
      name: "forecast",
      component: PlaceholderView,
      props: { title: "Forecast" },
    },
  ],
});
