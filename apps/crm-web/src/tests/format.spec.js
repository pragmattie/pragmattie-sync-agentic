import { describe, expect, it } from "vitest";
import { money, moneyFull, monthLabel, percent, shortDate } from "../format";

describe("money", () => {
  it("formats compact dollars with at most one decimal", () => {
    expect(money(1234500)).toBe("$1.2M");
    expect(money(12500)).toBe("$12.5K");
    expect(money(2000000)).toBe("$2M");
    expect(money(950)).toBe("$950");
  });

  it("accepts strings and treats null as 0", () => {
    expect(money("12500.00")).toBe("$12.5K");
    expect(money(null)).toBe("$0");
  });
});

describe("moneyFull", () => {
  it("formats whole dollars", () => {
    expect(moneyFull(1234500)).toBe("$1,234,500");
    expect(moneyFull(1234.6)).toBe("$1,235");
  });

  it("accepts strings and treats null as 0", () => {
    expect(moneyFull("12500.00")).toBe("$12,500");
    expect(moneyFull(null)).toBe("$0");
  });
});

describe("percent", () => {
  it("rounds and adds %", () => {
    expect(percent(42.6)).toBe("43%");
    expect(percent("25")).toBe("25%");
    expect(percent(null)).toBe("0%");
  });
});

describe("shortDate", () => {
  it("reads the calendar date without a time-zone shift", () => {
    expect(shortDate("2026-09-30")).toBe("Sep 30, 2026");
    expect(shortDate("2026-01-01")).toBe("Jan 1, 2026");
    expect(shortDate(null)).toBe("");
  });
});

describe("monthLabel", () => {
  it("formats a year-month", () => {
    expect(monthLabel("2026-09")).toBe("Sep 2026");
    expect(monthLabel("2027-01")).toBe("Jan 2027");
  });
});
