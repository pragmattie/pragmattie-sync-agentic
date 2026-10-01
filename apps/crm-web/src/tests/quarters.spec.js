import { describe, expect, it } from "vitest";
import { quarterLabel, quarterOf, quarterRange, shiftQuarter } from "../quarters";

describe("quarterOf", () => {
  it("reads ISO dates and Dates as calendar quarters", () => {
    expect(quarterOf("2026-09-30")).toEqual({ year: 2026, quarter: 3 });
    expect(quarterOf("2026-10-01")).toEqual({ year: 2026, quarter: 4 });
    expect(quarterOf("2027-01-01")).toEqual({ year: 2027, quarter: 1 });
    expect(quarterOf(new Date(2026, 11, 31))).toEqual({ year: 2026, quarter: 4 });
  });
});

describe("shiftQuarter", () => {
  it("moves across a year boundary in both directions", () => {
    expect(shiftQuarter({ year: 2026, quarter: 4 }, 1)).toEqual({ year: 2027, quarter: 1 });
    expect(shiftQuarter({ year: 2027, quarter: 1 }, -1)).toEqual({ year: 2026, quarter: 4 });
    expect(shiftQuarter({ year: 2026, quarter: 3 }, 6)).toEqual({ year: 2028, quarter: 1 });
    expect(shiftQuarter({ year: 2026, quarter: 2 }, 0)).toEqual({ year: 2026, quarter: 2 });
  });
});

describe("quarterLabel", () => {
  it("labels a quarter", () => {
    expect(quarterLabel({ year: 2026, quarter: 3 })).toBe("2026-Q3");
    expect(quarterLabel(shiftQuarter({ year: 2026, quarter: 4 }, 1))).toBe("2027-Q1");
  });
});

describe("quarterRange", () => {
  it("gives the first and last ISO date", () => {
    expect(quarterRange({ year: 2026, quarter: 3 })).toEqual({
      start: "2026-07-01",
      end: "2026-09-30",
    });
    expect(quarterRange({ year: 2026, quarter: 4 })).toEqual({
      start: "2026-10-01",
      end: "2026-12-31",
    });
    expect(quarterRange({ year: 2027, quarter: 1 })).toEqual({
      start: "2027-01-01",
      end: "2027-03-31",
    });
    expect(quarterRange({ year: 2028, quarter: 1 }).end).toBe("2028-03-31");
  });
});
