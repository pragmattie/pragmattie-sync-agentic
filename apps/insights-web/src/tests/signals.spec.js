import { describe, expect, it } from "vitest";
import {
  delta,
  doraTiles,
  historyFigures,
  isFlaky,
  median,
  moduleName,
  moduleRows,
  overEstimate,
  percent,
  pooledBarRows,
  pooledNote,
  realCountNote,
  sourceNote,
  sprintLabels,
  thresholdRows,
  tierRows,
  verdict,
} from "../signals";

describe("delta", () => {
  it("colours a rise success when up is better", () => {
    expect(delta(4.5, 3, "up", " per week")).toEqual({
      text: "+1.5 vs prior 30 days",
      direction: "up",
      color: "success",
    });
  });

  it("colours a rise error when down is better", () => {
    expect(delta(20, 18.5, "down", "h")).toEqual({
      text: "+1.5h vs prior 30 days",
      direction: "up",
      color: "error",
    });
  });

  it("colours a fall success when down is better", () => {
    expect(delta(12.2, 14, "down", "h")).toEqual({
      text: "-1.8h vs prior 30 days",
      direction: "down",
      color: "success",
    });
  });

  it("colours a fall error when up is better", () => {
    expect(delta(2, 3, "up")).toMatchObject({ direction: "down", color: "error" });
  });

  it("shows no colour when unchanged", () => {
    expect(delta(5.02, 5, "down", "%")).toEqual({
      text: "0.0% vs prior 30 days",
      direction: "flat",
      color: null,
    });
  });

  it("is left out when a value is missing", () => {
    expect(delta(null, 3, "up")).toBeNull();
    expect(delta(3, null, "up")).toBeNull();
    expect(delta(3, undefined, "up")).toBeNull();
  });

  it("is left out when the previous value is 0", () => {
    expect(delta(3, 0, "down", "%")).toBeNull();
  });

  it("names the period length", () => {
    expect(delta(2, 1, "up", "h", 14).text).toBe("+1.0h vs prior 14 days");
  });
});

describe("doraTiles", () => {
  it("formats the four measures and shows a dash for missing values", () => {
    const tiles = doraTiles({
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
        time_to_restore_hours: null,
      },
    });

    expect(tiles.map((tile) => tile.title)).toEqual([
      "Deployment frequency",
      "Lead time for changes",
      "Change failure rate",
      "Time to restore",
    ]);
    expect(tiles.map((tile) => tile.value)).toEqual(["3.5 per week", "20h", "—", "2.5h"]);
    expect(tiles.map((tile) => tile.note)).toEqual([
      "",
      "PR opened → merged, median",
      "",
      "median",
    ]);
    expect(tiles[1].delta.text).toBe("+1.5h vs prior 30 days");
    expect(tiles[2].delta).toBeNull();
    expect(tiles[3].delta).toBeNull();
  });
});

describe("sourceNote", () => {
  it("counts simulated and real PRs", () => {
    expect(sourceNote({ pull_requests: { synthetic: 412, github: 37 } })).toBe(
      "412 simulated PRs of history plus 37 real PRs collected from GitHub.",
    );
  });

  it("says real activity is still to come", () => {
    expect(sourceNote({ pull_requests: { synthetic: 412, github: 0 } })).toBe(
      "412 simulated PRs of history. Real GitHub activity appears here once the collector runs.",
    );
  });

  it("says when there is no simulated history", () => {
    expect(sourceNote({ pull_requests: { synthetic: 0, github: 37 } })).toBe(
      "37 real PRs collected from GitHub; no simulated history.",
    );
  });
});

describe("sprintLabels", () => {
  it("numbers the sprints and stars the one in progress", () => {
    const rows = [{ in_progress: false }, { in_progress: false }, { in_progress: true }];

    expect(sprintLabels(rows)).toEqual(["S1", "S2", "S3*"]);
  });
});

describe("median and over-estimate", () => {
  it("takes the middle value, or the mean of the two middle values", () => {
    expect(median([3, 1, 2])).toBe(2);
    expect(median([4, 1, 3, 2])).toBe(2.5);
    expect(median([1, null, 3])).toBe(2);
    expect(median([])).toBeNull();
  });

  it("flags more than 1.3× the median", () => {
    expect(overEstimate(1.31, 1)).toBe(true);
    expect(overEstimate(1.3, 1)).toBe(false);
    expect(overEstimate(null, 1)).toBe(false);
    expect(overEstimate(2, null)).toBe(false);
  });

  it("prepares module rows", () => {
    const rows = moduleRows([
      { module: "billing_auth", incident_rate: 8, days_per_point: 1.4 },
      { module: "leads", incident_rate: 2, days_per_point: 0.9 },
      { module: "platform", incident_rate: 0, days_per_point: 1 },
    ]);

    expect(rows.map((row) => row.name)).toEqual(["Billing & Auth", "Leads", "Platform"]);
    expect(rows.map((row) => row.barWidth)).toEqual([100, 25, 0]);
    expect(rows.map((row) => row.overEstimate)).toEqual([true, false, false]);
  });
});

