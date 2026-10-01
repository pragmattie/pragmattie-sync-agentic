import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getJson } from "../api";
import { barValueLabels, moneyTooltip } from "../charts/setup";
import { vuetify } from "../plugins/vuetify";
import ForecastView from "../views/ForecastView.vue";

vi.mock("../api", () => ({ getJson: vi.fn() }));

// jsdom has no canvas, so the chart is a stub; its data is tested in forecast.spec.js.
vi.mock("vue-chartjs", () => ({
  Bar: {
    name: "Bar",
    props: ["data", "options", "plugins"],
    template: '<div data-test="bar-chart" />',
  },
}));

const forecast = {
  quarter: "2026-Q4",
  start: "2026-10-01",
  end: "2026-12-31",
  quota: "1000000.00",
  won: "300000.00",
  commit: "500000.00",
  best_case: "800000.00",
  pipeline: "1900000.00",
  weighted: "612345.00",
  by_month: [
    {
      month: "2026-10",
      won: "100000.00",
      commit: "150000.00",
      best_case: "300000.00",
      weighted: "200000.00",
    },
    {
      month: "2026-11",
      won: "200000.00",
      commit: "250000.00",
      best_case: "300000.00",
      weighted: "260000.00",
    },
    {
      month: "2026-12",
      won: "0.00",
      commit: "100000.00",
      best_case: "200000.00",
      weighted: "152345.00",
    },
  ],
  by_rep: [
    {
      rep: { id: 2, name: "Jordan Park" },
      quota: "500000.00",
      won: "250000.00",
      commit: "300000.00",
      weighted: "400000.00",
      attainment_pct: 50.0,
    },
    {
      rep: { id: 1, name: "Avery Lee" },
      quota: "500000.00",
      won: "600000.00",
      commit: "600000.00",
      weighted: "212345.00",
      attainment_pct: 120.0,
    },
  ],
  by_stage: [
    { stage: "prospecting", count: 12, amount: "900000.00" },
    { stage: "qualification", count: 8, amount: "500000.00" },
    { stage: "proposal", count: 6, amount: "300000.00" },
    { stage: "negotiation", count: 4, amount: "200000.00" },
  ],
};

let wrapper;

async function mountView() {
  wrapper = mount(ForecastView, { global: { plugins: [vuetify] } });
  await flushPromises();
  return wrapper;
}

