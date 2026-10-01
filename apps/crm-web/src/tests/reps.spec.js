import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NETWORK_ERROR_MESSAGE } from "../api";
import { useRepsStore } from "../stores/reps";

const reps = [
  { id: 1, name: "Avery Lee", email: "avery.lee@pragmattie-sync.example", region: "East" },
  { id: 2, name: "Jordan Park", email: "jordan.park@pragmattie-sync.example", region: "EMEA" },
];

beforeEach(() => {
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("reps store", () => {
  it("loads the reps once and shares them", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(reps),
    });
    vi.stubGlobal("fetch", fetchMock);
    const store = useRepsStore();

    await Promise.all([store.load(), store.load()]);
    await store.load();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/api/v1/reps");
    expect(store.options).toEqual([
      { value: 1, title: "Avery Lee" },
      { value: 2, title: "Jordan Park" },
    ]);
  });

  it("keeps the error and allows a retry", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const store = useRepsStore();

    await expect(store.load()).rejects.toThrow(NETWORK_ERROR_MESSAGE);
    expect(store.error).toBe(NETWORK_ERROR_MESSAGE);
    expect(store.loaded).toBe(false);
  });
});
