import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import RiskModelPanel from "../components/RiskModelPanel.vue";
import { vuetify } from "../plugins/vuetify";

const { ChartStub } = vi.hoisted(() => ({
  ChartStub: { props: ["data", "options"], template: "<canvas />" },
}));
vi.mock("vue-chartjs", () => ({ Bar: ChartStub, Line: ChartStub }));

const POOLED = {
  histories: 30,
  merged_prs: 15044,
  incident_prs: 451,
  overall_rate: 0.03,
  t0_rate: 0.0018,
  top_decile: { capture: 0.5787, lowest: 0.375, highest: 0.8 },
  bars: { t0_rate_at_most_a_quarter_of_overall: true, top_decile_captures_majority_pooled: true },
};

function report(bars = {}) {
  return {
    merged_prs: 500,
    incident_prs: 16,
    by_tier: {
      T0: { prs: 300, incidents: 1 },
      T1: { prs: 120, incidents: 5 },
      T2: { prs: 60, incidents: 4 },
      T3: { prs: 20, incidents: 6 },
    },
    top_decile: { size: 50, incidents: 9, capture: 0.5625 },
    thresholds: {
      T1: { flagged: 200, incidents: 15, precision: 0.075, recall: 0.9375 },
      T2: { flagged: 80, incidents: 10, precision: 0.125, recall: 0.625 },
      T3: { flagged: 20, incidents: 6, precision: 0.3, recall: 0.375 },
    },
    bars: { t0_has_no_incidents: true, top_decile_captures_majority: true, ...bars },
    real_merged_prs: 42,
    real_incident_prs: 0,
    pooled: POOLED,
    as_of: "2026-10-08T09:00:00",
  };
}

async function mountPanel() {
  const wrapper = mount(RiskModelPanel, { global: { plugins: [vuetify] } });
  await flushPromises();
  return wrapper;
}

enableAutoUnmount(afterEach);

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("RiskModelPanel", () => {
  it("labels the data simulated and gives the real counts", async () => {
    const getJson = vi.spyOn(api, "getJson").mockResolvedValue(report());

    const wrapper = await mountPanel();

    expect(getJson).toHaveBeenCalledWith("/api/v1/signals/calibration");
    expect(wrapper.find("h2").text()).toBe("Risk model");
    expect(wrapper.find("[data-test='calibration-chip']").text()).toBe(
      "Calibration data · simulated",
    );
    expect(wrapper.find("[data-test='real-counts']").text()).toBe(
      "The risk score is graded on simulated history, because real changes haven't caused " +
        "incidents yet. 42 real PRs merged so far, 0 caused an incident.",
    );
  });

  it("shows the thresholds and tiers tables", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue(report());

    const wrapper = await mountPanel();

    const cells = (selector) =>
      wrapper.findAll(`${selector} td`).map((cell) => cell.text());
    expect(cells("[data-test='threshold-T1+']")).toEqual(["T1+", "200", "15", "7.5%", "93.8%"]);
    expect(cells("[data-test='threshold-T2+']")).toEqual(["T2+", "80", "10", "12.5%", "62.5%"]);
    expect(cells("[data-test='threshold-T3+']")).toEqual(["T3+", "20", "6", "30.0%", "37.5%"]);
    expect(cells("[data-test='tier-T0']")).toEqual(["T0", "300", "1"]);
    expect(cells("[data-test='tier-T3']")).toEqual(["T3", "20", "6"]);
    expect(wrapper.findAll("[data-test='tiers'] tbody tr")).toHaveLength(4);
  });

  it("gives the pooled verdict and this history's figures without a verdict", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue(report());

    const wrapper = await mountPanel();

    const bars = wrapper.find("[data-test='bars']");
    expect(bars.findAll("[data-test='verdict']").map((chip) => chip.text())).toEqual([
      "PASS",
      "PASS",
    ]);
    expect(wrapper.find("[data-test='pooled-note']").text()).toBe(
      "The risk score passes both bars across 30 generated histories: T0's incident rate " +
        "0.18% against 3.00% overall, and the top tenth by score catches 57.9% of incident PRs.",
    );
    const history = wrapper.find("[data-test='history']");
    expect(history.text()).toContain("This database's history (one history is a noisy judge)");
    expect(wrapper.find("[data-test='history-t0_rate']").text()).toBe(
      "T0's incident rate 0.33% against 3.20% overall",
    );
    expect(wrapper.find("[data-test='history-top_decile']").text()).toBe(
      "The top tenth by score catches 56.3% of incident PRs",
    );
  });

  it("never grades this history on its own, even when its strict bars would fail", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue(
      report({
        t0_has_no_incidents: false,
        top_decile_captures_majority: false,
      }),
    );

    const wrapper = await mountPanel();

    const verdicts = wrapper.findAll("[data-test='verdict']");
    expect(verdicts.map((chip) => chip.text())).toEqual(["PASS", "PASS"]);
    expect(wrapper.find("[data-test='history']").text()).not.toMatch(/PASS|FAIL/);
    expect(wrapper.text()).not.toContain("No incident PR in T0");
  });

  it("takes its verdict from the pooled grading alone", async () => {
    const failing = {
      ...POOLED,
      bars: { ...POOLED.bars, top_decile_captures_majority_pooled: false },
    };
    vi.spyOn(api, "getJson").mockResolvedValue({
      ...report(),
      pooled: failing,
    });

    const wrapper = await mountPanel();

    expect(
      wrapper
        .find("[data-test='bar-top_decile_captures_majority_pooled'] [data-test='verdict']")
        .text(),
    ).toBe("FAIL");
    expect(wrapper.find("[data-test='pooled-note']").text()).toMatch(
      /^The risk score does not pass both bars across 30 generated histories/,
    );
  });

  it("charts precision and recall at each threshold", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue(report());

    const wrapper = await mountPanel();

    const chart = wrapper.findComponent(ChartStub);
    expect(chart.props("data").labels).toEqual(["T1+", "T2+", "T3+"]);
    expect(chart.props("data").datasets.map((set) => [set.label, set.data])).toEqual([
      ["Precision", [7.5, 12.5, 30]],
      ["Recall", [93.8, 62.5, 37.5]],
    ]);
    expect(chart.attributes("aria-label")).toBeTruthy();
  });

  it("shows its own error and loads again on Retry", async () => {
    const getJson = vi
      .spyOn(api, "getJson")
      .mockRejectedValueOnce(new Error("Database is unavailable"))
      .mockResolvedValue(report());

    const wrapper = await mountPanel();

    expect(wrapper.find("[data-test='load-error']").text()).toContain("Database is unavailable");
    expect(wrapper.find("[data-test='calibration-chip']").exists()).toBe(true);
    expect(wrapper.find("[data-test='thresholds']").exists()).toBe(false);

    await wrapper.find("[data-test='retry']").trigger("click");
    await flushPromises();

    expect(getJson).toHaveBeenCalledTimes(2);
    expect(wrapper.find("[data-test='load-error']").exists()).toBe(false);
    expect(wrapper.find("[data-test='thresholds']").exists()).toBe(true);
  });
});
