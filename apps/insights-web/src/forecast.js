// Pure helpers behind the Delivery forecast page, kept apart from the view so
// they can be unit-tested.

import { parseUtc } from "./decisions";
import { MISSING } from "./signals";

export const POLL_MS = 15000;
export const FLASH_MS = 2000;

export const NO_FORECASTS = "No forecasts yet. The forecaster saves one after each collection.";
export const NO_REAL_MILESTONES = "No real milestones collected yet (see Running beside v1).";
export const SIMULATED_HEADING = "Simulated history (calibration data)";
export const SIMULATED_NOTE =
  "Generated history used to test the forecasting model; not a real team.";
export const PROPOSAL_NOTE = "A proposal only: nothing has changed. A person decides.";
export const OUT_OF_DATE = "Out of date: the forecast has moved since this was drafted";
export const NOT_MEASURABLE = "Not measurable by the simulation";

export const TRAIL_COLORS = { p50: "#1B8A94", p85: "#2E3F55" };

// The feed holds probabilities as fractions; the page shows whole percentages.
export function percent(fraction) {
  return Math.round(fraction * 100);
}

export function chanceColor(percentage) {
  if (percentage >= 85) return "success";
  if (percentage >= 50) return "warning";
  return "error";
}

function calendarDay(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day);
}

// "2026-10-18" → "Oct 18", read as a calendar day rather than a UTC instant.
export function formatDay(iso) {
  if (!iso) return MISSING;
  return calendarDay(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// A feed time (UTC without a zone) as the viewer's local clock time, e.g. "09:14".
export function clockTime(iso) {
  return parseUtc(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function days(count) {
  return `${count} ${count === 1 ? "day" : "days"}`;
}

export function movedText(moved) {
  if (!moved?.previous_at) return "First forecast";
  const [key, change] = moved.p85_days ? ["P85", moved.p85_days] : ["P50", moved.p50_days];
  if (!change) return "No change";
  const direction = change > 0 ? "later" : "earlier";
  const at = clockTime(moved.previous_at);
  return `${key} ${days(Math.abs(change))} ${direction} than the forecast at ${at}`;
}

export function targetText(forecast) {
  if (!forecast.end_date) return "No target set";
  const target = `Target ${formatDay(forecast.end_date)}`;
  if (forecast.on_time_probability === null || forecast.on_time_probability === undefined) {
    return target;
  }
  return `${target}: ${percent(forecast.on_time_probability)}% chance`;
}

export function targetColor(forecast) {
  const chance = forecast.on_time_probability;
  if (!forecast.end_date || chance === null || chance === undefined) return undefined;
  return chanceColor(percent(chance));
}

export function paceText(forecast) {
  return (
    `Agents' pace: ${forecast.throughput_mean} items per working day over the last ` +
    `${forecast.history_days} working days`
  );
}

// The measured effect of one planner option, or why there is none.
export function effectText(option) {
  const effect = option.effect;
  if (!effect) return NOT_MEASURABLE;
  const parts = [`P85 ${formatDay(effect.from?.p85)} → ${formatDay(effect.p85)}`];
  const before = effect.from?.on_time_probability;
  const after = effect.on_time_probability;
  if (typeof before === "number" && typeof after === "number") {
    parts.push(`target chance ${percent(before)}% → ${percent(after)}%`);
  }
  return parts.join("; ");
}

export function itemsText(items) {
  return (items ?? []).map((number) => `#${number}`).join(", ");
}

// Real milestones ("github" or "mixed") first, in feed order, apart from the simulated epics.
export function splitFeed(feed) {
  const epics = feed?.epics ?? [];
  return {
    real: epics.filter((epic) => epic.source !== "synthetic"),
    simulated: epics.filter((epic) => epic.source === "synthetic"),
  };
}

function idsBySubject(feed) {
  return Object.fromEntries((feed?.epics ?? []).map((epic) => [epic.subject, epic.id]));
}

// Subjects whose latest forecast is a different one than at the last poll.
export function changedIds(previous, next) {
  if (!previous) return [];
  const before = idsBySubject(previous);
  return Object.entries(idsBySubject(next))
    .filter(([subject, id]) => subject in before && before[subject] !== id)
    .map(([subject]) => subject);
}

export function hasForecasts(feed) {
  return Boolean(feed && ((feed.epics ?? []).length || feed.sprint));
}

// A calendar date as a whole day count, so a chart can plot dates on its y axis.
export function dayNumber(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return Date.UTC(year, month - 1, day) / 86400000;
}

export function dayLabel(number) {
  const date = new Date(number * 86400000);
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export function trailChartData(trail) {
  const points = trail ?? [];
  const series = (key) => points.map((point) => (point[key] ? dayNumber(point[key]) : null));
  return {
    labels: points.map((point) => clockTime(point.at)),
    datasets: [
      { label: "P50", data: series("p50"), borderColor: TRAIL_COLORS.p50 },
      { label: "P85", data: series("p85"), borderColor: TRAIL_COLORS.p85 },
    ].map((set) => ({ ...set, backgroundColor: set.borderColor, pointRadius: 2, tension: 0 })),
  };
}
