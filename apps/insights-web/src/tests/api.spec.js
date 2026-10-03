import { afterEach, describe, expect, it, vi } from "vitest";
import { buildUrl, getJson, NETWORK_ERROR_MESSAGE, ORCH_URL } from "../api";

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
  it("defaults to the local orchestrator", () => {
    expect(ORCH_URL).toBe("http://localhost:8001");
    expect(buildUrl("/api/v1/signals")).toBe("http://localhost:8001/api/v1/signals");
  });

  it("takes the base URL from VITE_ORCH_URL", async () => {
    vi.stubEnv("VITE_ORCH_URL", "https://orchestrator.pragmattie-sync.example");
    vi.resetModules();
    const api = await import("../api");

    expect(api.buildUrl("/api/v1/signals")).toBe(
      "https://orchestrator.pragmattie-sync.example/api/v1/signals",
    );
  });

  it("leaves out undefined, null and empty values", () => {
    const url = buildUrl("/api/v1/signals", { q: "", kind: null, page: undefined, days: 30 });
    expect(url).toBe("http://localhost:8001/api/v1/signals?days=30");
  });

  it("repeats array values", () => {
    const url = buildUrl("/api/v1/signals", { source: ["synthetic", "github"], days: 7 });
    expect(url).toBe("http://localhost:8001/api/v1/signals?source=synthetic&source=github&days=7");
  });

  it("keeps zero and false", () => {
    expect(buildUrl("/x", { offset: 0, open: false })).toBe(
      "http://localhost:8001/x?offset=0&open=false",
    );
  });
});

describe("getJson", () => {
  it("returns the parsed body", async () => {
    const fetchMock = respond(200, [{ id: 1 }]);
    vi.stubGlobal("fetch", fetchMock);

    await expect(getJson("/api/v1/signals", { days: 7 })).resolves.toEqual([{ id: 1 }]);
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8001/api/v1/signals?days=7");
  });

  it("throws FastAPI's string detail with the status", async () => {
    vi.stubGlobal("fetch", respond(404, { detail: "Signal not found" }));

    const error = await getJson("/api/v1/signals/9").catch((e) => e);
    expect(error).toBeInstanceOf(Error);
    expect(error.message).toBe("Signal not found");
    expect(error.status).toBe(404);
  });

  it("joins a validation list's messages", async () => {
    vi.stubGlobal(
      "fetch",
      respond(422, {
        detail: [
          { loc: ["query", "days"], msg: "Input should be greater than 0" },
          { loc: ["query", "source"], msg: "Input should be 'synthetic' or 'github'" },
        ],
      }),
    );

    const error = await getJson("/api/v1/signals").catch((e) => e);
    expect(error.message).toBe(
      "Input should be greater than 0; Input should be 'synthetic' or 'github'",
    );
    expect(error.status).toBe(422);
  });

  it("falls back to the path and status when the body isn't JSON", async () => {
    vi.stubGlobal("fetch", respond(502, null, { json: false }));

    const error = await getJson("/api/v1/signals").catch((e) => e);
    expect(error.message).toBe("/api/v1/signals returned 502");
    expect(error.status).toBe(502);
  });

  it("says the orchestrator can't be reached on a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const error = await getJson("/api/v1/signals").catch((e) => e);
    expect(error).toBeInstanceOf(Error);
    expect(error.message).toBe(
      "Can't reach the Delivery Insights server. " +
        "Check that the orchestrator is running and try again.",
    );
    expect(error.message).toBe(NETWORK_ERROR_MESSAGE);
    expect(error.status).toBeUndefined();
    expect(error.network).toBe(true);
  });
});
