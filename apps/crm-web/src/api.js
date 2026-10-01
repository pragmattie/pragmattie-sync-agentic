export const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

function isEmpty(value) {
  return value === undefined || value === null || value === "";
}

// Builds the request URL. Empty values are left out; an array repeats the
// parameter (`?status=new&status=working`), as the API expects.
export function buildUrl(path, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    const values = Array.isArray(value) ? value : [value];
    for (const item of values) {
      if (!isEmpty(item)) query.append(key, item);
    }
  }
  const qs = query.toString();
  return `${API_URL}${path}${qs ? `?${qs}` : ""}`;
}

function detailMessage(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => item?.msg).filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  return null;
}

export const NETWORK_ERROR_MESSAGE =
  "Can't reach the PragMattie Sync server. Check your connection and try again.";

async function request(path, url, options) {
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    // No response at all: the browser's own message ("Failed to fetch") means
    // little to a user, so say what happened instead.
    const error = new Error(NETWORK_ERROR_MESSAGE);
    error.network = true;
    throw error;
  }
  let body = null;
  let isJson = false;
  try {
    body = await response.json();
    isJson = true;
  } catch {
    // A body that isn't JSON (or no body at all) is handled below.
  }
  if (!response.ok) {
    const message = (isJson && detailMessage(body?.detail)) || `${path} returned ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return body;
}

export function getJson(path, params) {
  return request(path, buildUrl(path, params), {
    headers: { Accept: "application/json" },
  });
}

export function sendJson(method, path, body) {
  const options = {
    method,
    headers: { Accept: "application/json" },
  };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  return request(path, buildUrl(path), options);
}
