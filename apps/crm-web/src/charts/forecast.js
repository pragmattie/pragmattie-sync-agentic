// Turns a forecast from GET /api/v1/forecast into chart and display data.
import { OPEN_STAGES, stageTitle } from "../constants";
import { monthLabel } from "../format";
import { quarterLabel, quarterOf, shiftQuarter } from "../quarters";

function num(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

// Never below zero, e.g. if commit somehow falls under won.
function gap(high, low) {
  return Math.max(0, num(high) - num(low));
}

// Each month's won, negotiation (commit − won) and proposal (best case − commit).
export function monthStacks(forecast) {
  const months = forecast.by_month;
  return {
    labels: months.map((month) => monthLabel(month.month)),
    won: months.map((month) => Math.max(0, num(month.won))),
    negotiation: months.map((month) => gap(month.commit, month.won)),
    proposal: months.map((month) => gap(month.best_case, month.commit)),
  };
}

// The four open stages, in pipeline order, labelled "<Stage> (<count>)".
export function stageBars(forecast) {
  const byStage = new Map(forecast.by_stage.map((row) => [row.stage, row]));
  return {
    labels: OPEN_STAGES.map(
      (stage) => `${stageTitle(stage)} (${byStage.get(stage)?.count ?? 0})`,
    ),
    values: OPEN_STAGES.map((stage) => num(byStage.get(stage)?.amount)),
  };
}

// Segment widths and the quota marker, as percentages of a scale running to
// the larger of best case and quota, plus 5%.
export function quotaMeter(forecast) {
  const won = Math.max(0, num(forecast.won));
  const negotiation = gap(forecast.commit, forecast.won);
  const proposal = gap(forecast.best_case, forecast.commit);
  const quota = Math.max(0, num(forecast.quota));
  const scale = Math.max(won + negotiation + proposal, quota) * 1.05;
  const pct = (value) => (scale > 0 ? (value / scale) * 100 : 0);
  return {
    won: pct(won),
    negotiation: pct(negotiation),
    proposal: pct(proposal),
    quota: pct(quota),
  };
}

// The two quarters before this one, this one (marked "(current)") and the next.
export function quarterOptions(today = new Date()) {
  const current = quarterOf(today);
  return [-2, -1, 0, 1].map((by) => {
    const value = quarterLabel(shiftQuarter(current, by));
    return { value, title: by === 0 ? `${value} (current)` : value };
  });
}
