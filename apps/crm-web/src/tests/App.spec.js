import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App.vue";
import { vuetify } from "../plugins/vuetify";
import { navItems, router } from "../router";

async function mountApp(path = "/") {
  await router.push(path);
  await router.isReady();

  return mount(App, {
    global: {
      plugins: [createPinia(), router, vuetify],
    },
  });
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({}) }),
  );
});

describe("App", () => {
  it("shows the app bar title", async () => {
    const wrapper = await mountApp();

    expect(wrapper.text()).toContain("PragMattie Sync CRM");
  });

  it("navigates to the product's sections only", async () => {
    const wrapper = await mountApp();

    expect(navItems.map((item) => item.title)).toEqual([
      "Home",
      "Leads",
      "Accounts",
      "Pipeline",
      "Forecast",
    ]);
    for (const item of navItems) {
      expect(wrapper.text()).toContain(item.title);
    }
  });

  it("renders the home page by default", async () => {
    const wrapper = await mountApp();

    expect(wrapper.text()).toContain("Welcome to PragMattie Sync");
  });

  it("renders the forecast page", async () => {
    const wrapper = await mountApp("/forecast");

    expect(wrapper.find("h1").text()).toBe("Forecast");
    expect(wrapper.text()).toContain(
      "Where the quarter will land, based on deal stage and close date.",
    );
  });
});
