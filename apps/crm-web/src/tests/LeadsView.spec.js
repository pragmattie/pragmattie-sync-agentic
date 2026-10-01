import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { VDataTableServer, VTextField } from "vuetify/components";
import { getJson, sendJson } from "../api";
import { vuetify } from "../plugins/vuetify";
import { useSnackbarStore } from "../stores/snackbar";
import LeadsView from "../views/LeadsView.vue";

vi.mock("../api", () => ({
  getJson: vi.fn(),
  sendJson: vi.fn(),
}));

const reps = [
  { id: 1, name: "Avery Lee" },
  { id: 2, name: "Jordan Park" },
];

function lead(overrides = {}) {
  return {
    id: 1,
    first_name: "Alex",
    last_name: "Abbott",
    email: "alex.abbott@northwind.example",
    company: "Northwind Example",
    title: "VP Sales",
    source: "web",
    status: "new",
    score: 72,
    owner_id: 1,
    owner: { id: 1, name: "Avery Lee" },
    converted_account_id: null,
    created_at: "2026-09-30T10:00:00",
    ...overrides,
  };
}

let leads;
let total;

function leadCalls() {
  return getJson.mock.calls.filter(([path]) => path === "/api/v1/leads");
}

function lastParams() {
  return leadCalls().at(-1)[1];
}

let wrapper;
let pinia;

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/leads", component: LeadsView },
      { path: "/accounts/:id", component: { template: "<div />" } },
    ],
  });
  await router.push("/leads");
  await router.isReady();
  pinia = createPinia();
  wrapper = mount(LeadsView, {
    attachTo: document.body,
    global: { plugins: [pinia, router, vuetify] },
  });
  await flushPromises();
  return wrapper;
}

function body(selector) {
  return document.body.querySelector(selector);
}

async function openActions(index = 0) {
  wrapper.findAll('[data-test="actions"]')[index].trigger("click");
  await flushPromises();
}

beforeEach(() => {
  leads = [lead()];
  total = null;
  getJson.mockImplementation((path) => {
    if (path === "/api/v1/reps") return Promise.resolve(reps);
    return Promise.resolve({ items: leads, total: total ?? leads.length });
  });
  sendJson.mockReset();
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.clearAllMocks();
  vi.useRealTimers();
  document.body.innerHTML = "";
});

