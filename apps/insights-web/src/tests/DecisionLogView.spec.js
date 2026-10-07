import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import { VApp, VSelect } from "vuetify/components";
import { getJson } from "../api";
import { vuetify } from "../plugins/vuetify";
import DecisionLogView from "../views/DecisionLogView.vue";

vi.mock("../api", () => ({ getJson: vi.fn() }));

const SHA = "abcdef0123456789abcdef0123456789abcdef01";

function decision(overrides = {}) {
  return {
    id: 1,
    created_at: "2026-10-07T13:45:00",
    agent: "pr_risk",
    agent_version: "1",
    model_id: "claude-sonnet-5-5",
    prompt_version: "v3",
    prompt_hash: "0123456789abcdef0123",
    subject_type: "pr",
    subject_source: "github",
    subject_id: 42,
    head_sha: SHA,
    attempt: 1,
    trigger: "poll",
    signals: { change_size: 10, timing: 2 },
    output: { cost_usd: 0.0123 },
    action_taken: { labels: ["tier:T1"] },
    human_override: null,
    final_score: 34,
    tier: "T1",
    status: "ok",
    error: null,
    latency_ms: 1200,
    input_tokens: 1000,
    output_tokens: 200,
    supersedes_id: null,
    ...overrides,
  };
}

const FAILED_ERROR = 'Anthropic API 529: {"type":"overloaded_error",\n  "message": "Overloaded"}';

const ROWS = [
  decision(),
  decision({
    id: 2,
    agent: "triage",
    subject_type: "issue",
    subject_source: "synthetic",
    subject_id: 7,
    trigger: "trial",
    status: "error",
    error: FAILED_ERROR,
    output: null,
    tier: null,
    final_score: null,
  }),
  decision({
    id: 3,
    agent: "tier_override",
    head_sha: "comment-555",
    trigger: "human",
    status: "rejected",
    human_override: {
      actor: "pat-reviewer",
      from_tier: "T2",
      to_tier: "T0",
      reason: "docs only",
      why: "Rejected: a policy floor keeps this PR at T2 or above.",
    },
    supersedes_id: 1,
  }),
];

const TOTALS = {
  runs: 120,
  input_tokens: 45000,
  output_tokens: 3000,
  cost_usd: 1.5,
  runs_without_cost: 0,
};

function page(overrides = {}) {
  return { total: 120, limit: 50, offset: 0, decisions: ROWS, totals: TOTALS, ...overrides };
}

const AGENTS = [
  { agent: "pr_risk", runs: 100 },
  { agent: "triage", runs: 20 },
];

function mockApi(list = page()) {
  getJson.mockImplementation(async (path) => {
    if (path === "/api/v1/signals/decisions/agents") return AGENTS;
    if (path === "/api/v1/signals/decisions") return typeof list === "function" ? list() : list;
    const id = Number(path.split("/").pop());
    return ROWS.find((row) => row.id === id);
  });
}

function filter(wrapper, name) {
  return wrapper
    .findAllComponents(VSelect)
    .find((select) => select.attributes("data-test") === `filter-${name}`);
}

// Runs an action that changes the URL and waits for the navigation and the
// requests it starts to finish.
async function navigate(router, action) {
  const done = new Promise((resolve) => {
    const stop = router.afterEach(() => {
      stop();
      resolve();
    });
  });
  await action();
  await done;
  await flushPromises();
}

function listCalls() {
  return getJson.mock.calls.filter(([path]) => path === "/api/v1/signals/decisions");
}

async function mountView(query = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/decisions", component: DecisionLogView }],
  });
  await router.push({ path: "/decisions", query });
  await router.isReady();
  const Host = defineComponent({
    render: () => h(VApp, null, { default: () => h(DecisionLogView) }),
  });
  const wrapper = mount(Host, { global: { plugins: [router, vuetify] } });
  await flushPromises();
  return { wrapper, router };
}

enableAutoUnmount(afterEach);

beforeEach(() => {
  getJson.mockReset();
});

