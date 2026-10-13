import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { vuetify } from "../plugins/vuetify";
import AccuracyView from "../views/AccuracyView.vue";
import { accuracyReport } from "./accuracyReport";

const { BarStub, LineStub } = vi.hoisted(() => ({
  BarStub: { name: "BarStub", props: ["data", "options"], template: "<canvas />" },
  LineStub: { name: "LineStub", props: ["data", "options"], template: "<canvas />" },
}));
vi.mock("vue-chartjs", () => ({ Bar: BarStub, Line: LineStub }));

async function mountView() {
  const wrapper = mount(AccuracyView, { global: { plugins: [vuetify] } });
  await flushPromises();
  return wrapper;
}

const enoughTriage = {
  weeks: [
    { week: "2026-09-28", labelled: 3, corrected: 0 },
    { week: "2026-10-05", labelled: 3, corrected: 1 },
  ],
  total: 6,
  corrected: 1,
  kept_rate: 0.833,
};

enableAutoUnmount(afterEach);

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("AccuracyView", () => {
  it("loads the report once and shows the header", async () => {
    const getJson = vi.spyOn(api, "getJson").mockResolvedValue(accuracyReport());

    const wrapper = await mountView();

    expect(getJson).toHaveBeenCalledTimes(1);
    expect(getJson).toHaveBeenCalledWith("/api/v1/signals/accuracy");
    expect(wrapper.find("h1").text()).toBe("Prediction accuracy");
    expect(wrapper.text()).toContain(
      "How often the orchestrator's predictions matched what happened, and whether that is improving.",
    );
  });

  it("shows five tiles, each marked Simulated or Real", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue(accuracyReport());

    const wrapper = await mountView();

    const keys = ["forecast", "risk", "triage", "selector", "milestones"];
    const chips = keys.map((key) =>
      wrapper.find(`[data-test='tile-${key}'] [data-test='tile-chip']`).text(),
    );
    expect(chips).toEqual(["Simulated", "Simulated", "Real", "Real", "Real"]);
    const risk = wrapper.find("[data-test='tile-risk']");
    expect(risk.find("[data-test='tile-value']").text()).toBe("80%");
    expect(risk.find("[data-test='tile-target']").text()).toBe("12 of 15");
    expect(risk.find("[data-test='tile-detail']").text()).toBe("2 incident PRs scored T0");
  });

  it("shows the forecast chart, its legend and a chip per sprint", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue(accuracyReport());

    const wrapper = await mountView();

    const card = wrapper.find("[data-test='forecast-card']");
    expect(card.text()).toContain("as of its working day 3, using only what was known then");
    const chart = card.findComponent(LineStub);
    expect(chart.props("data").labels).toEqual(["S11", "S12", "S13"]);
    expect(card.find("[data-test='forecast-legend']").text()).toContain("dashed: target");
    const chips = card.findAll("[data-test='sprint-chip']").map((chip) => chip.text());
    expect(chips).toEqual(["S11 · 4d late", "S12 · on P50", "S13 · unfinished"]);
    expect(card.text()).toContain("A well-calibrated P85 is met about 85% of the time");
  });

  it("says nothing real is graded yet only when no real PR caused an incident", async () => {
    vi.spyOn(api, "getJson")
      .mockResolvedValueOnce(accuracyReport())
      .mockResolvedValueOnce(
        accuracyReport({
          risk: { ...accuracyReport().risk, real: { merged_prs: 40, incident_prs: 1 } },
        }),
      );

    const first = await mountView();
    expect(first.find("[data-test='risk-note']").text()).toContain(
      "0 caused an incident, so nothing real to grade yet.",
    );
    expect(first.find("[data-test='risk-card']").findComponent(BarStub).exists()).toBe(true);

    const second = await mountView();
    const note = second.find("[data-test='risk-note']").text();
    expect(note).toContain("1 caused an incident.");
    expect(note).not.toContain("nothing real to grade yet");
  });

  it("shows a thin triage trend as text and an enough selector trend as a chart", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue(accuracyReport());

    const wrapper = await mountView();

    const triage = wrapper.find("[data-test='triage-card']");
    expect(triage.findComponent(BarStub).exists()).toBe(false);
    expect(triage.find("[data-test='triage-thin']").text()).toBe(
      "3 real issues labelled so far: the trend appears at 5.",
    );
    const selector = wrapper.find("[data-test='selector-card']");
    expect(selector.find("[data-test='selector-thin']").exists()).toBe(false);
    expect(selector.findComponent(BarStub).props("data").labels).toEqual(["Sep 28", "Oct 5"]);
  });

  it("shows an enough triage trend as a chart and a thin selector trend as text", async () => {
    const report = accuracyReport({
      triage: enoughTriage,
      test_selector: { weeks: [], total: 0, missed: 0, miss_rate: null },
    });
    vi.spyOn(api, "getJson").mockResolvedValue(report);

    const wrapper = await mountView();

    const triage = wrapper.find("[data-test='triage-card']");
    expect(triage.find("[data-test='triage-thin']").exists()).toBe(false);
    expect(triage.findComponent(BarStub).props("data").datasets[0].data).toEqual([3, 2]);
    expect(wrapper.find("[data-test='selector-thin']").text()).toBe(
      "0 real commits settled so far: the trend appears at 5.",
    );
    expect(wrapper.find("[data-test='tile-selector'] [data-test='tile-value']").text()).toBe("—");
  });

  it("shows a graded milestone's figures and an ungraded one's reason", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue(accuracyReport());

    const wrapper = await mountView();

    const rows = wrapper.findAll("[data-test='milestone-row']");
    expect(rows).toHaveLength(2);
    const cells = rows[0].findAll("td").map((cell) => cell.text());
    expect(cells).toEqual([
      "Delivery forecasting (M5)",
      "Sep 28",
      "Oct 6",
      "Oct 9",
      "Oct 8",
      "2d late",
      "yes",
    ]);
    expect(rows[1].text()).toContain("Release gate (M6)");
    expect(rows[1].find("[data-test='milestone-reason']").text()).toBe("still open");
    expect(wrapper.find("[data-test='milestone-card']").text()).toContain(
      "Real work only. A milestone is graded once its work is finished",
    );
  });

  it("shows the error and loads again on retry", async () => {
    const getJson = vi
      .spyOn(api, "getJson")
      .mockRejectedValueOnce(new Error("Database is unavailable"))
      .mockResolvedValue(accuracyReport());

    const wrapper = await mountView();

    const error = wrapper.find("[data-test='load-error']");
    expect(error.text()).toContain("Couldn't load prediction accuracy");
    expect(error.text()).toContain("Database is unavailable");
    expect(wrapper.find("[data-test='tile-forecast']").exists()).toBe(false);

    await wrapper.find("[data-test='retry']").trigger("click");
    await flushPromises();

    expect(getJson).toHaveBeenCalledTimes(2);
    expect(wrapper.find("[data-test='load-error']").exists()).toBe(false);
    expect(wrapper.find("[data-test='tile-forecast']").exists()).toBe(true);
  });
});
