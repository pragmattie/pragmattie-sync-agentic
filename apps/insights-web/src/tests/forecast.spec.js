import { afterEach, describe, expect, it } from "vitest";
import {
  NOT_MEASURABLE,
  changedIds,
  chanceColor,
  clockTime,
  dayLabel,
  dayNumber,
  effectText,
  formatDay,
  hasForecasts,
  itemsText,
  movedText,
  paceText,
  percent,
  splitFeed,
  targetColor,
  targetText,
  trailChartData,
} from "../forecast";

const LOCAL_TIME = { hour: "2-digit", minute: "2-digit" };

function localTime(hours, minutes) {
  return new Date(Date.UTC(2026, 9, 6, hours, minutes)).toLocaleTimeString(undefined, LOCAL_TIME);
}

const originalTz = process.env.TZ;

afterEach(() => {
  process.env.TZ = originalTz;
});

describe("forecast helpers", () => {
  it("colours a chance green at 85, amber from 50 to 84 and red below 50", () => {
    expect(chanceColor(85)).toBe("success");
    expect(chanceColor(84)).toBe("warning");
    expect(chanceColor(50)).toBe("warning");
    expect(chanceColor(49)).toBe("error");
    expect(percent(0.846)).toBe(85);
  });

  it("states the target chance, or that there is no target", () => {
    expect(targetText({ end_date: "2026-10-30", on_time_probability: 0.81 })).toBe(
      "Target Oct 30: 81% chance",
    );
    expect(targetColor({ end_date: "2026-10-30", on_time_probability: 0.81 })).toBe("warning");
    expect(targetText({ end_date: null, on_time_probability: null })).toBe("No target set");
    expect(targetColor({ end_date: null, on_time_probability: null })).toBeUndefined();
  });

  it("words a later and an earlier move against the previous forecast's local time", () => {
    const later = { p50_days: 1, p85_days: 2, previous_at: "2026-10-06T09:14:00" };
    const earlier = { p50_days: -1, p85_days: -1, previous_at: "2026-10-06T09:14:00" };

    expect(movedText(later)).toBe(`P85 2 days later than the forecast at ${localTime(9, 14)}`);
    expect(movedText(earlier)).toBe(`P85 1 day earlier than the forecast at ${localTime(9, 14)}`);
    expect(movedText({ p50_days: 3, p85_days: 0, previous_at: "2026-10-06T09:14:00" })).toBe(
      `P50 3 days later than the forecast at ${localTime(9, 14)}`,
    );
  });

  it("says No change, or First forecast when there is nothing to compare", () => {
    expect(movedText({ p50_days: 0, p85_days: 0, previous_at: "2026-10-06T09:14:00" })).toBe(
      "No change",
    );
    expect(
      movedText({ p50_days: null, p85_days: null, previous_at: null, previous_p85: null }),
    ).toBe("First forecast");
  });

  it("reads a time without a zone marker as UTC", () => {
    process.env.TZ = "Asia/Kolkata";
    expect(clockTime("2026-10-06T09:00:00")).toMatch(/^(14|0?2):30/);
    expect(clockTime("2026-10-06T09:00:00")).toBe(localTime(9, 0));
    expect(clockTime("2026-10-06T09:00:00Z")).toBe(localTime(9, 0));
  });

  it("formats a calendar date without shifting it by the time zone", () => {
    process.env.TZ = "America/Los_Angeles";
    expect(formatDay("2026-10-02")).toBe("Oct 2");
    process.env.TZ = "Pacific/Kiritimati";
    expect(formatDay("2026-10-02")).toBe("Oct 2");
    expect(formatDay(null)).toBe("—");
    expect(dayLabel(dayNumber("2026-10-02"))).toBe("Oct 2");
  });

  it("formats a defer option's measured effect, and none for a split", () => {
    const defer = {
      action: "defer",
      effect: {
        p50: "2026-10-12",
        p85: "2026-10-15",
        on_time_probability: 0.81,
        from: { p50: "2026-10-14", p85: "2026-10-18", on_time_probability: 0.4 },
      },
    };

    expect(effectText(defer)).toBe("P85 Oct 18 → Oct 15; target chance 40% → 81%");
    expect(
      effectText({
        action: "defer",
        effect: { ...defer.effect, on_time_probability: null, from: { p85: "2026-10-18" } },
      }),
    ).toBe("P85 Oct 18 → Oct 15");
    expect(effectText({ action: "split", effect: null })).toBe(NOT_MEASURABLE);
    expect(itemsText([12, 14])).toBe("#12, #14");
  });

  it("states the agents' pace", () => {
    expect(paceText({ throughput_mean: 1.4, history_days: 20 })).toBe(
      "Agents' pace: 1.4 items per working day over the last 20 working days",
    );
  });

  it("splits real milestones from simulated epics, keeping feed order", () => {
    const feed = {
      epics: [
        { subject: "M5", source: "github" },
        { subject: "M6", source: "mixed" },
        { subject: "Leads", source: "synthetic" },
      ],
    };

    const { real, simulated } = splitFeed(feed);
    expect(real.map((epic) => epic.subject)).toEqual(["M5", "M6"]);
    expect(simulated.map((epic) => epic.subject)).toEqual(["Leads"]);
    expect(splitFeed(null)).toEqual({ real: [], simulated: [] });
  });

  it("finds the subjects whose forecast id changed since the last poll", () => {
    const before = { epics: [{ subject: "M5", id: 1 }, { subject: "M6", id: 2 }] };
    const after = {
      epics: [
        { subject: "M5", id: 3 },
        { subject: "M6", id: 2 },
        { subject: "M7", id: 4 },
      ],
    };

    expect(changedIds(before, after)).toEqual(["M5"]);
    expect(changedIds(null, after)).toEqual([]);
  });

  it("knows whether there are any forecasts", () => {
    expect(hasForecasts({ epics: [], sprint: null })).toBe(false);
    expect(hasForecasts({ epics: [], sprint: { id: 1 } })).toBe(true);
    expect(hasForecasts(null)).toBe(false);
  });

  it("plots the trail's P50 and P85 as day numbers against local times", () => {
    const data = trailChartData([
      { at: "2026-10-06T09:00:00", p50: "2026-10-12", p85: "2026-10-15" },
      { at: "2026-10-06T09:14:00", p50: "2026-10-13", p85: null },
    ]);

    expect(data.labels).toEqual([localTime(9, 0), localTime(9, 14)]);
    expect(data.datasets.map((set) => set.label)).toEqual(["P50", "P85"]);
    expect(data.datasets[0].data).toEqual([dayNumber("2026-10-12"), dayNumber("2026-10-13")]);
    expect(data.datasets[1].data).toEqual([dayNumber("2026-10-15"), null]);
  });
});