describe("names and flags", () => {
  it("names every module", () => {
    expect(moduleName("orchestrator")).toBe("Orchestrator");
    expect(moduleName("unknown")).toBe("unknown");
  });

  it("flags a flaky rate of 3% or more", () => {
    expect(isFlaky(3)).toBe(true);
    expect(isFlaky(2.9)).toBe(false);
    expect(isFlaky(null)).toBe(false);
  });
});

const POOLED = {
  histories: 30,
  merged_prs: 15044,
  incident_prs: 451,
  overall_rate: 0.03,
  t0_rate: 0.0018,
  top_decile: { capture: 0.5787, lowest: 0.375, highest: 0.8 },
  bars: { t0_rate_at_most_a_quarter_of_overall: true, top_decile_captures_majority_pooled: true },
};

const POOLED_SENTENCE =
  "The risk score passes both bars across 30 generated histories: T0's incident rate 0.18% " +
  "against 3.00% overall, and the top tenth by score catches 57.9% of incident PRs.";

describe("percent", () => {
  it("formats a ratio with one decimal place by default", () => {
    expect(percent(0.6)).toBe("60.0%");
    expect(percent(1 / 3)).toBe("33.3%");
    expect(percent(0)).toBe("0.0%");
  });

  it("takes the number of places", () => {
    expect(percent(0.0018, 2)).toBe("0.18%");
  });

  it("shows a dash when there is nothing to divide", () => {
    expect(percent(null)).toBe("—");
    expect(percent(undefined)).toBe("—");
  });
});

describe("calibration rows", () => {
  const report = {
    by_tier: { T0: { prs: 7, incidents: 1 }, T1: { prs: 4, incidents: 2 } },
    thresholds: {
      T1: { flagged: 5, incidents: 3, precision: 0.6, recall: 0.75 },
      T2: { flagged: 0, incidents: 0, precision: null, recall: 0 },
    },
  };

  it("gives each threshold with percentages", () => {
    expect(thresholdRows(report)).toEqual([
      { tier: "T1+", flagged: 5, incidents: 3, precision: "60.0%", recall: "75.0%" },
      { tier: "T2+", flagged: 0, incidents: 0, precision: "—", recall: "0.0%" },
      { tier: "T3+", flagged: 0, incidents: 0, precision: "—", recall: "—" },
    ]);
  });

  it("gives every tier, zero when it has no PRs", () => {
    expect(tierRows(report)).toEqual([
      { tier: "T0", prs: 7, incidents: 1 },
      { tier: "T1", prs: 4, incidents: 2 },
      { tier: "T2", prs: 0, incidents: 0 },
      { tier: "T3", prs: 0, incidents: 0 },
    ]);
  });

  it("says how many real PRs there are", () => {
    expect(realCountNote({ real_merged_prs: 12, real_incident_prs: 0 })).toBe(
      "The risk score is graded on simulated history, because real changes haven't caused " +
        "incidents yet. 12 real PRs merged so far, 0 caused an incident.",
    );
  });
});

describe("bars", () => {
  it("words each pooled bar PASS or FAIL", () => {
    expect(verdict(true)).toBe("PASS");
    expect(verdict(false)).toBe("FAIL");
    const rows = pooledBarRows({
      bars: {
        t0_rate_at_most_a_quarter_of_overall: false,
        top_decile_captures_majority_pooled: true,
      },
    });
    expect(rows.map((row) => [row.key, row.verdict])).toEqual([
      ["t0_rate_at_most_a_quarter_of_overall", "FAIL"],
      ["top_decile_captures_majority_pooled", "PASS"],
    ]);
  });

  it("gives the verdict from the pooled grading", () => {
    expect(pooledNote(POOLED)).toBe(POOLED_SENTENCE);
  });

  it("gives this history's figures with no verdict", () => {
    const figures = historyFigures({
      merged_prs: 400,
      incident_prs: 12,
      by_tier: { T0: { prs: 200, incidents: 1 } },
      top_decile: { capture: 0.25 },
      bars: { t0_has_no_incidents: false, top_decile_captures_majority: false },
    });
    expect(figures.map((figure) => figure.label)).toEqual([
      "T0's incident rate 0.50% against 3.00% overall",
      "The top tenth by score catches 25.0% of incident PRs",
    ]);
  });

  it("shows dashes for a history with no PRs", () => {
    const labels = historyFigures({}).map((figure) => figure.label);
    expect(labels[0]).toBe("T0's incident rate — against — overall");
  });
});
