import { createRouter, createWebHistory } from "vue-router";

const EngineeringSignalsView = () => import("../views/EngineeringSignalsView.vue");
const NotFoundView = () => import("../views/NotFoundView.vue");

export const appName = "Delivery Insights · PragMattie Sync";

export const navItems = [{ title: "Engineering signals", to: "/signals", name: "signals" }];

export function pageTitle(route) {
  const page = route.meta?.title;
  return page ? `${page} · ${appName}` : appName;
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/signals" },
    {
      path: "/signals",
      name: "signals",
      component: EngineeringSignalsView,
      meta: { title: "Engineering signals" },
    },
    {
      path: "/:pathMatch(.*)*",
      name: "not-found",
      component: NotFoundView,
      meta: { title: "Page not found" },
    },
  ],
});

router.afterEach((to) => {
  document.title = pageTitle(to);
});
