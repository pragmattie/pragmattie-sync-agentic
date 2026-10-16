import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { vuetify } from "../plugins/vuetify";
import HomeView from "../views/HomeView.vue";

vi.mock("../api", () => ({ getJson: vi.fn() }));

const { getJson } = await import("../api");

const summary = {
  quarter: "2026-Q4",
  leads_by_status: { new: 900, working: 334 },
  open_leads: 1234,
  open_deals: 56,
  open_pipeline: "12500000.00",
  won_this_quarter: "1234500.00",
};

async function mountHome() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: ["/", "/leads", "/pipeline", "/forecast"].map((path) => ({
      path,
      component: { template: "<div />" },
    })),
  });
  await router.push("/");
  await router.isReady();

  const wrapper = mount(HomeView, { global: { plugins: [router, vuetify] } });
  await flushPromises();
  return wrapper;
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("HomeView", () => {
  it("shows the welcome heading and what the CRM is for", async () => {
    getJson.mockResolvedValue(summary);
    const wrapper = await mountHome();

    expect(wrapper.find("h1").text()).toBe("Welcome to PragMattie Sync");
    expect(wrapper.text()).toContain("A CRM for mid-market B2B sales teams.");
  });

  it("shows the four KPI tiles in order with formatted values and links", async () => {
    getJson.mockResolvedValue(summary);
    const wrapper = await mountHome();

    expect(getJson).toHaveBeenCalledWith("/api/v1/summary");
    const tiles = wrapper.findAll("[data-test^='tile-']");
    const shown = tiles.map((tile) => ({
      text: tile.text(),
      href: tile.attributes("href"),
    }));
    expect(shown).toEqual([
      { text: "Open leads1,234View leads", href: "/leads" },
      { text: "Open deals56View pipeline", href: "/pipeline" },
      { text: "Open pipeline$12.5MView pipeline", href: "/pipeline" },
      { text: "Won in 2026-Q4$1.2MView forecast", href: "/forecast" },
    ]);
    expect(wrapper.find("[data-test='load-error']").exists()).toBe(false);
  });

  it("shows each tile can be opened, with a chevron and a hover lift", async () => {
    getJson.mockResolvedValue(summary);
    const wrapper = await mountHome();

    const tiles = wrapper.findAll("[data-test^='tile-']");
    const views = tiles.map((tile) => tile.find("[data-test='view-line']"));
    expect(views.map((view) => view.text())).toEqual([
      "View leads",
      "View pipeline",
      "View pipeline",
      "View forecast",
    ]);
    for (const view of views) {
      expect(view.classes()).toEqual(expect.arrayContaining(["text-secondary", "text-body-2"]));
      expect(view.find(".mdi-chevron-right").exists()).toBe(true);
    }
    for (const tile of tiles) {
      expect(tile.element.tagName).toBe("A");
      expect(tile.classes()).toContain("v-card--hover");
    }
  });

  it("gives each tile an accessible name with its label, value and destination", async () => {
    getJson.mockResolvedValue(summary);
    const wrapper = await mountHome();

    const names = wrapper
      .findAll("[data-test^='tile-']")
      .map((tile) => tile.attributes("aria-label"));
    expect(names).toEqual([
      "Open leads: 1,234. View leads",
      "Open deals: 56. View pipeline",
      "Open pipeline: $12.5M. View pipeline",
      "Won in 2026-Q4: $1.2M. View forecast",
    ]);
  });

  it("lays the tiles out four across on desktop, two on tablets and one on phones", async () => {
    getJson.mockResolvedValue(summary);
    const wrapper = await mountHome();

    for (const tile of wrapper.findAll("[data-test^='tile-']")) {
      const column = tile.element.parentElement.classList;
      expect([...column]).toEqual(
        expect.arrayContaining(["v-col--cols-12", "v-col--cols-sm-6", "v-col--cols-md-3"]),
      );
    }
  });

  it("shows a progress bar and no tiles while the summary loads", async () => {
    let resolve;
    getJson.mockReturnValue(new Promise((done) => (resolve = done)));
    const wrapper = await mountHome();

    expect(wrapper.find("[data-test='loading-bar']").exists()).toBe(true);
    expect(wrapper.findAll("[data-test^='tile-']")).toHaveLength(0);

    resolve(summary);
    await flushPromises();
    expect(wrapper.find("[data-test='loading-bar']").exists()).toBe(false);
    expect(wrapper.findAll("[data-test^='tile-']")).toHaveLength(4);
  });

  it("shows zeros, not a blank area, when there is nothing yet", async () => {
    getJson.mockResolvedValue({
      ...summary,
      open_leads: 0,
      open_deals: 0,
      open_pipeline: "0.00",
      won_this_quarter: "0.00",
    });
    const wrapper = await mountHome();

    const values = wrapper.findAll("[data-test^='tile-'] .text-h4").map((value) => value.text());
    expect(values).toEqual(["0", "0", "$0", "$0"]);
  });

  it("shows an error alert, still under the heading, when the summary fails to load", async () => {
    getJson.mockRejectedValue(new Error("Service unavailable"));
    const wrapper = await mountHome();

    const alert = wrapper.find("[data-test='load-error']");
    expect(alert.classes()).toContain("text-error");
    expect(alert.text()).toContain("Couldn't load the sales summary");
    expect(alert.text()).toContain("Service unavailable");
    expect(wrapper.find("h1").text()).toBe("Welcome to PragMattie Sync");
    expect(wrapper.findAll("[data-test^='tile-']")).toHaveLength(0);
    expect(wrapper.find("[data-test='loading-bar']").exists()).toBe(false);
  });

  it("never mentions development information", async () => {
    getJson.mockResolvedValue(summary);
    const ok = (await mountHome()).text();
    getJson.mockRejectedValue(new Error("Service unavailable"));
    const failed = (await mountHome()).text();

    for (const text of [ok, failed]) {
      expect(text).not.toMatch(/phase|health|build|agent|demo|simulated|roadmap|status/i);
    }
  });
});
