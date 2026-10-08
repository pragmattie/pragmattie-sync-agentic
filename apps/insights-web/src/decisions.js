// Pure helpers behind the Decision log page, kept apart from the view so they
// can be unit-tested.

export const MISSING = "—";

export const AGENT_LABELS = {
  pr_risk: "PR risk",
  triage: "Triage",
  tier_override: "Tier override",
  objection_window: "Objection window",
  implementer: "Implementer",
  reviewer: "Reviewer",
  pr_risk_rubric: "Risk rubric (manual)",
  forecaster: "Forecaster",
  planner: "Planner",
  test_selector: "Test selector",
  release_gate: "Release gate",
};

export function agentLabel(agent) {
  return AGENT_LABELS[agent] ?? agent;
}

export function subjectLabel(decision) {
  const id = decision.subject_id;
  switch (decision.subject_type) {
    case "pr":
      return `PR #${id}`;
    case "issue":
      return `Issue #${id}`;
    case "sprint":
      return `Sprint: ${decision.output?.subject ?? id}`;
    case "epic":
      return `Epic: ${decision.output?.subject ?? id}`;
    case "release":
      return `Release up to PR #${id}`;
    default:
      return `${decision.subject_type} #${id}`;
  }
}

// What a row is about, read from its head_sha: the agents prefix the ones that
// are not a commit or an issue version.
const KIND_PREFIXES = [
  ["run-", "agent run"],
  ["review-", "review"],
  ["window-", "window sign-off"],
  ["comment-", "a person's comment"],
];

export function kindOf(decision) {
  const sha = decision.head_sha;
  if (!sha) return "";
  const prefixed = KIND_PREFIXES.find(([prefix]) => sha.startsWith(prefix));
  if (prefixed) return prefixed[1];
  const short = sha.slice(0, 7);
  return decision.subject_type === "issue" ? `version ${short}` : `commit ${short}`;
}

export function statusColor(status) {
  if (status === "ok") return "success";
  if (status === "rejected" || status === "missed") return "warning";
  return "error";
}

function isMissing(value) {
  return value === undefined || value === null || Number.isNaN(value);
}

export function formatCost(usd) {
  if (isMissing(usd)) return MISSING;
  return usd < 1 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

export function formatNumber(value) {
  return isMissing(value) ? MISSING : value.toLocaleString("en-US");
}

// The orchestrator stores UTC without a zone, so a bare time is read as UTC.
export function parseUtc(iso) {
  const zoned = /(Z|[+-]\d{2}:?\d{2})$/.test(iso);
  return new Date(zoned ? iso : `${iso}Z`);
}

export const TIME_FORMAT = { dateStyle: "medium", timeStyle: "short" };

export function formatTime(iso) {
  if (!iso) return MISSING;
  return parseUtc(iso).toLocaleString(undefined, TIME_FORMAT);
}

// The run cost an agent recorded with its output, when it recorded one.
export function decisionCost(decision) {
  const value = decision.output?.cost_usd;
  return typeof value === "number" ? value : null;
}

export function totalsLine(totals, total) {
  const runs = totals.runs;
  const parts = [
    `${formatNumber(total)} decision${total === 1 ? "" : "s"}`,
    `${formatNumber(runs)} run${runs === 1 ? "" : "s"} cost ${formatCost(totals.cost_usd)}`,
    `${formatNumber(totals.input_tokens)} in / ${formatNumber(totals.output_tokens)} out tokens`,
  ];
  if (totals.runs_without_cost) {
    parts.push(`${formatNumber(totals.runs_without_cost)} without a recorded cost`);
  }
  return parts.join(" · ");
}

export const PAGE_SIZE = 50;

export const SUBJECT_OPTIONS = [
  { title: "PR", value: "pr" },
  { title: "Issue", value: "issue" },
  { title: "Sprint", value: "sprint" },
  { title: "Epic", value: "epic" },
  { title: "Release", value: "release" },
];

// The API matches status exactly, so every failure kind is its own option.
export const STATUS_OPTIONS = [
  "ok",
  "error",
  "timeout",
  "rate_limited",
  "refused",
  "invalid_output",
  "rejected",
  "missed",
];

export const TIER_OPTIONS = ["T0", "T1", "T2", "T3"];

export const SOURCE_OPTIONS = [
  { title: "All", value: "all" },
  { title: "Real", value: "real" },
  { title: "Simulated", value: "simulated" },
];

const SOURCE_PARAMS = { real: "github", simulated: "synthetic" };

export const FILTER_KEYS = ["agent", "subject", "status", "tier", "source"];

function single(value) {
  return Array.isArray(value) ? value[0] : value;
}

// The filters and page held in the URL query, with anything unknown dropped.
export function filtersFromQuery(query) {
  const filters = {};
  for (const key of FILTER_KEYS) {
    const value = single(query[key]);
    if (value) filters[key] = value;
  }
  if (!SOURCE_PARAMS[filters.source]) delete filters.source;
  const page = Number.parseInt(single(query.page), 10);
  return { filters, page: page > 1 ? page : 1 };
}

// The orchestrator's query parameters for one page of the filtered log.
export function listParams(filters, page) {
  return {
    agent: filters.agent,
    subject_type: filters.subject,
    status: filters.status,
    tier: filters.tier,
    subject_source: SOURCE_PARAMS[filters.source],
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  };
}

export function signalRows(signals) {
  return Object.entries(signals ?? {}).map(([name, points]) => ({
    name,
    points: typeof points === "object" && points !== null ? JSON.stringify(points) : points,
  }));
}

export function prettyJson(value) {
  return JSON.stringify(value, null, 2);
}
