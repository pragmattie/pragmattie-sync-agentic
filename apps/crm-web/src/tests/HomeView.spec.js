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
      { text: "Open leads1,234", href: "/leads" },
      { text: "Open deals56", href: "/pipeline" },
      { text: "Open pipeline$12.5M", href: "/pipeline" },
      { text: "Won in 2026-Q4$1.2M", href: "/forecast" },
    ]);
    expect(wrapper.find("[data-test='summary-error']").exists()).toBe(false);
  });

  it("shows a warning and no tiles when the summary fails to load", async () => {
    getJson.mockRejectedValue(new Error("Service unavailable"));
    const wrapper = await mountHome();

    expect(wrapper.find("[data-test='summary-error']").text()).toContain(
      "Couldn't load the sales summary: Service unavailable",
    );
    expect(wrapper.findAll("[data-test^='tile-']")).toHaveLength(0);
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
