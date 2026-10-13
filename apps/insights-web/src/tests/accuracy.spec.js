import { describe, expect, it } from "vitest";
import {
  errorText,
  forecastChartData,
  headlineTiles,
  milestoneRows,
  pct,
  realState,
  riskChartData,
  riskNote,
  selectorChartData,
  selectorThin,
  shortSprint,
  sprintChips,
  triageChartData,
  triageThin,
} from "../accuracy";
import { accuracyReport } from "./accuracyReport";

describe("pct", () => {
  it("rounds a fraction to a whole percentage, and shows a dash for none", () => {
    expect(pct(0.833)).toBe("83%");
    expect(pct(0)).toBe("0%");
    expect(pct(1)).toBe("100%");
    expect(pct(null)).toBe("—");
    expect(pct(undefined)).toBe("—");
  });
});

describe("shortSprint", () => {
  it("shortens sprint names and leaves anything else alone", () => {
    expect(shortSprint("Sprint 12")).toBe("S12");
    expect(shortSprint("Sprint 3")).toBe("S3");
    expect(shortSprint("Kickoff")).toBe("Kickoff");
  });
});

describe("realState", () => {
  it("is empty with nothing, thin below the minimum and enough at it", () => {
    expect(realState({ total: 0 }, 5)).toBe("empty");
    expect(realState({ total: 3 }, 5)).toBe("thin");
    expect(realState({ total: 5 }, 5)).toBe("enough");
    expect(realState(undefined, 5)).toBe("empty");
  });
});

describe("errorText", () => {
  it("words a finish late, early or on its P50", () => {
    expect(errorText(2)).toBe("2d late");
    expect(errorText(0)).toBe("on P50");
    expect(errorText(-1)).toBe("1d early");
    expect(errorText(null)).toBe("—");
  });
});

describe("headlineTiles", () => {
  it("builds the five tiles with their values, flags and target lines", () => {
    const tiles = headlineTiles(accuracyReport());

    expect(tiles.map((tile) => [tile.title, tile.simulated, tile.value])).toEqual([
      ["Forecasts met by P85", true, "33%"],
      ["Incident PRs flagged T2+", true, "80%"],
      ["Triage labels kept", false, "67%"],
      ["Test selector misses", false, "1"],
      ["Milestones met by P85", false, "100%"],
    ]);
    expect(tiles.map((tile) => tile.target)).toEqual([
      "target 85%",
      "12 of 15",
      "1 corrected",
      "of 6 real commits",
      "target 85%",
    ]);
    expect(tiles.map((tile) => tile.detail)).toEqual([
      "3 sprints backtested",
      "2 incident PRs scored T0",
      "3 real issues labelled",
      "a job it would have skipped failed",
      "1 real milestone graded",
    ]);
  });

  it("shows dashes when nothing real has been graded", () => {
    const report = accuracyReport({
      triage: { weeks: [], total: 0, corrected: 0, kept_rate: null },
      test_selector: { weeks: [], total: 0, missed: 0, miss_rate: null },
      milestones: { milestones: [], total: 0, p50_hit_rate: null, p85_hit_rate: null },
    });

    const values = headlineTiles(report).map((tile) => tile.value);

    expect(values.slice(2)).toEqual(["—", "—", "—"]);
  });
});

describe("forecastChartData", () => {
  it("plots the running hit rates as percentages, with flat targets", () => {
    const data = forecastChartData(accuracyReport().forecast);

    expect(data.labels).toEqual(["S11", "S12", "S13"]);
    const [p85, p50, p85Target, p50Target] = data.datasets;
    expect(p85.label).toBe("Met by P85 (so far)");
    expect(p85.data).toEqual([0, 50, 33]);
    expect(p50.label).toBe("Met by P50 (so far)");
    expect(p85Target.data).toEqual([85, 85, 85]);
    expect(p50Target.data).toEqual([50, 50, 50]);
    expect(p85Target.borderDash.length).toBeGreaterThan(0);
    expect(p85.borderDash).toEqual([]);
  });
});

describe("sprintChips", () => {
  it("says how far each finish landed from P50, warning when P85 was missed", () => {
    const chips = sprintChips(accuracyReport().forecast);

    expect(chips.map((chip) => chip.text)).toEqual([
      "S11 · 4d late",
      "S12 · on P50",
      "S13 · unfinished",
    ]);
    expect(chips.map((chip) => chip.color)).toEqual(["warning", undefined, "warning"]);
    expect(chips[0].tooltip).toBe("Forecast Sep 18 (P50), Sep 22 (P85); finished Sep 24");
    expect(chips[2].tooltip).toBe("Forecast Oct 12 (P50), Oct 14 (P85); finished not yet");
  });
});

describe("risk", () => {
  it("stacks flagged and missed incident PRs by sprint", () => {
    const data = riskChartData(accuracyReport().risk);

    expect(data.labels).toEqual(["S11", "S12"]);
    expect(data.datasets.map((set) => [set.label, set.data])).toEqual([
      ["Flagged T2+", [6, 6]],
      ["Missed", [1, 2]],
    ]);
  });

  it("says there is nothing real to grade only when no real PR caused an incident", () => {
    const { risk } = accuracyReport();

    expect(riskNote(risk)).toBe(
      "12 of 15 incident PRs flagged (80%); 2 scored T0. Only a few incidents happen per " +
        "sprint, so read the total, not one sprint. Simulated history. Real PRs so far: " +
        "40 merged, 0 caused an incident, so nothing real to grade yet.",
    );
    const graded = riskNote({ ...risk, real: { merged_prs: 40, incident_prs: 2 } });
    expect(graded).toMatch(/40 merged, 2 caused an incident\.$/);
    expect(graded).not.toContain("nothing real to grade yet");
  });
});

describe("real trends", () => {
  it("stacks triage labels kept and corrected by week", () => {
    const data = triageChartData(accuracyReport().triage);

    expect(data.labels).toEqual(["Oct 5"]);
    expect(data.datasets.map((set) => [set.label, set.data])).toEqual([
      ["Kept", [2]],
      ["Corrected by a person", [1]],
    ]);
  });

  it("stacks selector commits safe to skip and missed by week", () => {
    const data = selectorChartData(accuracyReport().test_selector);

    expect(data.labels).toEqual(["Sep 28", "Oct 5"]);
    expect(data.datasets.map((set) => [set.label, set.data])).toEqual([
      ["Safe to skip", [2, 3]],
      ["Missed", [0, 1]],
    ]);
  });

  it("says when a thin trend will appear", () => {
    expect(triageThin({ total: 3 }, 5)).toBe(
      "3 real issues labelled so far: the trend appears at 5.",
    );
    expect(triageThin({ total: 1 }, 5)).toBe("1 real issue labelled so far: the trend appears at 5.");
    expect(selectorThin({ total: 0 }, 5)).toBe(
      "0 real commits settled so far: the trend appears at 5.",
    );
  });
});

describe("milestoneRows", () => {
  it("shows a graded milestone's figures and an ungraded one's reason", () => {
    const [graded, open] = milestoneRows(accuracyReport().milestones);

    expect(graded).toMatchObject({
      label: "Delivery forecasting (M5)",
      firstForecast: "Sep 28",
      p50: "Oct 6",
      p85: "Oct 9",
      finished: "Oct 8",
      error: "2d late",
      targetMet: "yes",
      graded: true,
    });
    expect(open).toMatchObject({ label: "Release gate (M6)", graded: false, reason: "still open" });
    expect(open.targetMet).toBe("—");
  });
});
