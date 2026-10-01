import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App.vue";
import { vuetify } from "../plugins/vuetify";
import { navItems, pageTitle, router } from "../router";

async function mountApp(path = "/") {
  await router.push(path);
  await router.isReady();

  return mount(App, {
    global: {
      plugins: [createPinia(), router, vuetify],
    },
  });
}

enableAutoUnmount(afterEach);

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({}) }),
  );
});

describe("App", () => {
  it("shows the logo and CRM in the app bar", async () => {
    const wrapper = await mountApp();

    const bar = wrapper.find("header");
    expect(bar.find("svg[aria-label='PragMattie Sync']").exists()).toBe(true);
    expect(bar.find("[data-test='app-bar-product']").text()).toBe("CRM");
  });

  it("shows the footer with the current year and an About link", async () => {
    const wrapper = await mountApp("/about");

    expect(wrapper.find("[data-test='copyright']").text()).toBe(
      `© ${new Date().getFullYear()} PragMattie Sync`,
    );
    expect(wrapper.find("[data-test='about-link']").attributes("href")).toBe("/about");
  });

  it("renders the About page", async () => {
    const wrapper = await mountApp("/about");

    expect(wrapper.find("h1").text()).toBe("PragMattie Sync CRM");
    expect(wrapper.find("[data-test='version']").exists()).toBe(true);
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

describe("App tab titles", () => {
  it.each([
    ["/", "Home · PragMattie Sync CRM"],
    ["/leads", "Leads · PragMattie Sync CRM"],
    ["/accounts", "Accounts · PragMattie Sync CRM"],
    ["/accounts/1", "Account · PragMattie Sync CRM"],
    ["/pipeline", "Pipeline · PragMattie Sync CRM"],
    ["/forecast", "Forecast · PragMattie Sync CRM"],
    ["/about", "About · PragMattie Sync CRM"],
  ])("titles %s as %s", (path, title) => {
    expect(pageTitle(router.resolve(path))).toBe(title);
  });

  it("sets the browser tab title on navigation", async () => {
    await mountApp("/about");

    expect(document.title).toBe("About · PragMattie Sync CRM");
  });

  it("falls back to the product name for a route without a page name", () => {
    expect(pageTitle({ meta: {} })).toBe("PragMattie Sync CRM");
  });
});

describe("App errors", () => {
  it("tells a network failure apart from an API error on a page", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const offline = await mountApp("/forecast");
    await flushPromises();
    const network = offline.find("[data-test='load-error']").text();

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ detail: "Forecast is being recalculated" }),
      }),
    );
    await router.push("/");
    const failing = await mountApp("/forecast");
    await flushPromises();
    const api = failing.find("[data-test='load-error']").text();

    expect(network).toContain("Couldn't load the forecast");
    expect(network).toContain("Can't reach the PragMattie Sync server");
    expect(network).not.toContain("Failed to fetch");
    expect(api).toContain("Forecast is being recalculated");
    expect(api).not.toContain("Can't reach");
  });
});
