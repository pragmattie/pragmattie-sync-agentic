import { describe, expect, it } from "vitest";
import {
  STAGE_COLORS,
  flowChartData,
  flowTiles,
  noRealWork,
  shortDate,
  throughputChartData,
} from "../flow";

const STAGES = ["Backlog", "Triaged", "In progress", "In review", "Merged", "Production"];

describe("flow helpers", () => {
  it("knows when there is no real work", () => {
    expect(noRealWork({ source: "github", available: { github: 0, synthetic: 5 } })).toBe(true);
    expect(noRealWork({ source: "github", available: { github: 2, synthetic: 5 } })).toBe(false);
    expect(noRealWork({ source: "synthetic", available: { github: 0, synthetic: 5 } })).toBe(false);
    expect(noRealWork(null)).toBe(false);
  });

  it("formats calendar dates without shifting the day", () => {
    expect(shortDate("2026-10-02")).toBe("2 Oct");
  });

  it("gives every stage a fixed colour", () => {
    expect(Object.keys(STAGE_COLORS)).toEqual(STAGES);
  });

  it("builds filled bands in stage order, zero for a missing count", () => {
    const data = flowChartData({
      stages: STAGES,
      series: [{ date: "2026-10-01", counts: { Backlog: 2 } }],
    });

    expect(data.labels).toEqual(["1 Oct"]);
    expect(data.datasets.map((set) => set.label)).toEqual(STAGES);
    expect(data.datasets[0].data).toEqual([2]);
    expect(data.datasets[5].data).toEqual([0]);
    expect(data.datasets[0].borderColor).toBe(STAGE_COLORS.Backlog);
  });

  it("charts throughput by week", () => {
    const data = throughputChartData({ throughput: [{ week: "2026-09-28", merged: 3 }] });

    expect(data.labels).toEqual([shortDate("2026-09-28")]);
    expect(data.datasets[0].data).toEqual([3]);
  });

  it("shows a dash for a cycle time it can't compute", () => {
    const tiles = flowTiles({ items: 0, cycle_time_days: { median: null, p85: 1 } });

    expect(tiles.map((tile) => tile.value)).toEqual(["0", "—", "1 day"]);
  });
});
