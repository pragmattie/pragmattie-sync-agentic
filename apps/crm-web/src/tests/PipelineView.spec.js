import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { getJson, sendJson } from "../api";
import { vuetify } from "../plugins/vuetify";
import { useSnackbarStore } from "../stores/snackbar";
import PipelineView from "../views/PipelineView.vue";
import pipelineSource from "../views/PipelineView.vue?raw";

vi.mock("../api", () => ({
  getJson: vi.fn(),
  sendJson: vi.fn(),
}));

const OPEN = ["prospecting", "qualification", "proposal", "negotiation"];

const reps = [
  { id: 1, name: "Avery Lee" },
  { id: 2, name: "Jordan Park" },
];

function deal(overrides = {}) {
  return {
    id: 1,
    account_id: 7,
    account: { id: 7, name: "Northwind Example" },
    name: "Northwind renewal",
    amount: "10000.00",
    stage: "prospecting",
    probability: 10,
    close_date: "2026-11-15",
    owner_id: 1,
    owner: { id: 1, name: "Avery Lee" },
    created_at: "2026-08-01T09:00:00",
    ...overrides,
  };
}

let deals;
let wrapper;
let pinia;

function dealCalls() {
  return getJson.mock.calls.filter(([path]) => path === "/api/v1/opportunities");
}

function lastParams() {
  return dealCalls().at(-1)[1];
}

function column(stage) {
  return wrapper.find(`[data-test="column-${stage}"]`);
}

function body(selector) {
  return document.body.querySelector(selector);
}

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/pipeline", component: PipelineView },
      { path: "/accounts/:id", component: { template: "<div />" } },
    ],
  });
  await router.push("/pipeline");
  await router.isReady();
  pinia = createPinia();
  wrapper = mount(PipelineView, {
    attachTo: document.body,
    global: { plugins: [pinia, router, vuetify] },
  });
  await flushPromises();
  return wrapper;
}

function selects() {
  return wrapper.findAllComponents({ name: "VSelect" });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 1, 12));
  deals = [deal()];
  getJson.mockImplementation((path) => {
    if (path === "/api/v1/reps") return Promise.resolve(reps);
    return Promise.resolve({ items: deals, total: deals.length });
  });
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.clearAllMocks();
  vi.useRealTimers();
  document.body.innerHTML = "";
});

