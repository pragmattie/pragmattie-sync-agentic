import { describe, expect, it } from "vitest";
import {
  AGENT_LABELS,
  agentLabel,
  decisionCost,
  filtersFromQuery,
  formatCost,
  formatTime,
  kindOf,
  listParams,
  parseUtc,
  prettyJson,
  signalRows,
  STATUS_OPTIONS,
  statusColor,
  subjectLabel,
  TIME_FORMAT,
  totalsLine,
} from "../decisions";

describe("agentLabel", () => {
  it.each([
    ["pr_risk", "PR risk"],
    ["triage", "Triage"],
    ["tier_override", "Tier override"],
    ["objection_window", "Objection window"],
    ["implementer", "Implementer"],
    ["reviewer", "Reviewer"],
    ["pr_risk_rubric", "Risk rubric (manual)"],
    ["forecaster", "Forecaster"],
    ["planner", "Planner"],
    ["test_selector", "Test selector"],
    ["release_gate", "Release gate"],
  ])("names %s %s", (agent, label) => {
    expect(agentLabel(agent)).toBe(label);
  });

  it("covers every agent in the spec", () => {
    expect(Object.keys(AGENT_LABELS)).toHaveLength(11);
  });

  it("falls back to the raw name", () => {
    expect(agentLabel("new_agent")).toBe("new_agent");
  });
});

describe("subjectLabel", () => {
  it("names pull requests and issues by number", () => {
    expect(subjectLabel({ subject_type: "pr", subject_id: 42 })).toBe("PR #42");
    expect(subjectLabel({ subject_type: "issue", subject_id: 7 })).toBe("Issue #7");
  });

  it("names forecasts by the subject in their output", () => {
    const output = { subject: "Sprint 4" };
    expect(subjectLabel({ subject_type: "sprint", subject_id: 4, output })).toBe(
      "Sprint: Sprint 4",
    );
    expect(
      subjectLabel({ subject_type: "epic", subject_id: 2, output: { subject: "Pipeline" } }),
    ).toBe("Epic: Pipeline");
  });

  it("names a release by the last pull request in it", () => {
    expect(subjectLabel({ subject_type: "release", subject_id: 120 })).toBe(
      "Release up to PR #120",
    );
  });
});

describe("kindOf", () => {
  const sha = "0123456789abcdef0123456789abcdef01234567";

  it.each([
    ["run-1234", "agent run"],
    ["review-99", "review"],
    ["window-abc", "window sign-off"],
    ["comment-555", "a person's comment"],
  ])("reads %s as %s", (head_sha, kind) => {
    expect(kindOf({ subject_type: "pr", head_sha })).toBe(kind);
  });

  it("shows a pull request's commit", () => {
    expect(kindOf({ subject_type: "pr", head_sha: sha })).toBe("commit 0123456");
  });

  it("shows an issue's version", () => {
    expect(kindOf({ subject_type: "issue", head_sha: sha })).toBe("version 0123456");
  });

  it("is blank without a head_sha", () => {
    expect(kindOf({ subject_type: "sprint", head_sha: null })).toBe("");
  });
});

describe("statusColor", () => {
  it.each([
    ["ok", "success"],
    ["rejected", "warning"],
    ["missed", "warning"],
    ["error", "error"],
    ["timeout", "error"],
    ["rate_limited", "error"],
    ["refused", "error"],
    ["invalid_output", "error"],
  ])("colours %s %s", (status, color) => {
    expect(statusColor(status)).toBe(color);
  });

  it("offers every status as its own filter option", () => {
    expect(STATUS_OPTIONS).toEqual([
      "ok",
      "error",
      "timeout",
      "rate_limited",
      "refused",
      "invalid_output",
      "rejected",
      "missed",
    ]);
  });
});

describe("formatCost", () => {
  it("shows four decimals below a dollar", () => {
    expect(formatCost(0.0123)).toBe("$0.0123");
    expect(formatCost(0)).toBe("$0.0000");
  });

  it("shows cents from a dollar up", () => {
    expect(formatCost(1.234)).toBe("$1.23");
    expect(formatCost(12)).toBe("$12.00");
  });

  it("shows a dash when missing", () => {
    expect(formatCost(null)).toBe("—");
    expect(formatCost(undefined)).toBe("—");
  });
});

describe("formatTime", () => {
  it("reads a stored time without a zone as UTC", () => {
    expect(parseUtc("2026-10-07T13:45:00").toISOString()).toBe("2026-10-07T13:45:00.000Z");
    expect(parseUtc("2026-10-07T13:45:00+00:00").toISOString()).toBe("2026-10-07T13:45:00.000Z");
  });

  it("shows the viewer's local date and time", () => {
    const expected = new Date(Date.UTC(2026, 9, 7, 13, 45)).toLocaleString(undefined, TIME_FORMAT);
    expect(formatTime("2026-10-07T13:45:00")).toBe(expected);
  });

  it("shows a dash when missing", () => {
    expect(formatTime(null)).toBe("—");
  });
});

describe("decisionCost", () => {
  it("reads the cost from the output", () => {
    expect(decisionCost({ output: { cost_usd: 0.02 } })).toBe(0.02);
    expect(decisionCost({ output: {} })).toBeNull();
    expect(decisionCost({ output: null })).toBeNull();
  });
});

describe("totalsLine", () => {
  const totals = {
    runs: 1234,
    input_tokens: 500000,
    output_tokens: 20000,
    cost_usd: 12.3456,
    runs_without_cost: 0,
  };

  it("sums the filtered set", () => {
    expect(totalsLine(totals, 1234)).toBe(
      "1,234 decisions · 1,234 runs cost $12.35 · 500,000 in / 20,000 out tokens",
    );
  });

  it("counts rows without a recorded cost", () => {
    expect(totalsLine({ ...totals, runs: 1, runs_without_cost: 3 }, 1)).toBe(
      "1 decision · 1 run cost $12.35 · 500,000 in / 20,000 out tokens · " +
        "3 without a recorded cost",
    );
  });
});

describe("filtersFromQuery and listParams", () => {
  it("keeps known filters and the page", () => {
    expect(
      filtersFromQuery({ agent: "triage", source: "simulated", page: "3", other: "x" }),
    ).toEqual({ filters: { agent: "triage", source: "simulated" }, page: 3 });
  });

  it("drops an unknown source and a bad page", () => {
    expect(filtersFromQuery({ source: "all", page: "0" })).toEqual({ filters: {}, page: 1 });
  });

  it("maps filters to the orchestrator's parameters and pages by 50", () => {
    expect(
      listParams({ agent: "pr_risk", subject: "pr", status: "ok", tier: "T2", source: "real" }, 3),
    ).toEqual({
      agent: "pr_risk",
      subject_type: "pr",
      status: "ok",
      tier: "T2",
      subject_source: "github",
      limit: 50,
      offset: 100,
    });
    expect(listParams({ source: "simulated" }, 1)).toMatchObject({
      subject_source: "synthetic",
      offset: 0,
    });
  });
});

describe("signalRows and prettyJson", () => {
  it("lists each signal's points", () => {
    expect(signalRows({ change_size: 10, timing: 0 })).toEqual([
      { name: "change_size", points: 10 },
      { name: "timing", points: 0 },
    ]);
    expect(signalRows(null)).toEqual([]);
  });

  it("indents JSON", () => {
    expect(prettyJson({ a: 1 })).toBe('{\n  "a": 1\n}');
  });
});
