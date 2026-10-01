import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { VDataTableServer } from "vuetify/components";
import { getJson } from "../api";
import { vuetify } from "../plugins/vuetify";
import AccountsView from "../views/AccountsView.vue";

vi.mock("../api", () => ({
  getJson: vi.fn(),
}));

const reps = [
  { id: 1, name: "Avery Lee" },
  { id: 2, name: "Jordan Park" },
];

function account(overrides = {}) {
  return {
    id: 1,
    name: "Northwind Example",
    industry: "Manufacturing",
    region: "EMEA",
    employee_count: 12500,
    annual_revenue: "250000000.00",
    website: "https://northwind.example",
    owner_id: 1,
    owner: { id: 1, name: "Avery Lee" },
    created_at: "2025-03-14T09:00:00",
    open_pipeline: "1234500.00",
    contact_count: 4,
    ...overrides,
  };
}

let accounts;
let total;
let wrapper;
let router;

function accountCalls() {
  return getJson.mock.calls.filter(([path]) => path === "/api/v1/accounts");
}

function lastParams() {
  return accountCalls().at(-1)[1];
}

async function mountView() {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/accounts", component: AccountsView },
      { path: "/accounts/:id", component: { template: "<div />" } },
    ],
  });
  await router.push("/accounts");
  await router.isReady();
  wrapper = mount(AccountsView, {
    attachTo: document.body,
    global: { plugins: [createPinia(), router, vuetify] },
  });
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  accounts = [account()];
  total = null;
  getJson.mockImplementation((path) => {
    if (path === "/api/v1/reps") return Promise.resolve(reps);
    return Promise.resolve({ items: accounts, total: total ?? accounts.length });
  });
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.clearAllMocks();
  vi.useRealTimers();
  document.body.innerHTML = "";
});

describe("AccountsView", () => {
  it("shows the title, subtitle and account rows", async () => {
    await mountView();

    expect(wrapper.text()).toContain("Accounts");
    expect(wrapper.text()).toContain("Customer and prospect companies, with their open pipeline.");
    const name = wrapper.find('[data-test="account-name"]');
    expect(name.text()).toBe("Northwind Example");
    expect(name.classes()).toContain("font-weight-bold");
    expect(wrapper.text()).toContain("Manufacturing");
    expect(wrapper.text()).toContain("EMEA");
    expect(wrapper.text()).toContain("12,500");
    expect(wrapper.text()).toContain("Avery Lee");
  });

  it("shows open pipeline compactly with the full amount on hover", async () => {
    await mountView();

    const pipeline = wrapper.find('[data-test="pipeline"]');
    expect(pipeline.text()).toBe("$1.2M");
    expect(pipeline.attributes("title")).toBe("$1,234,500");
  });

  it("shows a dash for zero pipeline and Unassigned for no owner", async () => {
    accounts = [account({ open_pipeline: "0.00", owner_id: null, owner: null })];
    await mountView();

    const pipeline = wrapper.find('[data-test="pipeline"]');
    expect(pipeline.text()).toBe("—");
    expect(pipeline.attributes("title")).toBeUndefined();
    expect(wrapper.text()).toContain("Unassigned");
  });

  it("loads the first page of 25 with no filters", async () => {
    await mountView();

    expect(accountCalls()).toHaveLength(1);
    expect(lastParams()).toEqual({
      q: "",
      industry: null,
      owner_id: null,
      limit: 25,
      offset: 0,
    });
  });

  it("debounces the search by 300 ms", async () => {
    await mountView();
    vi.useFakeTimers();

    await wrapper.find('[data-test="search"] input').setValue("north");
    vi.advanceTimersByTime(299);
    await flushPromises();
    expect(accountCalls()).toHaveLength(1);

    vi.advanceTimersByTime(1);
    await flushPromises();
    expect(accountCalls()).toHaveLength(2);
    expect(lastParams().q).toBe("north");
  });

  it("filters by industry and owner", async () => {
    await mountView();
    const [industrySelect, ownerSelect] = wrapper.findAllComponents({ name: "VSelect" });

    industrySelect.vm.$emit("update:modelValue", "Retail");
    await flushPromises();
    expect(lastParams().industry).toBe("Retail");

    ownerSelect.vm.$emit("update:modelValue", 2);
    await flushPromises();
    expect(lastParams().owner_id).toBe(2);

    industrySelect.vm.$emit("update:modelValue", null);
    await flushPromises();
    expect(lastParams().industry).toBeNull();
  });

  it("pages through the API and goes back to page 1 when a filter changes", async () => {
    total = 120;
    await mountView();
    const table = wrapper.findComponent(VDataTableServer);

    table.vm.$emit("update:page", 3);
    await flushPromises();
    expect(lastParams()).toMatchObject({ limit: 25, offset: 50 });

    const [industrySelect] = wrapper.findAllComponents({ name: "VSelect" });
    industrySelect.vm.$emit("update:modelValue", "Energy");
    await flushPromises();
    expect(lastParams()).toMatchObject({ industry: "Energy", offset: 0 });
  });

  it("has no column sorting", async () => {
    await mountView();

    const headers = wrapper.findComponent(VDataTableServer).props("headers");
    expect(headers.every((header) => header.sortable === false)).toBe(true);
  });

  it("opens the account page when a row is clicked", async () => {
    accounts = [account({ id: 42 })];
    await mountView();

    const push = vi.spyOn(router, "push");
    await wrapper.find("tbody tr").trigger("click");
    await flushPromises();

    expect(push).toHaveBeenCalledWith("/accounts/42");
  });

  it("uses the table's loading state, without the last search's rows, while loading", async () => {
    await mountView();
    expect(wrapper.find('[data-test="account-name"]').exists()).toBe(true);

    getJson.mockImplementation(() => new Promise(() => {}));
    wrapper.findAllComponents({ name: "VSelect" })[0].vm.$emit("update:modelValue", "Retail");
    await flushPromises();

    const table = wrapper.findComponent(VDataTableServer);
    expect(table.props("loading")).toBe(true);
    expect(wrapper.find('[data-test="account-name"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("Loading accounts…");
  });

  it("says no accounts match the filters when there are none", async () => {
    accounts = [];
    await mountView();

    expect(wrapper.text()).toContain("No accounts match these filters");
  });

  it("shows an API error alert in place of the table, keeping the header and filters", async () => {
    getJson.mockImplementation((path) => {
      if (path === "/api/v1/reps") return Promise.resolve(reps);
      return Promise.reject(new Error("Service unavailable"));
    });
    await mountView();

    const alert = wrapper.find('[data-test="load-error"]');
    expect(alert.classes()).toContain("text-error");
    expect(alert.text()).toContain("Couldn't load accounts");
    expect(alert.text()).toContain("Service unavailable");
    expect(wrapper.findComponent(VDataTableServer).exists()).toBe(false);
    expect(wrapper.find("h1").text()).toBe("Accounts");
    expect(wrapper.find('[data-test="search"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="industry"]').exists()).toBe(true);
  });
});
