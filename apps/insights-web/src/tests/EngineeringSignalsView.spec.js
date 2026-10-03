import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { vuetify } from "../plugins/vuetify";
import EngineeringSignalsView from "../views/EngineeringSignalsView.vue";

const SUMMARY = {
  days: 30,
  current: {
    deploys_per_week: 3.5,
    lead_time_hours: 20,
    change_failure_rate: null,
    time_to_restore_hours: 2.5,
  },
  previous: {
    deploys_per_week: 3,
    lead_time_hours: 18.5,
    change_failure_rate: 4,
    time_to_restore_hours: 2.5,
  },
};

const SPRINTS = [
  { sprint: "Sprint 1", goal: "Leads", in_progress: false, committed: 30, completed: 28 },
  { sprint: "Sprint 2", goal: "Pipeline", in_progress: true, committed: 32, completed: 10 },
];

const CYCLE_TIME = [
  { sprint: "Sprint 1", in_progress: false, median_hours: 18, p85_hours: 40, merged: 12 },
  { sprint: "Sprint 2", in_progress: true, median_hours: 20, p85_hours: 44, merged: 5 },
];

const CI = {
  by_suite: [
    { suite: "api", runs: 200, pass_rate: 96.5, flaky_rate: 1.5 },
    { suite: "crm-web", runs: 150, pass_rate: 91, flaky_rate: 4.2 },
  ],
  by_week: [],
};

const MODULES = [
  { module: "billing_auth", merged_prs: 40, incidents: 4, incident_rate: 10, days_per_point: 1.6 },
  { module: "leads", merged_prs: 60, incidents: 1, incident_rate: 1.7, days_per_point: 1 },
  { module: "platform", merged_prs: 30, incidents: 0, incident_rate: 0, days_per_point: 1.1 },
];

const SOURCES = {
  issues: { synthetic: 300, github: 0 },
  pull_requests: { synthetic: 412, github: 0 },
  ci_runs: { synthetic: 350, github: 0 },
  deployments: { synthetic: 90, github: 0 },
  incidents: { synthetic: 5, github: 0 },
};

const EMPTY_SOURCES = Object.fromEntries(
  Object.keys(SOURCES).map((name) => [name, { synthetic: 0, github: 0 }]),
);

function responses(overrides = {}) {
  return {
    "/api/v1/signals/summary": SUMMARY,
    "/api/v1/signals/sprints": SPRINTS,
    "/api/v1/signals/cycle-time": CYCLE_TIME,
    "/api/v1/signals/ci": CI,
    "/api/v1/signals/modules": MODULES,
    "/api/v1/signals/sources": SOURCES,
    ...overrides,
  };
}

