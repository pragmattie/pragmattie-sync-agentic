import { afterEach, describe, expect, it, vi } from "vitest";
import { API_URL, buildUrl, getJson, NETWORK_ERROR_MESSAGE, sendJson } from "../api";

function respond(status, body, { json = true } = {}) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: json ? () => Promise.resolve(body) : () => Promise.reject(new SyntaxError("not JSON")),
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("buildUrl", () => {
  it("defaults to the local API", () => {
    expect(API_URL).toBe("http://localhost:8000");
    expect(buildUrl("/api/v1/reps")).toBe("http://localhost:8000/api/v1/reps");
  });

  it("takes the base URL from VITE_API_URL", async () => {
    vi.stubEnv("VITE_API_URL", "https://api.pragmattie-sync.example");
    vi.resetModules();
    const api = await import("../api");

    expect(api.buildUrl("/api/v1/reps")).toBe("https://api.pragmattie-sync.example/api/v1/reps");
  });

  it("leaves out undefined, null and empty values", () => {
    const url = buildUrl("/api/v1/leads", { q: "", owner_id: null, page: undefined, size: 25 });
    expect(url).toBe("http://localhost:8000/api/v1/leads?size=25");
  });

  it("repeats array values", () => {
    const url = buildUrl("/api/v1/leads", { status: ["new", "working"], q: "acme" });
    expect(url).toBe("http://localhost:8000/api/v1/leads?status=new&status=working&q=acme");
  });

  it("keeps zero and false", () => {
    expect(buildUrl("/x", { offset: 0, open: false })).toBe(
      "http://localhost:8000/x?offset=0&open=false",
    );
  });
});

describe("getJson", () => {
  it("returns the parsed body", async () => {
    const fetchMock = respond(200, [{ id: 1 }]);
    vi.stubGlobal("fetch", fetchMock);

    await expect(getJson("/api/v1/reps", { region: "EMEA" })).resolves.toEqual([{ id: 1 }]);
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/api/v1/reps?region=EMEA");
  });

  it("throws the API's string detail with the status", async () => {
    vi.stubGlobal("fetch", respond(404, { detail: "Lead not found" }));

    const error = await getJson("/api/v1/leads/9").catch((e) => e);
    expect(error).toBeInstanceOf(Error);
    expect(error.message).toBe("Lead not found");
    expect(error.status).toBe(404);
  });

  it("joins a validation list's messages", async () => {
    vi.stubGlobal(
      "fetch",
      respond(422, {
        detail: [
          { loc: ["body", "email"], msg: "value is not a valid email address" },
          { loc: ["body", "name"], msg: "Field required" },
        ],
      }),
    );

    const error = await getJson("/api/v1/leads").catch((e) => e);
    expect(error.message).toBe("value is not a valid email address; Field required");
    expect(error.status).toBe(422);
  });

  it("falls back to the path and status when the body isn't JSON", async () => {
    vi.stubGlobal("fetch", respond(502, null, { json: false }));

    const error = await getJson("/api/v1/leads").catch((e) => e);
    expect(error.message).toBe("/api/v1/leads returned 502");
    expect(error.status).toBe(502);
  });

  it("has no status on a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const error = await getJson("/api/v1/leads").catch((e) => e);
    expect(error).toBeInstanceOf(Error);
    expect(error.status).toBeUndefined();
    expect(error.network).toBe(true);
  });

  it("says the server can't be reached on a network failure, not the browser's message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const error = await getJson("/api/v1/leads").catch((e) => e);
    expect(error.message).toBe(NETWORK_ERROR_MESSAGE);
    expect(error.message).toContain("Can't reach the PragMattie Sync server");
    expect(error.message).not.toContain("Failed to fetch");
  });

  it("gives a network failure and an API error different, readable messages", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const network = await getJson("/api/v1/leads").catch((e) => e);

    vi.stubGlobal("fetch", respond(503, { detail: "Database is unavailable" }));
    const api = await getJson("/api/v1/leads").catch((e) => e);

    expect(api.message).toBe("Database is unavailable");
    expect(network.message).not.toBe(api.message);
    expect(api.network).toBeUndefined();
  });
});

describe("sendJson", () => {
  it("sends the body as JSON", async () => {
    const fetchMock = respond(201, { id: 7 });
    vi.stubGlobal("fetch", fetchMock);

    await expect(sendJson("POST", "/api/v1/leads", { name: "Ada" })).resolves.toEqual({ id: 7 });
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/v1/leads");
    expect(options.method).toBe("POST");
    expect(options.headers["Content-Type"]).toBe("application/json");
    expect(options.body).toBe('{"name":"Ada"}');
  });

  it("accepts an empty success response", async () => {
    vi.stubGlobal("fetch", respond(204, null, { json: false }));

    await expect(sendJson("DELETE", "/api/v1/leads/7")).resolves.toBeNull();
  });
});
