import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { vuetify } from "../plugins/vuetify";
import DeliveryFlowView from "../views/DeliveryFlowView.vue";

const { ChartStub } = vi.hoisted(() => ({
  ChartStub: { props: ["data", "options"], template: "<canvas />" },
}));
vi.mock("vue-chartjs", () => ({ Bar: ChartStub, Line: ChartStub }));

const STAGES = ["Backlog", "Triaged", "In progress", "In review", "Merged", "Production"];

function counts(values) {
  return Object.fromEntries(STAGES.map((stage, index) => [stage, values[index]]));
}

function flowResult({ source = "github", days = 60, available = { github: 4, synthetic: 90 } } = {}) {
  return {
    source,
    days,
    stages: STAGES,
    series: [
      { date: "2026-10-01", counts: counts([1, 2, 1, 0, 0, 0]) },
      { date: "2026-10-02", counts: counts([1, 1, 1, 1, 1, 0]) },
    ],
    throughput: [
      { week: "2026-09-21", merged: 0 },
      { week: "2026-09-28", merged: 1 },
    ],
    cycle_time_days: { median: 2.5, p85: 4.1 },
    items: 5,
    available,
  };
}

async function mountView() {
  const wrapper = mount(DeliveryFlowView, { global: { plugins: [vuetify] } });
  await flushPromises();
  return wrapper;
}

function mockFlow(available) {
  return vi
    .spyOn(api, "getJson")
    .mockImplementation(async (_path, params) =>
      flowResult({ source: params.source, days: params.days, available }),
    );
}

enableAutoUnmount(afterEach);

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("DeliveryFlowView", () => {
  it("defaults to Real work when real items exist", async () => {
    const getJson = mockFlow({ github: 4, synthetic: 90 });

    const wrapper = await mountView();

    expect(getJson).toHaveBeenCalledWith("/api/v1/signals/flow", { source: "github", days: 60 });
    expect(wrapper.find("[data-test='real-note']").text()).toBe(
      "Work in this repository, built by the agents.",
    );
    expect(wrapper.find("[data-test='simulated-chip']").exists()).toBe(false);
    expect(wrapper.find("[data-test='empty-real']").exists()).toBe(false);
    expect(wrapper.find("[data-test='flow-chart']").exists()).toBe(true);
    const tiles = ["items", "median", "p85"].map((key) =>
      wrapper.find(`[data-test='tile-${key}'] [data-test='tile-value']`).text(),
    );
    expect(tiles).toEqual(["5", "2.5 days", "4.1 days"]);
  });

  it("shows the empty Real state when there is no real work, with a way to the simulated history", async () => {
    const getJson = mockFlow({ github: 0, synthetic: 90 });

    const wrapper = await mountView();

    expect(wrapper.find("[data-test='empty-real']").text()).toContain(
      "No real work recorded here yet. It appears once this system collects its own repository " +
        "(rebuild plan 7.1) or imports v1's record of the rebuild (7.4).",
    );
    expect(wrapper.find("[data-test='flow-chart']").exists()).toBe(false);

    await wrapper.find("[data-test='view-simulated']").trigger("click");
    await flushPromises();

    expect(getJson).toHaveBeenLastCalledWith("/api/v1/signals/flow", {
      source: "synthetic",
      days: 60,
    });
    expect(wrapper.find("[data-test='empty-real']").exists()).toBe(false);
    expect(wrapper.find("[data-test='flow-chart']").exists()).toBe(true);
  });

  it("labels the simulated history with its chip and sentence", async () => {
    mockFlow({ github: 4, synthetic: 90 });

    const wrapper = await mountView();
    await wrapper.find("[data-test='source-synthetic']").trigger("click");
    await flushPromises();

    expect(wrapper.find("[data-test='simulated-chip']").text()).toBe(
      "Simulated history · calibration data",
    );
    expect(wrapper.find("[data-test='simulated-note']").text()).toBe(
      "Generated history used to test the forecasting and risk models; not a real team.",
    );
    expect(wrapper.find("[data-test='real-note']").exists()).toBe(false);
  });

  it("gives the chart one stacked band per stage, in stage order", async () => {
    mockFlow({ github: 4, synthetic: 90 });

    const wrapper = await mountView();

    const [flowChart, throughput] = wrapper.findAllComponents(ChartStub);
    const datasets = flowChart.props("data").datasets;
    expect(datasets.map((set) => set.label)).toEqual(STAGES);
    expect(datasets.map((set) => set.fill)).toEqual(["origin", "-1", "-1", "-1", "-1", "-1"]);
    expect(datasets[4].data).toEqual([0, 1]);
    expect(flowChart.props("options").scales.y.stacked).toBe(true);
    expect(throughput.props("data").datasets[0].data).toEqual([0, 1]);
  });

  it("queries again when the days change", async () => {
    const getJson = mockFlow({ github: 4, synthetic: 90 });

    const wrapper = await mountView();
    await wrapper.find("[data-test='days-180']").trigger("click");
    await flushPromises();

    expect(getJson).toHaveBeenCalledTimes(2);
    expect(getJson).toHaveBeenLastCalledWith("/api/v1/signals/flow", {
      source: "github",
      days: 180,
    });
  });

  it("shows an error and loads again on Retry", async () => {
    const getJson = vi
      .spyOn(api, "getJson")
      .mockRejectedValueOnce(new Error("Database is unavailable"))
      .mockResolvedValue(flowResult());

    const wrapper = await mountView();

    expect(wrapper.find("[data-test='load-error']").text()).toContain("Database is unavailable");
    await wrapper.find("[data-test='retry']").trigger("click");
    await flushPromises();

    expect(getJson).toHaveBeenCalledTimes(2);
    expect(wrapper.find("[data-test='flow-chart']").exists()).toBe(true);
  });
});
