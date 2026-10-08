// Pure calculations behind the Delivery flow page, kept apart from the view so
// they can be unit-tested.

import { MISSING } from "./signals";

export const DAY_OPTIONS = [30, 60, 90, 180];
export const DEFAULT_DAYS = 60;

export const SOURCES = [
  { value: "github", title: "Real work" },
  { value: "synthetic", title: "Simulated history" },
];

// One fixed colour a stage, from the theme's navy, teal and gold: early stages
// pale, finished work dark, so the bands read bottom to top as work moves on.
export const STAGE_COLORS = {
  Backlog: "#C5CDD8",
  Triaged: "#8796AA",
  "In progress": "#8CCBD1",
  "In review": "#1B8A94",
  Merged: "#E8B35A",
  Production: "#2E3F55",
};

export const REAL_NOTE = "Work in this repository, built by the agents.";
export const SIMULATED_CHIP = "Simulated history · calibration data";
export const SIMULATED_NOTE =
  "Generated history used to test the forecasting and risk models; not a real team.";
export const NO_REAL_WORK =
  "No real work recorded here yet. It appears once this system collects its own repository " +
  "(rebuild plan 7.1) or imports v1's record of the rebuild (7.4).";

// Whether the page should show the empty Real state instead of a chart.
export function noRealWork(result) {
  return result?.source === "github" && (result.available?.github ?? 0) === 0;
}

// "2026-10-02" → "2 Oct", read as a calendar day rather than a UTC instant.
export function shortDate(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
  });
}

function withAlpha(hex, alpha) {
  const value = Math.round(alpha * 255)
    .toString(16)
    .padStart(2, "0");
  return `${hex}${value}`;
}

// One stacked, filled band per stage, in stage order (Backlog at the bottom).
export function flowChartData(result) {
  const series = result?.series ?? [];
  const stages = result?.stages ?? [];
  return {
    labels: series.map((row) => shortDate(row.date)),
    datasets: stages.map((stage, index) => ({
      label: stage,
      data: series.map((row) => row.counts?.[stage] ?? 0),
      borderColor: STAGE_COLORS[stage],
      backgroundColor: withAlpha(STAGE_COLORS[stage], 0.85),
      fill: index === 0 ? "origin" : "-1",
      pointRadius: 0,
      borderWidth: 1,
      tension: 0,
    })),
  };
}

export function throughputChartData(result) {
  const weeks = result?.throughput ?? [];
  return {
    labels: weeks.map((row) => shortDate(row.week)),
    datasets: [
      {
        label: "Merged",
        backgroundColor: STAGE_COLORS.Merged,
        data: weeks.map((row) => row.merged),
      },
    ],
  };
}

function days(value) {
  if (value === null || value === undefined) return MISSING;
  return `${value} ${value === 1 ? "day" : "days"}`;
}

export function flowTiles(result) {
  const cycle = result?.cycle_time_days ?? {};
  return [
    { key: "items", title: "Items", value: String(result?.items ?? 0) },
    { key: "median", title: "Cycle time, median", value: days(cycle.median) },
    { key: "p85", title: "Cycle time, 85th percentile", value: days(cycle.p85) },
  ];
}
