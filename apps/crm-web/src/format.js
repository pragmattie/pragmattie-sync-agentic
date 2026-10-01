const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Money arrives from the API as strings ("12500.00"); null counts as 0.
function toNumber(value) {
  if (value === null || value === undefined || value === "") return 0;
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

const compactUsd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  minimumFractionDigits: 0,
  maximumFractionDigits: 1,
});

const wholeUsd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

// Compact dollars for tiles and axes: $1.2M.
export function money(value) {
  return compactUsd.format(toNumber(value));
}

// Whole dollars for tables and tooltips: $1,234,500.
export function moneyFull(value) {
  return wholeUsd.format(toNumber(value));
}

export function percent(value) {
  return `${Math.round(toNumber(value))}%`;
}

// "2026-09-30" -> "Sep 30, 2026", read as a calendar date with no time-zone shift.
export function shortDate(iso) {
  if (!iso) return "";
  const [year, month, day] = String(iso).slice(0, 10).split("-").map(Number);
  return `${MONTHS[month - 1]} ${day}, ${year}`;
}

// "2026-09" -> "Sep 2026".
export function monthLabel(value) {
  if (!value) return "";
  const [year, month] = String(value).split("-").map(Number);
  return `${MONTHS[month - 1]} ${year}`;
}
