import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { getJson } from "../api";
import { vuetify } from "../plugins/vuetify";
import AccountDetailView from "../views/AccountDetailView.vue";

vi.mock("../api", () => ({
  getJson: vi.fn(),
}));

function opportunity(overrides = {}) {
  return {
    id: 1,
    account_id: 7,
    account: { id: 7, name: "Northwind Example" },
    name: "Northwind platform rollout",
    amount: "100000.00",
    stage: "proposal",
    probability: 50,
    close_date: "2026-11-15",
    owner_id: 1,
    owner: { id: 1, name: "Avery Lee" },
    created_at: "2026-08-01T09:00:00",
    ...overrides,
  };
}

function contact(overrides = {}) {
  return {
    id: 1,
    account_id: 7,
    first_name: "Dana",
    last_name: "Reyes",
    email: "dana.reyes@northwind.example",
    title: "Head of Operations",
    phone: "+1 555 0100",
    created_at: "2025-03-14T09:00:00",
    ...overrides,
  };
}

function accountDetail(overrides = {}) {
  return {
    id: 7,
    name: "Northwind Example",
    industry: "Manufacturing",
    region: "EMEA",
    employee_count: 12500,
    annual_revenue: "250000000.00",
    website: "https://northwind.example",
    owner_id: 1,
    owner: { id: 1, name: "Avery Lee" },
    created_at: "2025-03-14T09:00:00",
    open_pipeline: "0.00",
    contact_count: 1,
    contacts: [contact()],
    opportunities: [
      opportunity({ id: 1, stage: "prospecting", amount: "100000.00" }),
      opportunity({ id: 2, stage: "negotiation", amount: "250000.00", owner: null, owner_id: null }),
      opportunity({ id: 3, stage: "closed_won", amount: "400000.00" }),
      opportunity({ id: 4, stage: "closed_won", amount: "150000.00" }),
      opportunity({ id: 5, stage: "closed_lost", amount: "900000.00" }),
    ],
    ...overrides,
  };
}

let wrapper;

async function mountView(id = "7") {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/accounts", component: { template: "<div />" } },
      { path: "/accounts/:id", component: AccountDetailView, props: true },
    ],
  });
  await router.push(`/accounts/${id}`);
  await router.isReady();
  wrapper = mount(AccountDetailView, {
    props: { id },
    attachTo: document.body,
    global: { plugins: [router, vuetify] },
  });
  await flushPromises();
  return wrapper;
}

function tileValue(key) {
  return wrapper.find(`[data-test="tile-${key}"] [data-test="tile-value"]`).text();
}

beforeEach(() => {
  getJson.mockResolvedValue(accountDetail());
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.clearAllMocks();
  document.body.innerHTML = "";
});

describe("AccountDetailView", () => {
  it("loads the account and shows its header, back link and website", async () => {
    await mountView();

    expect(getJson).toHaveBeenCalledWith("/api/v1/accounts/7");
    expect(wrapper.find("h1").text()).toBe("Northwind Example");
    expect(wrapper.text()).toContain("Manufacturing · EMEA");

    const back = wrapper.find('[data-test="back"]');
    expect(back.text()).toContain("← Accounts");
    expect(back.attributes("href")).toBe("/accounts");

    const website = wrapper.find('[data-test="website"]');
    expect(website.text()).toContain("Website ↗");
    expect(website.attributes("href")).toBe("https://northwind.example");
    expect(website.attributes("target")).toBe("_blank");
  });

  it("leaves out the website link when there is none", async () => {
    getJson.mockResolvedValue(accountDetail({ website: null }));
    await mountView();

    expect(wrapper.find('[data-test="website"]').exists()).toBe(false);
  });

  it("computes the tiles from the opportunities", async () => {
    await mountView();

    expect(tileValue("open-pipeline")).toBe("$350K");
    expect(wrapper.find('[data-test="tile-open-pipeline"] [data-test="tile-value"]').attributes("title")).toBe("$350,000");
    expect(tileValue("open-deals")).toBe("2");
    expect(tileValue("closed-won")).toBe("$550K");
    expect(wrapper.find('[data-test="tile-closed-won"] [data-test="tile-value"]').attributes("title")).toBe("$550,000");
    expect(tileValue("employees")).toBe("12,500");
    expect(wrapper.text()).toContain("Closed won (all time)");
  });

  it("colours stage chips green for won, grey for lost and teal for open", async () => {
    await mountView();

    const chip = (id) => wrapper.find(`[data-test="stage-${id}"]`);
    expect(chip(1).classes()).toContain("text-secondary");
    expect(chip(1).text()).toBe("Prospecting");
    expect(chip(2).classes()).toContain("text-secondary");
    expect(chip(3).classes()).toContain("text-success");
    expect(chip(3).text()).toBe("Closed won");
    expect(chip(5).classes()).toContain("text-grey");
    expect(chip(5).text()).toBe("Closed lost");
  });

  it("shows each opportunity's full amount, close date and owner", async () => {
    await mountView();

    const rows = wrapper.findAll('[data-test="opportunities"] tbody tr');
    expect(rows).toHaveLength(5);
    expect(rows[0].text()).toContain("Northwind platform rollout");
    expect(rows[0].text()).toContain("$100,000");
    expect(rows[0].text()).toContain("Nov 15, 2026");
    expect(rows[0].text()).toContain("Avery Lee");
    expect(rows[1].text()).toContain("—");
  });

  it("pages the opportunities 10 at a time", async () => {
    const opportunities = Array.from({ length: 12 }, (_, i) => opportunity({ id: i + 1 }));
    getJson.mockResolvedValue(accountDetail({ opportunities }));
    await mountView();

    expect(wrapper.findAll('[data-test="opportunities"] tbody tr')).toHaveLength(10);
  });

  it("lists the contacts", async () => {
    await mountView();

    const contacts = wrapper.find('[data-test="contacts"]');
    expect(contacts.text()).toContain("Dana Reyes");
    expect(contacts.text()).toContain("Head of Operations");
    expect(contacts.text()).toContain("dana.reyes@northwind.example");
    expect(contacts.text()).toContain("+1 555 0100");
  });

  it("says when there are no contacts", async () => {
    getJson.mockResolvedValue(accountDetail({ contacts: [], contact_count: 0 }));
    await mountView();

    expect(wrapper.find('[data-test="contacts"]').text()).toContain("No contacts yet");
    expect(wrapper.find('[data-test="contact"]').exists()).toBe(false);
  });

  it("shows the owner, annual revenue and customer-since date", async () => {
    await mountView();

    const details = wrapper.find('[data-test="details"]');
    expect(details.text()).toContain("Avery Lee");
    expect(details.text()).toContain("$250,000,000");
    expect(details.text()).toContain("Mar 14, 2025");
  });

  it("shows Unassigned when the account has no owner", async () => {
    getJson.mockResolvedValue(accountDetail({ owner: null, owner_id: null }));
    await mountView();

    expect(wrapper.find('[data-test="details"]').text()).toContain("Unassigned");
  });

  it("shows the API's message for an unknown account", async () => {
    getJson.mockRejectedValue(
      Object.assign(new Error("Account 999 not found"), { status: 404 }),
    );
    await mountView("999");

    expect(getJson).toHaveBeenCalledWith("/api/v1/accounts/999");
    const alert = wrapper.find('[data-test="load-error"]');
    expect(alert.text()).toContain("Account 999 not found");
    expect(alert.classes()).toContain("text-error");
    expect(wrapper.find("h1").exists()).toBe(false);
  });
});