describe("DecisionLogView", () => {
  it("shows a page of rows, newest first, with the Trial and Simulated chips", async () => {
    mockApi();
    const { wrapper } = await mountView();

    expect(wrapper.find("h1").text()).toBe("Decision log");
    expect(listCalls()[0][1]).toMatchObject({ limit: 50, offset: 0 });

    const first = wrapper.find("[data-test='row-1']");
    expect(first.text()).toContain("PR risk");
    expect(first.text()).toContain("PR #42");
    expect(first.text()).toContain("commit abcdef0");
    expect(first.text()).toContain("$0.0123");
    expect(first.find("[data-test='chip-trial']").exists()).toBe(false);
    expect(first.find("[data-test='chip-simulated']").exists()).toBe(false);

    const trial = wrapper.find("[data-test='row-2']");
    expect(trial.text()).toContain("Issue #7");
    expect(trial.text()).toContain("version abcdef0");
    expect(trial.find("[data-test='chip-trial']").text()).toBe("Trial");
    expect(trial.find("[data-test='chip-simulated']").text()).toBe("Simulated");
    expect(trial.text()).toContain("—");

    expect(wrapper.find("[data-test='row-3']").text()).toContain("a person's comment");
  });

  it("re-queries and updates the URL when a filter changes", async () => {
    mockApi();
    const { wrapper, router } = await mountView({ page: "2" });
    expect(listCalls().at(-1)[1]).toMatchObject({ offset: 50 });

    await navigate(router, () =>
      filter(wrapper, "status").vm.$emit("update:modelValue", "error"),
    );

    expect(router.currentRoute.value.query).toEqual({ status: "error" });
    expect(listCalls().at(-1)[1]).toMatchObject({ status: "error", offset: 0 });

    await navigate(router, () => wrapper.find("[data-test='source-simulated']").trigger("click"));

    expect(router.currentRoute.value.query).toEqual({ status: "error", source: "simulated" });
    expect(listCalls().at(-1)[1]).toMatchObject({
      status: "error",
      subject_source: "synthetic",
    });
  });

  it("reads filters from the URL and offers the agents the log has", async () => {
    mockApi();
    const { wrapper } = await mountView({ agent: "triage", tier: "T2", subject: "issue" });

    expect(listCalls()[0][1]).toMatchObject({
      agent: "triage",
      tier: "T2",
      subject_type: "issue",
    });
    expect(filter(wrapper, "agent").props("items")).toEqual([
      { title: "PR risk", value: "pr_risk" },
      { title: "Triage", value: "triage" },
    ]);
  });

  it("requests the right offset when paging", async () => {
    mockApi();
    const { wrapper, router } = await mountView();

    await navigate(router, () =>
      wrapper.findComponent("[data-test='decisions-table']").vm.$emit("update:page", 3),
    );

    expect(router.currentRoute.value.query).toEqual({ page: "3" });
    expect(listCalls().at(-1)[1]).toMatchObject({ limit: 50, offset: 100 });
  });

  it("shows the totals for the filtered set", async () => {
    mockApi(page({ totals: { ...TOTALS, runs_without_cost: 4 } }));
    const { wrapper } = await mountView();

    expect(wrapper.find("[data-test='totals']").text()).toBe(
      "120 decisions · 120 runs cost $1.50 · 45,000 in / 3,000 out tokens · " +
        "4 without a recorded cost",
    );
  });

  it("opens a failed row and shows its raw error verbatim", async () => {
    mockApi();
    const { wrapper, router } = await mountView();

    await navigate(router, () => wrapper.find("[data-test='row-2']").trigger("click"));

    expect(router.currentRoute.value.query).toEqual({ decision: "2" });
    const drawer = wrapper.find("[data-test='decision-drawer']");
    expect(drawer.find("[data-test='drawer-title']").text()).toBe("Triage · Issue #7");
    expect(drawer.find("[data-test='drawer-error']").element.textContent).toBe(FAILED_ERROR);
  });

  it("opens an override row and shows from and to", async () => {
    mockApi();
    const { wrapper } = await mountView({ decision: "3" });

    const drawer = wrapper.find("[data-test='decision-drawer']");
    expect(drawer.find("[data-test='override-Who']").text()).toContain("pat-reviewer");
    expect(drawer.find("[data-test='override-From']").text()).toContain("T2");
    expect(drawer.find("[data-test='override-To']").text()).toContain("T0");
    expect(drawer.find("[data-test='drawer-supersedes'] a").attributes("href")).toContain(
      "decision=1",
    );
  });

  it("shows the empty state with a button that clears the filters", async () => {
    mockApi(page({ total: 0, decisions: [], totals: { ...TOTALS, runs: 0 } }));
    const { wrapper, router } = await mountView({ status: "missed", tier: "T3" });

    const empty = wrapper.find("[data-test='empty']");
    expect(empty.text()).toContain("No decisions match these filters.");

    await navigate(router, () => empty.find("[data-test='clear-filters']").trigger("click"));

    expect(router.currentRoute.value.query).toEqual({});
    expect(listCalls().at(-1)[1]).toMatchObject({ status: undefined, tier: undefined });
  });

  it("shows the load error when the log can't be read", async () => {
    getJson.mockImplementation(async (path) => {
      if (path === "/api/v1/signals/decisions/agents") return [];
      throw new Error("Database is unavailable");
    });
    const { wrapper } = await mountView();

    const error = wrapper.find("[data-test='load-error']");
    expect(error.text()).toContain("Couldn't load the decision log");
    expect(error.text()).toContain("Database is unavailable");
  });
});
