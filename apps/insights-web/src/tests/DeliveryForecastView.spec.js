import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { vuetify } from "../plugins/vuetify";
import DeliveryForecastView from "../views/DeliveryForecastView.vue";

const { ChartStub } = vi.hoisted(() => ({
  ChartStub: { props: ["data", "options"], template: "<canvas />" },
}));
vi.mock("vue-chartjs", () => ({ Line: ChartStub }));

const NO_MOVE = {
  p50_days: null,
  p85_days: null,
  previous_p50: null,
  previous_p85: null,
  previous_at: null,
};

function epic(overrides = {}) {
  return {
    id: 10,
    created_at: "2026-10-06T09:14:00",
    as_of: "2026-10-06",
    kind: "epic",
    subject: "M5",
    source: "github",
    remaining_items: 6,
    ahead_items: 0,
    gate_days: 0,
    end_date: "2026-10-30",
    p50: "2026-10-20",
    p85: "2026-10-27",
    on_time_probability: 0.9,
    throughput_mean: 1.2,
    history_days: 20,
    label: "Delivery forecasting (M5)",
    moved: NO_MOVE,
    trail: [{ at: "2026-10-06T09:14:00", p50: "2026-10-20", p85: "2026-10-27" }],
    ...overrides,
  };
}

const SPRINT = {
  id: 30,
  kind: "sprint",
  subject: "Sprint 12",
  source: "synthetic",
  label: "Sprint 12",
  as_of: "2026-10-06",
  end_date: "2026-10-16",
  p50: "2026-10-15",
  p85: "2026-10-19",
  on_time_probability: 0.62,
  moved: NO_MOVE,
  trail: [],
  at_risk: [
    {
      number: 412,
      title: "Lead import retries",
      module: "leads",
      points: 5,
      expected_days: 3,
      remaining_days: 1,
      started: "2026-10-01",
      reason: "In progress longer than expected",
    },
  ],
};

const PROPOSAL = {
  summary: "Defer two items to keep the target.",
  options: [
    {
      action: "defer",
      title: "Defer the export stories",
      items: [71, 72],
      rationale: "Least connected work.",
      effect: {
        p50: "2026-10-12",
        p85: "2026-10-15",
        on_time_probability: 0.81,
        from: { p50: "2026-10-14", p85: "2026-10-18", on_time_probability: 0.4 },
      },
    },
    {
      action: "split",
      title: "Split the dashboard story",
      items: [73],
      rationale: "Ship the read-only half first.",
      effect: null,
    },
  ],
  recommended: 0,
  confidence: 0.7,
  created_at: "2026-10-06T09:20:00",
  forecast_id: 9,
  current: false,
};

function feed(overrides = {}) {
  return {
    epics: [
      epic(),
      epic({
        id: 11,
        subject: "M6",
        label: "Release gate (M6)",
        end_date: null,
        on_time_probability: null,
      }),
      epic({ id: 20, subject: "Leads revamp", label: "Leads revamp", source: "synthetic" }),
    ],
    sprint: SPRINT,
    proposals: {},
    saved: 12,
    as_of: "2026-10-06",
    ...overrides,
  };
}

async function mountView() {
  const wrapper = mount(DeliveryForecastView, { global: { plugins: [vuetify] } });
  await flushPromises();
  return wrapper;
}

enableAutoUnmount(afterEach);

