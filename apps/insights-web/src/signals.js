// Pure calculations behind the Engineering signals page, kept apart from the
// view so they can be unit-tested.

export const MISSING = "—";

export const MODULE_NAMES = {
  leads: "Leads",
  accounts: "Accounts",
  pipeline: "Pipeline",
  forecasting: "Forecasting",
  integrations: "Integrations",
  billing_auth: "Billing & Auth",
  platform: "Platform",
  orchestrator: "Orchestrator",
};

// A module runs well over estimate when its days per point exceed this
// multiple of the median across modules.
export const OVER_ESTIMATE_FACTOR = 1.3;

// A suite is flagged as flaky at or above this flaky-failure rate (%).
export const FLAKY_THRESHOLD = 3;

const TILES = [
  { key: "deploys_per_week", title: "Deployment frequency", unit: " per week", better: "up" },
  {
    key: "lead_time_hours",
    title: "Lead time for changes",
    unit: "h",
    better: "down",
    note: "PR opened → merged, median",
  },
  { key: "change_failure_rate", title: "Change failure rate", unit: "%", better: "down" },
  {
    key: "time_to_restore_hours",
    title: "Time to restore",
    unit: "h",
    better: "down",
    note: "median",
  },
];

function hasValue(value) {
  return value !== null && value !== undefined;
}

export function moduleName(key) {
  return MODULE_NAMES[key] ?? key;
}

// The change from the previous period, or null when it can't be shown: a value
// is missing or the previous one is 0. `unit` is appended to the number ("h",
// "%"); a worded unit such as " per week" is left to the tile's value.
export function delta(current, previous, better, unit = "", days = 30) {
  if (!hasValue(current) || !hasValue(previous) || previous === 0) return null;
  const change = Math.round((current - previous) * 10) / 10;
  const suffix = /^\s/.test(unit) ? "" : unit;
  const text = `${change.toFixed(1)}${suffix} vs prior ${days} days`;
  if (change === 0) {
    return { text, direction: "flat", color: null };
  }
  const direction = change > 0 ? "up" : "down";
  return {
    text: change > 0 ? `+${text}` : text,
    direction,
    color: direction === better ? "success" : "error",
  };
}

export function doraTiles(summary) {
  const current = summary?.current ?? {};
  const previous = summary?.previous ?? {};
  const days = summary?.days ?? 30;
  return TILES.map((tile) => {
    const value = current[tile.key];
    return {
      key: tile.key,
      title: tile.title,
      value: hasValue(value) ? `${value}${tile.unit}` : MISSING,
      note: tile.note ?? "",
      delta: delta(value, previous[tile.key], tile.better, tile.unit, days),
    };
  });
}

// What share of the history is simulated, from the pull request counts.
export function sourceNote(sources) {
  const prs = sources?.pull_requests ?? {};
  const simulated = prs.synthetic ?? 0;
  const real = prs.github ?? 0;
  if (!simulated) return `${real} real PRs collected from GitHub; no simulated history.`;
  if (!real) {
    return `${simulated} simulated PRs of history. Real GitHub activity appears here once the collector runs.`;
  }
  return `${simulated} simulated PRs of history plus ${real} real PRs collected from GitHub.`;
}

// "S1", "S2", …, with "*" on the sprint in progress.
export function sprintLabels(rows) {
  return rows.map((row, index) => `S${index + 1}${row.in_progress ? "*" : ""}`);
}

export function median(values) {
  const ordered = values.filter(hasValue).sort((a, b) => a - b);
  if (!ordered.length) return null;
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
}

export function overEstimate(daysPerPoint, medianDays) {
  if (!hasValue(daysPerPoint) || !hasValue(medianDays)) return false;
  return daysPerPoint > OVER_ESTIMATE_FACTOR * medianDays;
}

// Module rows ready for the table: display name, incident bar width (% of the
// highest rate) and the over-estimate flag.
export function moduleRows(modules) {
  const medianDays = median(modules.map((row) => row.days_per_point));
  const highest = Math.max(0, ...modules.map((row) => row.incident_rate ?? 0));
  return modules.map((row) => ({
    ...row,
    name: moduleName(row.module),
    barWidth: highest ? ((row.incident_rate ?? 0) / highest) * 100 : 0,
    overEstimate: overEstimate(row.days_per_point, medianDays),
  }));
}

