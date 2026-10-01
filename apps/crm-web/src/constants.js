export const STAGES = [
  { value: "prospecting", title: "Prospecting", probability: 10, open: true },
  { value: "qualification", title: "Qualification", probability: 25, open: true },
  { value: "proposal", title: "Proposal", probability: 50, open: true },
  { value: "negotiation", title: "Negotiation", probability: 75, open: true },
  { value: "closed_won", title: "Closed won", probability: 100, open: false },
  { value: "closed_lost", title: "Closed lost", probability: 0, open: false },
];

export const OPEN_STAGES = STAGES.filter((stage) => stage.open).map((stage) => stage.value);

export const LEAD_STATUSES = [
  { value: "new", title: "New", color: "info", icon: "mdi-star-outline" },
  { value: "working", title: "Working", color: "secondary", icon: "mdi-progress-clock" },
  { value: "qualified", title: "Qualified", color: "success", icon: "mdi-check-circle-outline" },
  { value: "disqualified", title: "Disqualified", color: "grey", icon: "mdi-close-circle-outline" },
  { value: "converted", title: "Converted", color: "primary", icon: "mdi-account-convert" },
];

export const LEAD_SOURCES = [
  { value: "web", title: "Web" },
  { value: "referral", title: "Referral" },
  { value: "event", title: "Event" },
  { value: "outbound", title: "Outbound" },
  { value: "partner", title: "Partner" },
];

export const INDUSTRIES = [
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
];

export const REGIONS = [
  "North America East",
  "North America West",
  "North America Central",
  "EMEA",
  "APAC",
];

// One teal ramp, darkest = most certain.
export const FORECAST_COLORS = {
  won: "#0E5A61",
  negotiation: "#1B8A94",
  proposal: "#6FBAC1",
};

export function stageTitle(value) {
  return STAGES.find((stage) => stage.value === value)?.title ?? value;
}

export function leadStatus(value) {
  return LEAD_STATUSES.find((status) => status.value === value);
}