function mockApi(bodies, { failing = null } = {}) {
  const fetch = vi.fn(async (url) => {
    const { pathname } = new URL(url);
    if (pathname === failing) {
      return { ok: false, status: 503, json: async () => ({ detail: "Database is unavailable" }) };
    }
    return { ok: true, status: 200, json: async () => bodies[pathname] };
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

// Chart.js needs a real canvas, so the charts are stand-ins whose props are checked.
const { ChartStub } = vi.hoisted(() => ({
  ChartStub: { props: ["data", "options"], template: "<canvas />" },
}));
vi.mock("vue-chartjs", () => ({ Bar: ChartStub, Line: ChartStub }));

async function mountView() {
  const wrapper = mount(EngineeringSignalsView, {
    global: {
      plugins: [vuetify],
    },
  });
  await flushPromises();
  return wrapper;
}

enableAutoUnmount(afterEach);

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("EngineeringSignalsView", () => {
  it("requests the six signals in parallel", async () => {
    const fetch = mockApi(responses());

    const wrapper = await mountView();

    const urls = fetch.mock.calls.map(([url]) => {
      const { pathname, search } = new URL(url);
      return `${pathname}${search}`;
    });
    expect(urls.sort()).toEqual(
      [
        "/api/v1/signals/summary?days=30",
        "/api/v1/signals/sprints",
        "/api/v1/signals/cycle-time?bucket=sprint",
        "/api/v1/signals/ci?weeks=26",
        "/api/v1/signals/modules",
        "/api/v1/signals/sources",
      ].sort(),
    );
    expect(wrapper.find("h1").text()).toBe("Engineering signals");
    expect(wrapper.text()).toContain(
      "How the PragMattie Sync team delivers: the history the prediction models learn from.",
    );
  });

  it("shows the loading bar while waiting", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));

    const wrapper = mount(EngineeringSignalsView, {
      global: { plugins: [vuetify] },
    });
    await wrapper.vm.$nextTick();

    expect(wrapper.find("[data-test='loading-bar']").exists()).toBe(true);
  });

  it("says how much of the history is simulated", async () => {
    mockApi(responses());

    const wrapper = await mountView();

    const alert = wrapper.find("[data-test='source-note']");
    expect(alert.text()).toBe(
      "412 simulated PRs of history. Real GitHub activity appears here once the collector runs.",
    );
    expect(alert.find(".mdi-flask-outline").exists()).toBe(true);
  });

  it("shows the DORA tiles with their changes and a dash for missing values", async () => {
    mockApi(responses());

    const wrapper = await mountView();

    expect(wrapper.text()).toContain("Last 30 days · DORA delivery measures");
    const value = (key) => wrapper.find(`[data-test='tile-${key}'] [data-test='tile-value']`);
    const change = (key) => wrapper.find(`[data-test='tile-${key}'] [data-test='tile-delta']`);
    expect(value("deploys_per_week").text()).toBe("3.5 per week");
    expect(value("lead_time_hours").text()).toBe("20h");
    expect(value("change_failure_rate").text()).toBe("—");
    expect(value("time_to_restore_hours").text()).toBe("2.5h");

    expect(change("deploys_per_week").text()).toBe("+0.5 vs prior 30 days");
    expect(change("deploys_per_week").classes()).toContain("text-success");
    expect(change("deploys_per_week").find(".mdi-arrow-up").exists()).toBe(true);
    expect(change("lead_time_hours").text()).toBe("+1.5h vs prior 30 days");
    expect(change("lead_time_hours").classes()).toContain("text-error");
    expect(change("change_failure_rate").exists()).toBe(false);
    expect(change("time_to_restore_hours").find(".mdi-minus").exists()).toBe(true);
    expect(change("time_to_restore_hours").classes()).not.toContain("text-success");
    expect(change("time_to_restore_hours").classes()).not.toContain("text-error");
    expect(wrapper.find("[data-test='tile-lead_time_hours']").text()).toContain(
      "PR opened → merged, median",
    );
  });

  it("draws the velocity and cycle time charts with starred labels", async () => {
    mockApi(responses());

    const wrapper = await mountView();

    const [bar, line] = wrapper.findAllComponents(ChartStub);
    expect(bar.props("data").labels).toEqual(["S1", "S2*"]);
    expect(bar.props("data").datasets.map((set) => set.backgroundColor)).toEqual([
      "#6FBAC1",
      "#0E5A61",
    ]);
    expect(bar.attributes("aria-label")).toBeTruthy();
    const title = bar.props("options").plugins.tooltip.callbacks.title;
    expect(title([{ dataIndex: 1 }])).toEqual(["Sprint 2 (in progress)", "Pipeline"]);

    expect(line.props("data").labels).toEqual(["S1", "S2*"]);
    const footer = line.props("options").plugins.tooltip.callbacks.footer;
    expect(footer([{ dataIndex: 0 }])).toBe("12 PRs merged");
    expect(line.props("options").scales.y.beginAtZero).toBe(true);
    expect(line.props("options").scales.x.grid.display).toBe(false);
    expect(wrapper.find("[data-test='velocity']").text()).toContain("* in progress");
  });

  it("flags modules that run well over estimate", async () => {
    mockApi(responses());

    const wrapper = await mountView();

    const table = wrapper.find("[data-test='modules']");
    expect(table.text()).toContain(
      "Incident rate = share of merged PRs that caused a production incident",
    );
    const billing = table.find("[data-test='module-billing_auth']");
    expect(billing.text()).toContain("Billing & Auth");
    expect(billing.text()).toContain("10%");
    const warning = billing.find("[data-test='over-estimate']");
    expect(warning.attributes("title")).toBe(
      "Runs well over estimate compared with other modules",
    );
    expect(table.find("[data-test='module-leads'] [data-test='over-estimate']").exists()).toBe(
      false,
    );
    expect(table.findAll("[data-test='over-estimate']")).toHaveLength(1);
  });

  it("flags flaky CI suites", async () => {
    mockApi(responses());

    const wrapper = await mountView();

    const table = wrapper.find("[data-test='ci']");
    expect(table.text()).toContain("Last 26 weeks · flaky = failed, then passed on re-run");
    const flaky = table.find("[data-test='suite-crm-web'] [data-test='flaky']");
    expect(flaky.attributes("title")).toBe("Flaky suite");
    expect(table.find("[data-test='suite-api'] [data-test='flaky']").exists()).toBe(false);
  });

  it("shows the error and fetches all six again on Retry", async () => {
    const fetch = mockApi(responses(), { failing: "/api/v1/signals/ci" });

    const wrapper = await mountView();

    const error = wrapper.find("[data-test='load-error']");
    expect(error.text()).toContain("Database is unavailable");
    expect(wrapper.find("[data-test='source-note']").exists()).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(6);

    mockApi(responses());
    await wrapper.find("[data-test='retry']").trigger("click");
    await flushPromises();

    expect(globalThis.fetch).toHaveBeenCalledTimes(6);
    expect(wrapper.find("[data-test='load-error']").exists()).toBe(false);
    expect(wrapper.find("[data-test='source-note']").exists()).toBe(true);
  });

  it("leaves out sections with no data", async () => {
    mockApi(
      responses({
        "/api/v1/signals/sprints": [],
        "/api/v1/signals/cycle-time": [],
        "/api/v1/signals/modules": [],
        "/api/v1/signals/ci": { by_suite: [], by_week: [] },
      }),
    );

    const wrapper = await mountView();

    expect(wrapper.find("[data-test='source-note']").exists()).toBe(true);
    expect(wrapper.find("[data-test='velocity']").exists()).toBe(false);
    expect(wrapper.find("[data-test='modules']").exists()).toBe(false);
    expect(wrapper.find("[data-test='ci']").exists()).toBe(false);
  });

  it("says there is no history yet", async () => {
    mockApi(
      responses({
        "/api/v1/signals/summary": { days: 30, current: {}, previous: {} },
        "/api/v1/signals/sprints": [],
        "/api/v1/signals/cycle-time": [],
        "/api/v1/signals/modules": [],
        "/api/v1/signals/ci": { by_suite: [], by_week: [] },
        "/api/v1/signals/sources": EMPTY_SOURCES,
      }),
    );

    const wrapper = await mountView();

    expect(wrapper.find("[data-test='empty-history']").text()).toBe(
      "No engineering history yet. Run python -m sdlc.synth in the orchestrator to generate it.",
    );
    expect(wrapper.find("[data-test='source-note']").exists()).toBe(false);
  });
});