// Whether there is any engineering history to show at all.
export function hasHistory({ sources, sprints, modules, ci }) {
  const rows = Object.values(sources ?? {}).flatMap((counts) => Object.values(counts));
  return (
    rows.some((count) => count > 0) ||
    Boolean(sprints?.length || modules?.length || ci?.by_suite?.length)
  );
}

export function isFlaky(flakyRate) {
  return hasValue(flakyRate) && flakyRate >= FLAKY_THRESHOLD;
}

// Risk model calibration (/api/v1/signals/calibration). Ratios arrive as 0–1.

const THRESHOLD_TIERS = ["T1", "T2", "T3"];
const TIERS = ["T0", "T1", "T2", "T3"];

// The bars as the pooled grading names them: the only verdict the panel gives.
const POOLED_BARS = [
  {
    key: "t0_rate_at_most_a_quarter_of_overall",
    label: "T0's incident rate is at most a quarter of the overall rate",
  },
  {
    key: "top_decile_captures_majority_pooled",
    label: "The top tenth by score holds more than half of incident PRs",
  },
];

// A 0–1 ratio as a percentage with `places` decimals, or a dash when missing.
export function percent(ratio, places = 1) {
  return hasValue(ratio) ? `${(ratio * 100).toFixed(places)}%` : MISSING;
}

export function verdict(passed) {
  return passed ? "PASS" : "FAIL";
}

export function realCountNote(report) {
  const merged = report?.real_merged_prs ?? 0;
  const incidents = report?.real_incident_prs ?? 0;
  return (
    "The risk score is graded on simulated history, because real changes haven't caused " +
    `incidents yet. ${merged} real PRs merged so far, ${incidents} caused an incident.`
  );
}

export function thresholdRows(report) {
  return THRESHOLD_TIERS.map((tier) => {
    const row = report?.thresholds?.[tier] ?? {};
    return {
      tier: `${tier}+`,
      flagged: row.flagged ?? 0,
      incidents: row.incidents ?? 0,
      precision: percent(row.precision),
      recall: percent(row.recall),
    };
  });
}

export function tierRows(report) {
  return TIERS.map((tier) => ({
    tier,
    prs: report?.by_tier?.[tier]?.prs ?? 0,
    incidents: report?.by_tier?.[tier]?.incidents ?? 0,
  }));
}

export function pooledBarRows(pooled) {
  return POOLED_BARS.map((bar) => {
    const passed = Boolean(pooled?.bars?.[bar.key]);
    return { ...bar, passed, verdict: verdict(passed) };
  });
}

// The verdict sentence, from the pooled grading only.
export function pooledNote(pooled) {
  const passes = pooledBarRows(pooled).every((bar) => bar.passed);
  return (
    `The risk score ${passes ? "passes" : "does not pass"} both bars across ` +
    `${pooled?.histories ?? 30} generated histories: T0's incident rate ` +
    `${percent(pooled?.t0_rate, 2)} against ${percent(pooled?.overall_rate, 2)} overall, and ` +
    `the top tenth by score catches ${percent(pooled?.top_decile?.capture)} of incident PRs.`
  );
}

function ratio(part, whole) {
  return whole ? part / whole : null;
}

// This history's own figures: context for the pooled verdict, never a verdict of their own.
export function historyFigures(report) {
  const t0 = report?.by_tier?.T0 ?? {};
  return [
    {
      key: "t0_rate",
      label:
        `T0's incident rate ${percent(ratio(t0.incidents ?? 0, t0.prs ?? 0), 2)} against ` +
        `${percent(ratio(report?.incident_prs ?? 0, report?.merged_prs ?? 0), 2)} overall`,
    },
    {
      key: "top_decile",
      label: `The top tenth by score catches ${percent(report?.top_decile?.capture)} of incident PRs`,
    },
  ];
}