function width(selector) {
  return parseFloat(wrapper.find(selector).attributes("style").match(/width:\s*([\d.]+)%/)[1]);
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 1, 12));
  getJson.mockResolvedValue(forecast);
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("ForecastView", () => {
  it("shows the title and subtitle", async () => {
    await mountView();

    expect(wrapper.find("h1").text()).toBe("Forecast");
    expect(wrapper.text()).toContain(
      "Where the quarter will land, based on deal stage and close date.",
    );
  });

  it("offers two quarters back, the current one and the next, and loads the current", async () => {
    await mountView();

    const select = wrapper.findComponent({ name: "VSelect" });
    expect(select.props("items").map((item) => item.title)).toEqual([
      "2026-Q2",
      "2026-Q3",
      "2026-Q4 (current)",
      "2027-Q1",
    ]);
    expect(select.props("modelValue")).toBe("2026-Q4");
    expect(getJson).toHaveBeenCalledWith("/api/v1/forecast", { quarter: "2026-Q4" });
  });

  it("loads another quarter when the selection changes", async () => {
    await mountView();

    wrapper.findComponent({ name: "VSelect" }).vm.$emit("update:modelValue", "2026-Q3");
    await flushPromises();

    expect(getJson).toHaveBeenLastCalledWith("/api/v1/forecast", { quarter: "2026-Q3" });
  });

  it("shows the five tiles with their values and notes", async () => {
    await mountView();

    const tiles = wrapper.findAll("[data-test^='tile-'][class*='v-card']").map((tile) => ({
      label: tile.find("[data-test='tile-label']").text(),
      value: tile.find("[data-test='tile-value']").text(),
      note: tile.find("[data-test='tile-note']").text(),
    }));
    expect(tiles).toEqual([
      { label: "Quota", value: "$1M", note: "Sum of rep quotas" },
      { label: "Closed won", value: "$300K", note: "30% of quota" },
      { label: "Commit", value: "$500K", note: "Won + negotiation · 50%" },
      { label: "Best case", value: "$800K", note: "Commit + proposal · 80%" },
      { label: "Weighted", value: "$612.3K", note: "Won + open deals × win probability" },
    ]);
  });

  it("draws the quota meter's segments and quota marker on a quota + 5% scale", async () => {
    await mountView();

    const scale = 1000000 * 1.05;
    expect(width("[data-test='segment-won']")).toBeCloseTo((300000 / scale) * 100, 3);
    expect(width("[data-test='segment-negotiation']")).toBeCloseTo((200000 / scale) * 100, 3);
    expect(width("[data-test='segment-proposal']")).toBeCloseTo((300000 / scale) * 100, 3);
    const left = wrapper.find("[data-test='quota-marker']").attributes("style");
    expect(parseFloat(left.match(/left:\s*([\d.]+)%/)[1])).toBeCloseTo(100 / 1.05, 3);
    expect(wrapper.find("[data-test='quota-marker']").text()).toBe("Quota");
  });

  it("summarises the meter in words", async () => {
    await mountView();

    expect(wrapper.find("[data-test='meter-summary']").text()).toBe(
      "$300,000 of $1,000,000 closed",
    );
    expect(wrapper.find("[data-test='meter']").attributes("aria-label")).toBe(
      "Closed won $300,000, commit $500,000 and best case $800,000 " +
        "against a quota of $1,000,000",
    );
    expect(wrapper.find("[data-test='meter-legend']").text()).toMatch(
      /Closed won\s*Negotiation\s*Proposal/,
    );
  });

  it("switches forecast by month between chart and a full-dollar table", async () => {
    await mountView();

    expect(wrapper.find("[data-test='month-chart']").exists()).toBe(true);
    expect(wrapper.find("[data-test='month-table']").exists()).toBe(false);

    await wrapper.find("[data-test='month-view-table']").trigger("click");

    expect(wrapper.find("[data-test='month-chart']").exists()).toBe(false);
    const rows = wrapper
      .findAll("[data-test='month-table'] tbody tr")
      .map((row) => row.findAll("td").map((cell) => cell.text()));
    expect(rows).toEqual([
      ["Oct 2026", "$100,000", "$150,000", "$300,000", "$200,000"],
      ["Nov 2026", "$200,000", "$250,000", "$300,000", "$260,000"],
      ["Dec 2026", "$0", "$100,000", "$200,000", "$152,345"],
    ]);

    await wrapper.find("[data-test='month-view-chart']").trigger("click");
    expect(wrapper.find("[data-test='month-chart']").exists()).toBe(true);
  });

  it("passes the stacked months and stage bars to the charts", async () => {
    await mountView();

    const [months, stages] = wrapper.findAllComponents({ name: "Bar" });
    expect(months.props("data").datasets.map((set) => set.data)).toEqual([
      [100000, 200000, 0],
      [50000, 50000, 100000],
      [150000, 50000, 100000],
    ]);
    expect(stages.props("data").labels).toEqual([
      "Prospecting (12)",
      "Qualification (8)",
      "Proposal (6)",
      "Negotiation (4)",
    ]);
    expect(stages.props("options").indexAxis).toBe("y");
  });

  it("colours the month stacks with the forecast ramp and draws no chart legends", async () => {
    await mountView();

    const [months, stages] = wrapper.findAllComponents({ name: "Bar" });
    expect(months.props("data").datasets.map((set) => [set.label, set.backgroundColor])).toEqual([
      ["Closed won", "#0E5A61"],
      ["Negotiation", "#1B8A94"],
      ["Proposal", "#6FBAC1"],
    ]);
    expect(months.props("options").scales.x.stacked).toBe(true);
    expect(months.props("options").scales.y.stacked).toBe(true);
    expect(months.props("options").scales.y.ticks.callback(150000)).toBe("$150K");
    // The legend is plain HTML above the chart.
    const legend = wrapper.find("[data-test='month-chart']").element.previousElementSibling;
    expect(legend.textContent).toMatch(
      /Closed won\s*Negotiation\s*Proposal/,
    );
    for (const chart of [months, stages]) {
      expect(chart.props("options").plugins.legend.display).toBe(false);
      expect(chart.props("options").plugins.tooltip).toBe(moneyTooltip);
    }
    expect(stages.props("plugins")).toContain(barValueLabels);
    expect(stages.props("options").scales.x.ticks.callback(900000)).toBe("$900K");
  });

  it("lists reps in the API's order with capped attainment bars", async () => {
    await mountView();

    const rows = wrapper.findAll("[data-test='rep-table'] tbody tr");
    expect(rows.map((row) => row.findAll("td")[0].text())).toEqual(["Jordan Park", "Avery Lee"]);
    expect(rows[0].findAll("td").map((cell) => cell.text())).toEqual(
      expect.arrayContaining(["Jordan Park", "$500,000", "$250,000", "$300,000", "$400,000"]),
    );
    expect(rows[0].find("[data-test='attainment']").text()).toBe("50%");
    expect(rows[1].find("[data-test='attainment']").text()).toBe("120%");

    const bars = wrapper.findAll("[data-test='attainment-bar']");
    expect(bars[0].attributes("style")).toContain("width: 50%");
    expect(bars[1].attributes("style")).toContain("width: 100%");
  });

  it("shows a progress bar and no numbers while the forecast loads", async () => {
    let resolve;
    getJson.mockReturnValue(new Promise((done) => (resolve = done)));
    await mountView();

    expect(wrapper.find("[data-test='loading-bar']").exists()).toBe(true);
    expect(wrapper.find("[data-test='tile-quota']").exists()).toBe(false);

    resolve(forecast);
    await flushPromises();
    expect(wrapper.find("[data-test='loading-bar']").exists()).toBe(false);
    expect(wrapper.find("[data-test='tile-quota']").exists()).toBe(true);
  });

  it("clears the last quarter's forecast as soon as another quarter starts loading", async () => {
    await mountView();
    expect(wrapper.find("[data-test='tile-quota']").exists()).toBe(true);

    getJson.mockReturnValue(new Promise(() => {}));
    wrapper.findComponent({ name: "VSelect" }).vm.$emit("update:modelValue", "2026-Q3");
    await flushPromises();

    expect(wrapper.find("[data-test='loading-bar']").exists()).toBe(true);
    expect(wrapper.find("[data-test='tile-quota']").exists()).toBe(false);
    expect(wrapper.find("[data-test='meter']").exists()).toBe(false);
  });

  it("doesn't keep the last quarter's forecast when the new quarter fails", async () => {
    await mountView();

    getJson.mockRejectedValue(new Error("Service unavailable"));
    wrapper.findComponent({ name: "VSelect" }).vm.$emit("update:modelValue", "2026-Q3");
    await flushPromises();

    expect(wrapper.find("[data-test='load-error']").exists()).toBe(true);
    expect(wrapper.find("[data-test='tile-quota']").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("$300,000 of $1,000,000 closed");
  });

  it("says plainly when the quarter has no deals and no reps", async () => {
    getJson.mockResolvedValue({
      ...forecast,
      quota: "0.00",
      won: "0.00",
      commit: "0.00",
      best_case: "0.00",
      weighted: "0.00",
      by_month: forecast.by_month.map((month) => ({
        ...month,
        won: "0.00",
        commit: "0.00",
        best_case: "0.00",
        weighted: "0.00",
      })),
      by_rep: [],
      by_stage: [],
    });
    await mountView();

    expect(wrapper.find("[data-test='month-empty']").text()).toBe("No deals close in this quarter");
    expect(wrapper.find("[data-test='month-chart']").exists()).toBe(false);
    expect(wrapper.find("[data-test='stage-empty']").text()).toBe("No open deals");
    expect(wrapper.find("[data-test='stage-chart']").exists()).toBe(false);
    expect(wrapper.find("[data-test='rep-table']").text()).toContain(
      "No reps with a quota this quarter",
    );
  });

  it("shows an error alert, and keeps the header and quarter control, when loading fails", async () => {
    getJson.mockRejectedValue(new Error("Service unavailable"));
    await mountView();

    const alert = wrapper.find("[data-test='load-error']");
    expect(alert.classes()).toContain("text-error");
    expect(alert.text()).toContain("Couldn't load the forecast");
    expect(alert.text()).toContain("Service unavailable");
    expect(wrapper.find("h1").text()).toBe("Forecast");
    expect(wrapper.find("[data-test='quarter']").exists()).toBe(true);
    expect(wrapper.find("[data-test='meter']").exists()).toBe(false);
  });

  it("tries again when the quarter changes after a failure", async () => {
    getJson.mockRejectedValue(new Error("Service unavailable"));
    await mountView();

    getJson.mockResolvedValue(forecast);
    wrapper.findComponent({ name: "VSelect" }).vm.$emit("update:modelValue", "2026-Q3");
    await flushPromises();

    expect(wrapper.find("[data-test='load-error']").exists()).toBe(false);
    expect(wrapper.find("[data-test='tile-quota']").exists()).toBe(true);
  });

  it("never mentions development information", async () => {
    await mountView();

    expect(wrapper.text()).not.toMatch(/phase|health|build|agent|demo|simulated|roadmap/i);
  });
});
