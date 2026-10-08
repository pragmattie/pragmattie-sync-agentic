import { enableAutoUnmount, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { defineComponent, h } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import { VApp } from "vuetify/components";
import DecisionDrawer from "../components/DecisionDrawer.vue";
import { vuetify } from "../plugins/vuetify";

const DECISION = {
  id: 9,
  created_at: "2026-10-07T13:45:00",
  agent: "pr_risk",
  model_id: "claude-sonnet-5-5",
  prompt_version: "v3",
  prompt_hash: "fedcba9876543210ffff",
  subject_type: "pr",
  subject_source: "github",
  subject_id: 42,
  head_sha: "abcdef0123456789abcdef0123456789abcdef01",
  attempt: 2,
  trigger: "poll",
  signals: { change_size: 10, timing: 2 },
  output: { cost_usd: 0.0123, tier: "T1" },
  action_taken: { labels: ["tier:T1"] },
  human_override: null,
  status: "ok",
  error: null,
  latency_ms: 1200,
  input_tokens: 1000,
  output_tokens: 200,
  supersedes_id: 4,
};

async function mountDrawer(decision) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/decisions", component: { render: () => null } }],
  });
  await router.push("/decisions");
  const Host = defineComponent({
    render: () =>
      h(VApp, null, { default: () => h(DecisionDrawer, { modelValue: true, decision }) }),
  });
  return mount(Host, { global: { plugins: [router, vuetify] } });
}

enableAutoUnmount(afterEach);

describe("DecisionDrawer", () => {
  it("shows what the decision recorded", async () => {
    const wrapper = await mountDrawer(DECISION);

    expect(wrapper.find("[data-test='drawer-title']").text()).toBe("PR risk · PR #42");
    expect(wrapper.find("[data-test='drawer-status']").text()).toBe("ok");
    const details = wrapper.find("[data-test='drawer-details']").text();
    expect(details).toContain("claude-sonnet-5-5");
    expect(details).toContain("v3 · fedcba987654");
    expect(details).not.toContain("fedcba9876543");
    expect(details).toContain(DECISION.head_sha);
    expect(details).toContain("1,200 ms");
    expect(details).toContain("1,000 in / 200 out");
    expect(details).toContain("$0.0123");

    const signals = wrapper.find("[data-test='drawer-signals']").text();
    expect(signals).toContain("change_size");
    expect(signals).toContain("10");
    expect(wrapper.find("[data-test='drawer-output']").text()).toContain('"tier": "T1"');
    expect(wrapper.find("[data-test='drawer-action']").text()).toContain('"tier:T1"');
    expect(wrapper.find("[data-test='drawer-error']").exists()).toBe(false);
    expect(wrapper.find("[data-test='drawer-override']").exists()).toBe(false);
    expect(wrapper.find("[data-test='drawer-supersedes'] a").attributes("href")).toBe(
      "/decisions?decision=4",
    );
  });

  it("shows dashes for what a failed call never recorded", async () => {
    const wrapper = await mountDrawer({
      ...DECISION,
      status: "timeout",
      model_id: null,
      prompt_version: null,
      latency_ms: null,
      input_tokens: null,
      output_tokens: null,
      output: null,
      action_taken: null,
      signals: null,
      error: "Timed out after 60s",
      supersedes_id: null,
    });

    const details = wrapper.find("[data-test='drawer-details']").text();
    expect(details).not.toContain("null");
    expect(wrapper.find("[data-test='drawer-error']").text()).toBe("Timed out after 60s");
    expect(wrapper.find("[data-test='drawer-output']").exists()).toBe(false);
    expect(wrapper.find("[data-test='drawer-signals']").exists()).toBe(false);
    expect(wrapper.find("[data-test='drawer-supersedes']").exists()).toBe(false);
  });
});