beforeEach(() => {
  vi.restoreAllMocks();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("DeliveryForecastView", () => {
  it("shows real milestones, with labels, before the simulated history", async () => {
    const getJson = vi.spyOn(api, "getJson").mockResolvedValue(feed());

    const wrapper = await mountView();

    expect(getJson).toHaveBeenCalledWith("/api/v1/signals/forecast");
    const labels = wrapper.findAll("[data-test='card-label']").map((label) => label.text());
    expect(labels).toEqual(["Delivery forecasting (M5)", "Release gate (M6)"]);
    const html = wrapper.html();
    expect(html.indexOf("real-section")).toBeLessThan(html.indexOf("simulated-section"));
    const card = wrapper.find("[data-test='real-card']");
    expect(card.text()).toContain("6 items left");
    expect(card.find("[data-test='p50']").text()).toBe("Oct 20");
    expect(card.find("[data-test='p85']").text()).toBe("Oct 27");
    expect(card.find("[data-test='moved']").text()).toBe("Moved: First forecast");
    expect(card.find("[data-test='pace']").text()).toBe(
      "Agents' pace: 1.2 items per working day over the last 20 working days",
    );
    expect(wrapper.findAllComponents(ChartStub)).toHaveLength(2);
  });

  it("says how many items in earlier milestones come first, only when there are some", async () => {
    const epics = [
      epic(),
      epic({ id: 11, subject: "M6", label: "Release gate (M6)", ahead_items: 10 }),
    ];
    vi.spyOn(api, "getJson").mockResolvedValue(feed({ epics }));

    const wrapper = await mountView();

    const [first, second] = wrapper.findAll("[data-test='real-card']");
    expect(first.find("[data-test='ahead']").exists()).toBe(false);
    expect(first.text()).not.toContain("earlier milestones");
    expect(second.find("[data-test='ahead']").text()).toBe("After 10 items in earlier milestones");
  });

  it("says how many working days for gates and specs are included, only when above 0", async () => {
    const epics = [
      epic(),
      epic({ id: 11, subject: "M6", label: "Release gate (M6)", gate_days: 2 }),
    ];
    vi.spyOn(api, "getJson").mockResolvedValue(feed({ epics }));

    const wrapper = await mountView();

    const [first, second] = wrapper.findAll("[data-test='real-card']");
    expect(first.find("[data-test='gate-days']").exists()).toBe(false);
    expect(first.text()).not.toContain("gates and specs");
    expect(second.find("[data-test='gate-days']").text()).toBe(
      "Includes 2 working day(s) for gates and specs (measured from past milestones)",
    );
  });

  it("shows a finished milestone as done on the day it finished, not a forecast", async () => {
    const done = { remaining_items: 0, p50: "2026-10-16", p85: "2026-10-16" };
    const epics = [
      epic(),
      epic({ id: 11, subject: "M8", label: "Polish (M8)", ...done }),
      epic({ id: 12, subject: "M9", label: "Empty (M9)", remaining_items: 0, p50: null, p85: null }),
      epic({ id: 13, subject: "M10", label: "Waiting (M10)", remaining_items: 0, ahead_items: 3 }),
    ];
    vi.spyOn(api, "getJson").mockResolvedValue(feed({ epics }));

    const wrapper = await mountView();

    const [open, finished, empty, waiting] = wrapper.findAll("[data-test='real-card']");
    expect(open.find("[data-test='items-left']").text()).toBe("6 items left");
    expect(open.find("[data-test='p50']").text()).toBe("Oct 20");
    expect(open.find("[data-test='finished']").exists()).toBe(false);

    expect(finished.find("[data-test='items-left']").text()).toBe("Done");
    expect(finished.text()).toContain("Finished");
    expect(finished.find("[data-test='finished']").text()).toBe("Oct 16");
    expect(finished.find("[data-test='p50']").exists()).toBe(false);
    expect(finished.find("[data-test='p85']").exists()).toBe(false);
    for (const kept of ["target", "moved", "pace"]) {
      expect(finished.find(`[data-test='${kept}']`).exists()).toBe(true);
    }

    expect(empty.find("[data-test='items-left']").text()).toBe("Done");
    expect(empty.find("[data-test='finished']").text()).toBe("No items yet");

    expect(waiting.find("[data-test='items-left']").text()).toBe("0 items left");
    expect(waiting.find("[data-test='p50']").text()).toBe("Oct 20");
    expect(waiting.find("[data-test='finished']").exists()).toBe(false);
  });

  it("shows a card's target chance, or No target set", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue(feed());

    const wrapper = await mountView();

    const targets = wrapper.findAll("[data-test='target']").map((target) => target.text());
    expect(targets).toEqual(["Target Oct 30: 90% chance", "No target set"]);
  });

  it("flashes a card whose forecast id changed on the next poll", async () => {
    const moved = { ...NO_MOVE, p50_days: 1, p85_days: 2, previous_at: "2026-10-06T09:14:00" };
    const getJson = vi
      .spyOn(api, "getJson")
      .mockResolvedValueOnce(feed())
      .mockResolvedValue(feed({ epics: [epic({ id: 15, p85: "2026-10-29", moved })] }));

    const wrapper = await mountView();
    expect(wrapper.find(".flash").exists()).toBe(false);

    await vi.advanceTimersByTimeAsync(15000);
    await flushPromises();

    expect(getJson).toHaveBeenCalledTimes(2);
    const card = wrapper.find("[data-test='real-card']");
    expect(card.classes()).toContain("flash");
    expect(card.find("[data-test='moved']").text()).toContain("P85 2 days later");

    await vi.advanceTimersByTimeAsync(2000);
    expect(wrapper.find(".flash").exists()).toBe(false);
  });

  it("flashes twice for two changes a second apart, and ends with no flash", async () => {
    vi.spyOn(api, "getJson")
      .mockResolvedValueOnce(feed())
      .mockRejectedValueOnce(new Error("Database is unavailable"))
      .mockResolvedValueOnce(feed({ epics: [epic({ id: 15 })] }))
      .mockResolvedValue(feed({ epics: [epic({ id: 16 })] }));
    const wrapper = await mountView();
    const flashes = () => wrapper.find("[data-test='real-card']").classes().includes("flash");

    await vi.advanceTimersByTimeAsync(15000); // the poll fails
    await vi.advanceTimersByTimeAsync(14000);
    await wrapper.find("[data-test='retry']").trigger("click"); // 29 s: the first change
    await flushPromises();
    expect(flashes()).toBe(true);

    await vi.advanceTimersByTimeAsync(1000); // 30 s: the poll brings the second change
    await flushPromises();
    expect(flashes()).toBe(false); // the class goes for a frame, so the animation replays
    await vi.advanceTimersByTimeAsync(20);
    expect(flashes()).toBe(true);

    await vi.advanceTimersByTimeAsync(1500); // past the first flash's end: still flashing
    expect(flashes()).toBe(true);
    await vi.advanceTimersByTimeAsync(500);
    expect(flashes()).toBe(false);
    expect(vi.getTimerCount()).toBe(1); // only the poll is left
  });

  it("clears its flash timers when the page is left", async () => {
    vi.spyOn(api, "getJson")
      .mockResolvedValueOnce(feed())
      .mockResolvedValue(feed({ epics: [epic({ id: 15 })] }));
    const wrapper = await mountView();
    await vi.advanceTimersByTimeAsync(15000);
    await flushPromises();
    expect(wrapper.find(".flash").exists()).toBe(true);

    wrapper.unmount();

    expect(vi.getTimerCount()).toBe(0);
  });

  it("shows a proposal's effects, its recommended option and the out-of-date note", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue(feed({ proposals: { M5: PROPOSAL } }));

    const wrapper = await mountView();

    const proposals = wrapper.findAll("[data-test='proposal']");
    expect(proposals).toHaveLength(1);
    const proposal = proposals[0];
    expect(proposal.find("[data-test='proposal-summary']").text()).toBe(PROPOSAL.summary);
    const options = proposal.findAll("[data-test='proposal-option']");
    expect(options[0].text()).toContain("Defer the export stories");
    expect(options[0].text()).toContain("defer: #71, #72");
    expect(options[0].find("[data-test='proposal-effect']").text()).toBe(
      "P85 Oct 18 → Oct 15; target chance 40% → 81%",
    );
    expect(options[1].find("[data-test='proposal-effect']").text()).toBe(
      "Not measurable by the simulation",
    );
    expect(options[0].find("[data-test='recommended']").text()).toBe("Recommended");
    expect(options[1].find("[data-test='recommended']").exists()).toBe(false);
    expect(proposal.find("[data-test='proposal-out-of-date']").text()).toBe(
      "Out of date: the forecast has moved since this was drafted",
    );
    expect(proposal.find("[data-test='proposal-note']").text()).toBe(
      "A proposal only: nothing has changed. A person decides.",
    );
  });

  it("leaves out the out-of-date note for a current proposal", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue(
      feed({ proposals: { M5: { ...PROPOSAL, current: true } } }),
    );

    const wrapper = await mountView();

    expect(wrapper.find("[data-test='proposal-out-of-date']").exists()).toBe(false);
  });

  it("labels the simulated history with its heading and sentence", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue(feed());

    const wrapper = await mountView();

    expect(wrapper.find("[data-test='simulated-heading']").text()).toBe(
      "Simulated history (calibration data)",
    );
    expect(wrapper.find("[data-test='simulated-note']").text()).toBe(
      "Generated history used to test the forecasting model; not a real team.",
    );
    const sprint = wrapper.find("[data-test='sprint']");
    expect(sprint.text()).toContain("ends Oct 16");
    expect(sprint.find("[data-test='sprint-chance']").text()).toContain("62%");
    expect(sprint.find("[data-test='at-risk']").text()).toBe(
      "#412 Lead import retries: In progress longer than expected",
    );
    expect(wrapper.find("[data-test='simulated-epics']").text()).toContain("Leads revamp");
  });

  it("says when there are no forecasts at all", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue({
      epics: [],
      sprint: null,
      proposals: {},
      saved: 0,
      as_of: null,
    });

    const wrapper = await mountView();

    expect(wrapper.find("[data-test='empty-forecasts']").text()).toBe(
      "No forecasts yet. The forecaster saves one after each collection.",
    );
    expect(wrapper.find("[data-test='simulated-section']").exists()).toBe(false);
  });

  it("says when no real milestones are collected yet, still showing the simulated history", async () => {
    vi.spyOn(api, "getJson").mockResolvedValue(feed({ epics: [feed().epics[2]] }));

    const wrapper = await mountView();

    expect(wrapper.find("[data-test='empty-real']").text()).toBe(
      "No real milestones collected yet (see Running beside v1).",
    );
    expect(wrapper.find("[data-test='real-card']").exists()).toBe(false);
    expect(wrapper.find("[data-test='simulated-section']").exists()).toBe(true);
  });

  it("shows an error and tries again on the next poll", async () => {
    const getJson = vi
      .spyOn(api, "getJson")
      .mockRejectedValueOnce(new Error("Database is unavailable"))
      .mockResolvedValue(feed());

    const wrapper = await mountView();
    expect(wrapper.find("[data-test='load-error']").text()).toContain("Database is unavailable");

    await vi.advanceTimersByTimeAsync(15000);
    await flushPromises();

    expect(getJson).toHaveBeenCalledTimes(2);
    expect(wrapper.find("[data-test='load-error']").exists()).toBe(false);
    expect(wrapper.find("[data-test='real-card']").exists()).toBe(true);
  });

  it("stops polling when the page is left", async () => {
    const getJson = vi.spyOn(api, "getJson").mockResolvedValue(feed());

    const wrapper = await mountView();
    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(45000);

    expect(getJson).toHaveBeenCalledTimes(1);
  });
});
