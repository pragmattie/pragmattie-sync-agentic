// Pure helpers behind the Prediction accuracy page, kept apart from the view so
// they can be unit-tested.

import { formatDay } from "./forecast";
import { MISSING } from "./signals";

export const COLORS = { p85: "#2E3F55", p50: "#1B8A94", good: "#1B8A94", bad: "#E8B35A" };

export const FORECAST_NOTE =
  "A well-calibrated P85 is met about 85% of the time and a P50 about half the time. " +
  "The chips show how far each sprint's finish landed from its P50: early sprints had " +
  "little history behind them, so their forecasts were far too cautious, and the error " +
  "shrinks as history builds up. Simulated history.";
export const TRIAGE_NOTE =
  "Real issues only. Kept labels can echo labels an issue already had, so the stricter " +
  "test is the blind evaluation against your own labels (`python -m sdlc.eval`).";
export const SELECTOR_NOTE =
  "Real PRs only. CI still runs every job, so each commit shows whether skipping would " +
  "have been safe. A miss is a job it would have skipped that failed.";
export const MILESTONE_NOTE =
  "Real work only. A milestone is graded once its work is finished, against the first " +
  "forecast saved for it; a forecast made after its work had started closing isn't " +
  "graded. The forecasts use the agents' measured pace, which leaves out the time people " +
  "spend on specs, plan approvals and gates.";

// The report holds rates as fractions; the page shows whole percentages.
export function pct(fraction) {
  if (fraction === null || fraction === undefined) return MISSING;
  return `${Math.round(fraction * 100)}%`;
}

function plural(count, one, many = `${one}s`) {
  return `${count} ${count === 1 ? one : many}`;
}

// "Sprint 12" → "S12"; any other name is left as it is.
export function shortSprint(name) {
  const match = /^Sprint\s+(\d+)$/.exec(name ?? "");
  return match ? `S${match[1]}` : name;
}

// A real trend is "empty" with nothing in it, "enough" for a chart at minPoints, else "thin".
export function realState(trend, minPoints) {
  const total = trend?.total ?? 0;
  if (!total) return "empty";
  return total >= minPoints ? "enough" : "thin";
}

// Working days a finish landed from its P50: "2d late", "on P50" or "1d early".
export function errorText(days) {
  if (days === null || days === undefined) return MISSING;
  if (days === 0) return "on P50";
  return days > 0 ? `${days}d late` : `${-days}d early`;
}

export function headlineTiles(report) {
  const { forecast, risk, triage, test_selector: selector, milestones } = report;
  return [
    {
      key: "forecast",
      title: "Forecasts met by P85",
      simulated: true,
      value: pct(forecast.p85_hit_rate),
      target: `target ${pct(0.85)}`,
      detail: `${forecast.graded} sprints backtested`,
    },
    {
      key: "risk",
      title: "Incident PRs flagged T2+",
      simulated: true,
      value: pct(risk.recall),
      target: `${risk.caught} of ${risk.incident_prs}`,
      detail: `${plural(risk.t0_incidents, "incident PR")} scored T0`,
    },
    {
      key: "triage",
      title: "Triage labels kept",
      simulated: false,
      value: pct(triage.kept_rate),
      target: `${triage.corrected} corrected`,
      detail: `${plural(triage.total, "real issue")} labelled`,
    },
    {
      key: "selector",
      title: "Test selector misses",
      simulated: false,
      value: selector.total ? String(selector.missed) : MISSING,
      target: `of ${plural(selector.total, "real commit")}`,
      detail: "a job it would have skipped failed",
    },
    {
      key: "milestones",
      title: "Milestones met by P85",
      simulated: false,
      value: pct(milestones.p85_hit_rate),
      target: `target ${pct(0.85)}`,
      detail: `${plural(milestones.total, "real milestone")} graded`,
    },
  ];
}

function percentages(rows, key) {
  return rows.map((row) => (row[key] === null ? null : Math.round(row[key] * 100)));
}

