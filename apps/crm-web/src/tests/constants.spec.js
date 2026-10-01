import { describe, expect, it } from "vitest";
import {
  FORECAST_COLORS,
  INDUSTRIES,
  LEAD_SOURCES,
  LEAD_STATUSES,
  OPEN_STAGES,
  REGIONS,
  STAGES,
} from "../constants";

describe("constants", () => {
  it("lists the stages in order with default probabilities", () => {
    expect(STAGES.map((s) => [s.title, s.probability])).toEqual([
      ["Prospecting", 10],
      ["Qualification", 25],
      ["Proposal", 50],
      ["Negotiation", 75],
      ["Closed won", 100],
      ["Closed lost", 0],
    ]);
    expect(OPEN_STAGES).toEqual(["prospecting", "qualification", "proposal", "negotiation"]);
  });

  it("lists the lead statuses with their titles and colours", () => {
    expect(LEAD_STATUSES.map((s) => [s.title, s.color])).toEqual([
      ["New", "info"],
      ["Working", "secondary"],
      ["Qualified", "success"],
      ["Disqualified", "grey"],
      ["Converted", "primary"],
    ]);
  });

  it("gives every lead status a colour and an mdi icon", () => {
    for (const status of LEAD_STATUSES) {
      expect(status.color).toBeTruthy();
      expect(status.icon).toMatch(/^mdi-/);
    }
  });

  it("uses one teal ramp for the forecast", () => {
    expect(FORECAST_COLORS).toEqual({
      won: "#0E5A61",
      negotiation: "#1B8A94",
      proposal: "#6FBAC1",
    });
  });

  it("lists the lead sources, industries and regions", () => {
    expect(LEAD_SOURCES.map((s) => s.title)).toEqual([
      "Web",
      "Referral",
      "Event",
      "Outbound",
      "Partner",
    ]);
    expect(INDUSTRIES).toEqual([
      "Energy",
      "Financial Services",
      "Healthcare",
      "Life Sciences",
      "Manufacturing",
      "Professional Services",
      "Retail",
      "Technology",
      "Telecommunications",
      "Transportation",
    ]);
    expect(REGIONS).toEqual([
      "North America East",
      "North America West",
      "North America Central",
      "EMEA",
      "APAC",
    ]);
  });
});
