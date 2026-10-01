import { describe, expect, it } from "vitest";
import { monthStacks, quarterOptions, quotaMeter, stageBars } from "../charts/forecast";

const forecast = {
  quarter: "2026-Q4",
  quota: "1000000.00",
  won: "300000.00",
  commit: "500000.00",
  best_case: "800000.00",
  weighted: "600000.00",
  by_month: [
    { month: "2026-10", won: "100000.00", commit: "150000.00", best_case: "300000.00" },
    { month: "2026-11", won: "200000.00", commit: "250000.00", best_case: "300000.00" },
    { month: "2026-12", won: "0.00", commit: "100000.00", best_case: "200000.00" },
  ],
  by_stage: [
    { stage: "negotiation", count: 4, amount: "200000.00" },
    { stage: "prospecting", count: 12, amount: "900000.00" },
    { stage: "proposal", count: 6, amount: "300000.00" },
    { stage: "qualification", count: 8, amount: "500000.00" },
  ],
};

describe("monthStacks", () => {
  it("splits each month into won, negotiation and proposal", () => {
    expect(monthStacks(forecast)).toEqual({
      labels: ["Oct 2026", "Nov 2026", "Dec 2026"],
      won: [100000, 200000, 0],
      negotiation: [50000, 50000, 100000],
      proposal: [150000, 50000, 100000],
    });
  });

  it("clamps negative segments to zero", () => {
    const stacks = monthStacks({
      by_month: [{ month: "2026-10", won: "100.00", commit: "50.00", best_case: "40.00" }],
    });
    expect(stacks.negotiation).toEqual([0]);
    expect(stacks.proposal).toEqual([0]);
  });
});

describe("stageBars", () => {
  it("labels the open stages with their counts, in pipeline order", () => {
    expect(stageBars(forecast)).toEqual({
      labels: ["Prospecting (12)", "Qualification (8)", "Proposal (6)", "Negotiation (4)"],
      values: [900000, 500000, 300000, 200000],
    });
  });

  it("shows a stage with no deals as zero", () => {
    expect(stageBars({ by_stage: [] }).labels[0]).toBe("Prospecting (0)");
  });
});

describe("quotaMeter", () => {
  it("scales to quota plus 5% when quota is above best case", () => {
    const meter = quotaMeter(forecast);
    const scale = 1000000 * 1.05;
    expect(meter.won).toBeCloseTo((300000 / scale) * 100);
    expect(meter.negotiation).toBeCloseTo((200000 / scale) * 100);
    expect(meter.proposal).toBeCloseTo((300000 / scale) * 100);
    expect(meter.quota).toBeCloseTo(100 / 1.05);
  });

  it("scales to best case plus 5% when best case is above quota", () => {
    const meter = quotaMeter({ ...forecast, quota: "400000.00" });
    const scale = 800000 * 1.05;
    expect(meter.won + meter.negotiation + meter.proposal).toBeCloseTo(100 / 1.05);
    expect(meter.quota).toBeCloseTo((400000 / scale) * 100);
  });

  it("scales to best case, not the sum of clamped segments, when data is inconsistent", () => {
    // Commit below won: the clamped segments add up to 300k + 0 + 600k = 900k,
    // but the scale still runs from best case (800k) plus 5%.
    const meter = quotaMeter({ ...forecast, quota: "400000.00", commit: "200000.00" });
    const scale = 800000 * 1.05;
    expect(meter.won).toBeCloseTo((300000 / scale) * 100);
    expect(meter.negotiation).toBe(0);
    expect(meter.proposal).toBeCloseTo((600000 / scale) * 100);
    expect(meter.quota).toBeCloseTo((400000 / scale) * 100);
  });

  it("is empty when there is nothing to show", () => {
    expect(quotaMeter({ quota: "0", won: "0", commit: "0", best_case: "0" })).toEqual({
      won: 0,
      negotiation: 0,
      proposal: 0,
      quota: 0,
    });
  });
});

describe("quarterOptions", () => {
  it("offers two quarters back, the current one and the next", () => {
    expect(quarterOptions("2026-10-01")).toEqual([
      { value: "2026-Q2", title: "2026-Q2" },
      { value: "2026-Q3", title: "2026-Q3" },
      { value: "2026-Q4", title: "2026-Q4 (current)" },
      { value: "2027-Q1", title: "2027-Q1" },
    ]);
  });

  it("crosses a year boundary backwards", () => {
    expect(quarterOptions("2027-02-15").map((option) => option.value)).toEqual([
      "2026-Q3",
      "2026-Q4",
      "2027-Q1",
      "2027-Q2",
    ]);
  });
});