describe("LeadsView", () => {
  it("shows the title, subtitle and lead rows", async () => {
    await mountView();

    expect(wrapper.text()).toContain("Leads");
    expect(wrapper.text()).toContain("Inbound and outbound interest, scored and assigned to reps.");
    expect(wrapper.text()).toContain("Alex Abbott");
    expect(wrapper.text()).toContain("VP Sales");
    expect(wrapper.text()).toContain("alex.abbott@northwind.example");
    expect(wrapper.text()).toContain("Web");
    expect(wrapper.text()).toContain("Avery Lee");
    expect(wrapper.text()).toContain("Sep 30, 2026");
  });

  it("shows Unassigned for a lead with no owner", async () => {
    leads = [lead({ owner_id: null, owner: null })];
    await mountView();

    expect(wrapper.text()).toContain("Unassigned");
  });

  it("loads with the default statuses, page size and newest-first sort", async () => {
    await mountView();

    expect(leadCalls()).toHaveLength(1);
    expect(lastParams()).toEqual({
      q: "",
      status: ["new", "working", "qualified"],
      source: null,
      owner_id: null,
      sort: "-created_at",
      limit: 25,
      offset: 0,
    });
  });

  it("debounces the search by 300 ms", async () => {
    await mountView();
    vi.useFakeTimers();

    await wrapper.find('[data-test="search"] input').setValue("abb");
    vi.advanceTimersByTime(299);
    await flushPromises();
    expect(leadCalls()).toHaveLength(1);

    vi.advanceTimersByTime(1);
    await flushPromises();
    expect(leadCalls()).toHaveLength(2);
    expect(lastParams().q).toBe("abb");
  });

  it("toggles status chips", async () => {
    await mountView();

    await wrapper.find('[data-test="status-qualified"]').trigger("click");
    await flushPromises();
    expect(lastParams().status).toEqual(["new", "working"]);

    await wrapper.find('[data-test="status-converted"]').trigger("click");
    await flushPromises();
    expect(lastParams().status).toEqual(["new", "working", "converted"]);
  });

  it("filters by source and owner", async () => {
    await mountView();
    const [sourceSelect, ownerSelect] = wrapper.findAllComponents({ name: "VSelect" });

    sourceSelect.vm.$emit("update:modelValue", "referral");
    await flushPromises();
    expect(lastParams().source).toBe("referral");

    ownerSelect.vm.$emit("update:modelValue", 2);
    await flushPromises();
    expect(lastParams().owner_id).toBe(2);

    sourceSelect.vm.$emit("update:modelValue", null);
    await flushPromises();
    expect(lastParams().source).toBeNull();
  });

  it("sorts and pages through the API", async () => {
    total = 500;
    await mountView();
    const table = wrapper.findComponent(VDataTableServer);

    table.vm.$emit("update:sortBy", [{ key: "last_name", order: "asc" }]);
    await flushPromises();
    expect(lastParams().sort).toBe("last_name");

    table.vm.$emit("update:sortBy", [{ key: "score", order: "desc" }]);
    await flushPromises();
    expect(lastParams().sort).toBe("-score");

    table.vm.$emit("update:itemsPerPage", 50);
    table.vm.$emit("update:page", 3);
    await flushPromises();
    expect(lastParams()).toMatchObject({ limit: 50, offset: 100 });
  });

  it("goes back to page 1 when a filter changes", async () => {
    total = 500;
    await mountView();
    const table = wrapper.findComponent(VDataTableServer);
    table.vm.$emit("update:page", 2);
    await flushPromises();
    expect(lastParams().offset).toBe(25);

    await wrapper.find('[data-test="status-new"]').trigger("click");
    await flushPromises();
    expect(lastParams()).toMatchObject({ offset: 0, status: ["working", "qualified"] });
  });

  it("links a converted lead to its account instead of a menu", async () => {
    leads = [lead({ status: "converted", converted_account_id: 42 })];
    await mountView();

    const link = wrapper.find('[data-test="account-link"]');
    expect(link.exists()).toBe(true);
    expect(link.text()).toContain("Account →");
    expect(link.attributes("href")).toBe("/accounts/42");
    expect(wrapper.find('[data-test="actions"]').exists()).toBe(false);
  });

  it("offers conversion and every other status to an open lead", async () => {
    leads = [lead({ status: "working" })];
    await mountView();
    await openActions();

    expect(body('[data-test="convert"]')).not.toBeNull();
    const marks = [...document.body.querySelectorAll('[data-test^="mark-"]')].map((el) =>
      el.textContent.trim(),
    );
    expect(marks).toEqual(["Mark new", "Mark qualified", "Mark disqualified"]);
  });

  it("does not offer conversion on a disqualified lead", async () => {
    leads = [lead({ status: "disqualified" })];
    await mountView();
    await openActions();

    expect(body('[data-test="convert"]')).toBeNull();
    const marks = [...document.body.querySelectorAll('[data-test^="mark-"]')].map((el) =>
      el.textContent.trim(),
    );
    expect(marks).toEqual(["Mark new", "Mark working", "Mark qualified"]);
  });

  it("opens the conversion dialog for the lead", async () => {
    leads = [lead({ status: "qualified" })];
    await mountView();
    await openActions();

    body('[data-test="convert"]').click();
    await flushPromises();

    const dialog = wrapper.findComponent({ name: "ConvertLeadDialog" });
    expect(dialog.props("modelValue")).toBe(true);
    expect(body('[data-test="convert-dialog"]').textContent).toContain("Convert Alex Abbott");
    expect(sendJson).not.toHaveBeenCalled();
  });

  it("marks a status, confirms and reloads", async () => {
    sendJson.mockResolvedValue(lead({ status: "qualified" }));
    await mountView();
    await openActions();

    body('[data-test="mark-qualified"]').click();
    await flushPromises();

    expect(sendJson).toHaveBeenCalledWith("PATCH", "/api/v1/leads/1", { status: "qualified" });
    expect(leadCalls()).toHaveLength(2);
    const snackbar = useSnackbarStore(pinia);
    expect(snackbar.text).toBe("Alex Abbott marked qualified");
    expect(snackbar.color).toBe("primary");
  });

  it("reports a failed status change in the snackbar without reloading", async () => {
    sendJson.mockRejectedValue(new Error("Lead is already converted"));
    await mountView();
    await openActions();

    body('[data-test="mark-qualified"]').click();
    await flushPromises();

    const snackbar = useSnackbarStore(pinia);
    expect(snackbar.text).toBe("Lead is already converted");
    expect(snackbar.color).toBe("error");
    expect(leadCalls()).toHaveLength(1);
  });

  it("uses the table's loading state, without the last filter's rows, while loading", async () => {
    await mountView();
    expect(wrapper.text()).toContain("Alex Abbott");

    getJson.mockImplementation(() => new Promise(() => {}));
    wrapper.findAllComponents({ name: "VSelect" })[0].vm.$emit("update:modelValue", "referral");
    await flushPromises();

    const table = wrapper.findComponent(VDataTableServer);
    expect(table.props("loading")).toBe(true);
    expect(table.props("items")).toEqual([]);
    expect(wrapper.text()).not.toContain("Alex Abbott");
    expect(wrapper.text()).toContain("Loading leads…");
  });

  it("says no leads match the filters when there are none", async () => {
    leads = [];
    await mountView();

    expect(wrapper.text()).toContain("No leads match these filters");
  });

  it("shows an API error alert in place of the table, keeping the header and filters", async () => {
    getJson.mockImplementation((path) => {
      if (path === "/api/v1/reps") return Promise.resolve(reps);
      return Promise.reject(new Error("Service unavailable"));
    });
    await mountView();

    const alert = wrapper.find('[data-test="load-error"]');
    expect(alert.classes()).toContain("text-error");
    expect(alert.text()).toContain("Couldn't load leads");
    expect(alert.text()).toContain("Service unavailable");
    expect(wrapper.findComponent(VDataTableServer).exists()).toBe(false);
    expect(wrapper.find("h1").text()).toBe("Leads");
    expect(wrapper.find('[data-test="search"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="statuses"]').exists()).toBe(true);
  });

  it("loads again when a filter changes after a failure", async () => {
    getJson.mockImplementation((path) => {
      if (path === "/api/v1/reps") return Promise.resolve(reps);
      return Promise.reject(new Error("Service unavailable"));
    });
    await mountView();

    getJson.mockImplementation((path) => {
      if (path === "/api/v1/reps") return Promise.resolve(reps);
      return Promise.resolve({ items: leads, total: leads.length });
    });
    wrapper.findAllComponents({ name: "VSelect" })[0].vm.$emit("update:modelValue", "referral");
    await flushPromises();

    expect(wrapper.find('[data-test="load-error"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("Alex Abbott");
  });
});

describe("New lead dialog", () => {
  function type(name, value) {
    const field = wrapper
      .findAllComponents(VTextField)
      .find((f) => f.attributes("name") === name || f.props("name") === name);
    field.vm.$emit("update:modelValue", value);
  }

  async function openDialog() {
    await wrapper.find('[data-test="new-lead"]').trigger("click");
    await flushPromises();
  }

  async function save() {
    body('.v-dialog [data-test="save"]').click();
    await flushPromises();
    await flushPromises();
  }

  async function fillRequired(email = "sam.lee@contoso.example") {
    type("first_name", "Sam");
    type("last_name", "Lee");
    type("email", email);
    type("company", "Contoso Example");
    await flushPromises();
  }

  it("requires the name, email and company and checks the email", async () => {
    await mountView();
    await openDialog();

    await save();
    expect(sendJson).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("Required");

    await fillRequired("not-an-email");
    await save();
    expect(sendJson).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("Enter a valid email");
  });

  it("creates a lead with the defaults, confirms and reloads", async () => {
    sendJson.mockResolvedValue(lead({ id: 7, company: "Contoso Example" }));
    await mountView();
    await openDialog();

    await fillRequired();
    await save();

    expect(sendJson).toHaveBeenCalledWith("POST", "/api/v1/leads", {
      first_name: "Sam",
      last_name: "Lee",
      email: "sam.lee@contoso.example",
      company: "Contoso Example",
      title: null,
      source: "web",
      owner_id: null,
      score: 50,
    });
    expect(useSnackbarStore(pinia).text).toBe("Lead created for Contoso Example");
    expect(leadCalls()).toHaveLength(2);
    expect(wrapper.findComponent({ name: "NewLeadDialog" }).props("modelValue")).toBe(false);
  });

  it("keeps the dialog open and shows an API error", async () => {
    sendJson.mockRejectedValue(Object.assign(new Error("owner_id 99 does not exist"), {
      status: 422,
    }));
    await mountView();
    await openDialog();

    await fillRequired();
    await save();

    expect(body('.v-dialog [data-test="form-error"]').textContent).toContain(
      "owner_id 99 does not exist",
    );
    expect(wrapper.findComponent({ name: "NewLeadDialog" }).props("modelValue")).toBe(true);
    expect(leadCalls()).toHaveLength(1);
  });
});