export function forecastChartData(forecast) {
  const rows = forecast?.sprints ?? [];
  const targets = forecast?.targets ?? {};
  const line = (label, data, color, dashed = false) => ({
    label,
    data,
    borderColor: color,
    backgroundColor: color,
    borderDash: dashed ? [6, 4] : [],
    borderWidth: dashed ? 1 : 2,
    pointRadius: dashed ? 0 : 2,
    tension: 0,
  });
  const flat = (rate) => rows.map(() => Math.round((rate ?? 0) * 100));
  return {
    labels: rows.map((row) => shortSprint(row.sprint)),
    datasets: [
      line("Met by P85 (so far)", percentages(rows, "p85_hit_rate_so_far"), COLORS.p85),
      line("Met by P50 (so far)", percentages(rows, "p50_hit_rate_so_far"), COLORS.p50),
      line("P85 target", flat(targets.p85_hit_rate), COLORS.p85, true),
      line("P50 target", flat(targets.p50_hit_rate), COLORS.p50, true),
    ],
  };
}

export function sprintChips(forecast) {
  return (forecast?.sprints ?? []).map((row) => ({
    key: row.sprint,
    text: `${shortSprint(row.sprint)} · ${row.actual ? errorText(row.p50_error_days) : "unfinished"}`,
    color: row.p85_met ? undefined : "warning",
    tooltip:
      `Forecast ${formatDay(row.p50)} (P50), ${formatDay(row.p85)} (P85); ` +
      `finished ${row.actual ? formatDay(row.actual) : "not yet"}`,
  }));
}

function stacked(labels, good, bad) {
  return {
    labels,
    datasets: [
      { label: good.label, data: good.data, backgroundColor: COLORS.good },
      { label: bad.label, data: bad.data, backgroundColor: COLORS.bad },
    ],
  };
}

export function riskChartData(risk) {
  const rows = risk?.sprints ?? [];
  return stacked(
    rows.map((row) => shortSprint(row.sprint)),
    { label: "Flagged T2+", data: rows.map((row) => row.caught) },
    { label: "Missed", data: rows.map((row) => row.incident_prs - row.caught) },
  );
}

export function riskNote(risk) {
  const real = risk.real ?? { merged_prs: 0, incident_prs: 0 };
  const base =
    `${risk.caught} of ${risk.incident_prs} incident PRs flagged (${pct(risk.recall)}); ` +
    `${risk.t0_incidents} scored T0. Only a few incidents happen per sprint, so read the ` +
    `total, not one sprint. Simulated history. Real PRs so far: ${real.merged_prs} merged, ` +
    `${real.incident_prs} caused an incident`;
  return real.incident_prs ? `${base}.` : `${base}, so nothing real to grade yet.`;
}

export function triageChartData(triage) {
  const rows = triage?.weeks ?? [];
  return stacked(
    rows.map((row) => formatDay(row.week)),
    { label: "Kept", data: rows.map((row) => row.labelled - row.corrected) },
    { label: "Corrected by a person", data: rows.map((row) => row.corrected) },
  );
}

export function selectorChartData(selector) {
  const rows = selector?.weeks ?? [];
  return stacked(
    rows.map((row) => formatDay(row.week)),
    { label: "Safe to skip", data: rows.map((row) => row.settled - row.missed) },
    { label: "Missed", data: rows.map((row) => row.missed) },
  );
}

export function triageThin(triage, minPoints) {
  return (
    `${plural(triage.total, "real issue")} labelled so far: ` +
    `the trend appears at ${minPoints}.`
  );
}

export function selectorThin(selector, minPoints) {
  return (
    `${plural(selector.total, "real commit")} settled so far: ` +
    `the trend appears at ${minPoints}.`
  );
}

function yesNo(value) {
  if (value === null || value === undefined) return MISSING;
  return value ? "yes" : "no";
}

export function milestoneRows(milestones) {
  return (milestones?.milestones ?? []).map((row) => ({
    key: row.milestone,
    label: row.label,
    firstForecast: formatDay(row.first_forecast_at?.slice(0, 10)),
    graded: row.graded,
    reason: row.reason,
    p50: formatDay(row.p50),
    p85: formatDay(row.p85),
    finished: formatDay(row.actual),
    error: errorText(row.p50_error_days),
    targetMet: yesNo(row.target_met),
  }));
}
