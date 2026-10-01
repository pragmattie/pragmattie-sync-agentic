// Calendar quarters, as { year, quarter } with quarter 1-4.

function pad(number) {
  return String(number).padStart(2, "0");
}

// Accepts a Date or an ISO date string (read as a calendar date).
export function quarterOf(date = new Date()) {
  let year;
  let month;
  if (typeof date === "string") {
    [year, month] = date.slice(0, 10).split("-").map(Number);
  } else {
    year = date.getFullYear();
    month = date.getMonth() + 1;
  }
  return { year, quarter: Math.ceil(month / 3) };
}

export function shiftQuarter({ year, quarter }, by) {
  const index = year * 4 + (quarter - 1) + by;
  return { year: Math.floor(index / 4), quarter: (((index % 4) + 4) % 4) + 1 };
}

export function quarterLabel({ year, quarter }) {
  return `${year}-Q${quarter}`;
}

// First and last ISO date of the quarter.
export function quarterRange({ year, quarter }) {
  const firstMonth = (quarter - 1) * 3 + 1;
  const lastMonth = firstMonth + 2;
  const lastDay = new Date(Date.UTC(year, lastMonth, 0)).getUTCDate();
  return {
    start: `${year}-${pad(firstMonth)}-01`,
    end: `${year}-${pad(lastMonth)}-${pad(lastDay)}`,
  };
}