describe("PipelineView", () => {
  it("shows the title and subtitle", async () => {
    await mountView();

    expect(wrapper.text()).toContain("Pipeline");
    expect(wrapper.text()).toContain(
      "Open opportunities by stage. Use a card's menu to move it forward.",
    );
  });

  it("offers this quarter, next quarter and all open deals", async () => {
    await mountView();

    const [windowSelect] = selects();
    expect(windowSelect.props("items").map((item) => item.title)).toEqual([
      "This quarter (2026-Q4)",
      "Next quarter (2027-Q1)",
      "All open deals",
    ]);
  });

  it("loads this quarter's open deals by default", async () => {
    await mountView();

    expect(dealCalls()).toHaveLength(1);
    expect(lastParams()).toEqual({
      stage: OPEN,
      owner_id: null,
      close_from: "2026-10-01",
      close_to: "2026-12-31",
      limit: 500,
    });
  });

  it("loads next quarter and all open deals", async () => {
    await mountView();
    const [windowSelect] = selects();

    windowSelect.vm.$emit("update:modelValue", "next");
    await flushPromises();
    expect(lastParams()).toMatchObject({ close_from: "2027-01-01", close_to: "2027-03-31" });

    windowSelect.vm.$emit("update:modelValue", "all");
    await flushPromises();
    expect(lastParams()).toEqual({
      stage: OPEN,
      owner_id: null,
      close_from: undefined,
      close_to: undefined,
      limit: 500,
    });
  });

  it("filters by owner and clears it", async () => {
    await mountView();
    const [, ownerSelect] = selects();
    expect(ownerSelect.props("items")).toEqual([
      { value: 1, title: "Avery Lee" },
      { value: 2, title: "Jordan Park" },
    ]);

    ownerSelect.vm.$emit("update:modelValue", 2);
    await flushPromises();
    expect(lastParams()).toMatchObject({ owner_id: 2, close_from: "2026-10-01" });

    ownerSelect.vm.$emit("update:modelValue", null);
    await flushPromises();
    expect(lastParams().owner_id).toBeNull();
  });

  it("groups deals into a column per open stage with counts and totals", async () => {
    deals = [
      deal({ id: 1, stage: "prospecting", amount: "10000.00" }),
      deal({ id: 2, stage: "prospecting", amount: "15000.00" }),
      deal({ id: 3, stage: "proposal", amount: "1200000.00", probability: 50 }),
      deal({ id: 4, stage: "negotiation", amount: "80000.00", probability: 75 }),
    ];
    await mountView();

    for (const stage of OPEN) expect(column(stage).exists()).toBe(true);
    expect(wrapper.find('[data-test="column-closed_won"]').exists()).toBe(false);

    expect(column("prospecting").findAll('[data-test="deal"]')).toHaveLength(2);
    expect(column("prospecting").find('[data-test="column-count"]').text()).toBe("(2)");
    expect(column("prospecting").find('[data-test="column-total"]').text()).toBe("$25K");
    expect(column("prospecting").text()).toContain("10% win probability");

    expect(column("qualification").findAll('[data-test="deal"]')).toHaveLength(0);
    expect(column("qualification").text()).toContain("25% win probability");

    expect(column("proposal").find('[data-test="column-total"]').text()).toBe("$1.2M");
    expect(column("proposal").text()).toContain("50% win probability");
    expect(column("negotiation").find('[data-test="column-count"]').text()).toBe("(1)");
    expect(column("negotiation").text()).toContain("75% win probability");
  });

  it("shows the columns in stage order", async () => {
    await mountView();

    const order = wrapper
      .findAll('[data-test^="column-"]')
      .map((el) => el.attributes("data-test"))
      .filter((name) => OPEN.some((stage) => name === `column-${stage}`));
    expect(order).toEqual(OPEN.map((stage) => `column-${stage}`));
  });

  it("scrolls the board sideways with columns at least 240px wide", async () => {
    await mountView();

    // jsdom applies no stylesheets, so check the classes and the rules they carry.
    expect(wrapper.find(".board").exists()).toBe(true);
    for (const stage of OPEN) expect(column(stage).classes()).toContain("column");
    const style = pipelineSource.slice(pipelineSource.indexOf("<style"));
    expect(style).toMatch(/\.board\s*\{[^}]*overflow-x:\s*auto/);
    expect(style).toMatch(/\.column\s*\{[^}]*min-width:\s*240px/);
  });

  it("shows No deals in an empty column", async () => {
    deals = [deal({ stage: "proposal", probability: 50 })];
    await mountView();

    expect(column("prospecting").find('[data-test="column-empty"]').text()).toBe("No deals");
    expect(column("proposal").find('[data-test="column-empty"]').exists()).toBe(false);
  });

  it("sums the summary in full dollars, weighting each deal by its probability", async () => {
    deals = [
      deal({ id: 1, stage: "prospecting", amount: "10000.00", probability: 10 }),
      deal({ id: 2, stage: "proposal", amount: "200000.00", probability: 50 }),
      deal({ id: 3, stage: "negotiation", amount: "80000.50", probability: 80 }),
    ];
    await mountView();

    expect(wrapper.find('[data-test="summary-open"]').text()).toBe("$290,001");
    // 1,000 + 100,000 + 64,000.40
    expect(wrapper.find('[data-test="summary-weighted"]').text()).toBe("$165,000");
    expect(wrapper.find('[data-test="summary-count"]').text()).toBe("3");
  });

  it("shows each card's details and links it to its account", async () => {
    deals = [
      deal({ id: 1, name: "Northwind renewal", amount: "123456.00", account_id: 42 }),
      deal({ id: 2, name: "Contoso expansion", owner_id: null, owner: null }),
    ];
    await mountView();

    const [first, second] = wrapper.findAll('[data-test="deal"]');
    const link = first.find('[data-test="deal-name"]');
    expect(link.text()).toBe("Northwind renewal");
    expect(link.attributes("href")).toBe("/accounts/42");
    expect(first.find('[data-test="deal-amount"]').text()).toBe("$123,456");
    expect(first.text()).toContain("Nov 15, 2026");
    expect(first.text()).toContain("Avery Lee");
    expect(second.text()).toContain("Unassigned");
  });

  it("offers every other stage in the card's menu", async () => {
    deals = [deal({ stage: "qualification", probability: 25 })];
    await mountView();

    wrapper.find('[data-test="deal-actions"]').trigger("click");
    await flushPromises();

    const moves = [...document.body.querySelectorAll('[data-test^="move-"]')].map((el) =>
      el.textContent.trim(),
    );
    expect(moves).toEqual([
      "Move to Prospecting",
      "Move to Proposal",
      "Move to Negotiation",
      "Move to Closed won",
      "Move to Closed lost",
    ]);
  });

  it("moves a deal, confirms and reloads", async () => {
    deals = [deal({ id: 9, name: "Northwind renewal", stage: "negotiation", probability: 75 })];
    sendJson.mockImplementation(() => {
      deals = [];
      return Promise.resolve(deal({ id: 9, stage: "closed_won", probability: 100 }));
    });
    await mountView();

    wrapper.find('[data-test="deal-actions"]').trigger("click");
    await flushPromises();
    body('[data-test="move-closed_won"]').click();
    await flushPromises();

    expect(sendJson).toHaveBeenCalledWith("PATCH", "/api/v1/opportunities/9", {
      stage: "closed_won",
    });
    const snackbar = useSnackbarStore(pinia);
    expect(snackbar.show).toBe(true);
    expect(snackbar.text).toBe("Northwind renewal moved to Closed won");
    expect(snackbar.color).toBe("primary");
    expect(dealCalls()).toHaveLength(2);
    expect(wrapper.findAll('[data-test="deal"]')).toHaveLength(0);
    expect(wrapper.find('[data-test="summary-count"]').text()).toBe("0");
  });

  it("shows an error when a move fails", async () => {
    sendJson.mockRejectedValue(new Error("Stage change not allowed"));
    await mountView();

    wrapper.find('[data-test="deal-actions"]').trigger("click");
    await flushPromises();
    body('[data-test="move-proposal"]').click();
    await flushPromises();

    const snackbar = useSnackbarStore(pinia);
    expect(snackbar.text).toBe("Stage change not allowed");
    expect(snackbar.color).toBe("error");
    expect(dealCalls()).toHaveLength(1);
  });

  it("shows a progress bar and no summary or board while deals load", async () => {
    let resolve;
    getJson.mockImplementation((path) => {
      if (path === "/api/v1/reps") return Promise.resolve(reps);
      return new Promise((done) => (resolve = done));
    });
    await mountView();

    expect(wrapper.find('[data-test="loading-bar"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="summary"]').exists()).toBe(false);
    expect(column("prospecting").exists()).toBe(false);

    resolve({ items: deals, total: deals.length });
    await flushPromises();
    expect(wrapper.find('[data-test="loading-bar"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="summary-count"]').text()).toBe("1");
  });

  it("clears the last window's deals as soon as another window starts loading", async () => {
    await mountView();
    expect(wrapper.findAll('[data-test="deal"]')).toHaveLength(1);

    getJson.mockImplementation(() => new Promise(() => {}));
    selects()[0].vm.$emit("update:modelValue", "next");
    await flushPromises();

    expect(wrapper.find('[data-test="loading-bar"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="summary"]').exists()).toBe(false);
    expect(wrapper.findAll('[data-test="deal"]')).toHaveLength(0);
  });

  it("says No deals in every column when there are none", async () => {
    deals = [];
    await mountView();

    for (const stage of OPEN) {
      expect(column(stage).find('[data-test="column-empty"]').text()).toBe("No deals");
    }
    expect(wrapper.find('[data-test="summary-count"]').text()).toBe("0");
  });

  it("shows an API error alert in place of the board, keeping the header and filters", async () => {
    getJson.mockImplementation((path) => {
      if (path === "/api/v1/reps") return Promise.resolve(reps);
      return Promise.reject(new Error("Service unavailable"));
    });
    await mountView();

    const alert = wrapper.find('[data-test="load-error"]');
    expect(alert.classes()).toContain("text-error");
    expect(alert.text()).toContain("Couldn't load the pipeline");
    expect(alert.text()).toContain("Service unavailable");
    expect(wrapper.find("h1").text()).toBe("Pipeline");
    expect(wrapper.find('[data-test="window"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="owner"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="summary"]').exists()).toBe(false);
    expect(column("prospecting").exists()).toBe(false);
  });

  it("loads again when a filter changes after a failure", async () => {
    getJson.mockImplementation((path) => {
      if (path === "/api/v1/reps") return Promise.resolve(reps);
      return Promise.reject(new Error("Service unavailable"));
    });
    await mountView();

    getJson.mockImplementation((path) => {
      if (path === "/api/v1/reps") return Promise.resolve(reps);
      return Promise.resolve({ items: deals, total: deals.length });
    });
    selects()[0].vm.$emit("update:modelValue", "all");
    await flushPromises();

    expect(wrapper.find('[data-test="load-error"]').exists()).toBe(false);
    expect(wrapper.findAll('[data-test="deal"]')).toHaveLength(1);
  });
});
