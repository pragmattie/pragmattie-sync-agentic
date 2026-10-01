import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { VSelect, VSwitch, VTextField } from "vuetify/components";
import { sendJson } from "../api";
import ConvertLeadDialog from "../components/ConvertLeadDialog.vue";
import { vuetify } from "../plugins/vuetify";
import { useSnackbarStore } from "../stores/snackbar";

vi.mock("../api", () => ({
  getJson: vi.fn(),
  sendJson: vi.fn(),
}));

const lead = {
  id: 5,
  first_name: "Alex",
  last_name: "Abbott",
  email: "alex.abbott@northwind.example",
  company: "Northwind Example",
  status: "qualified",
};

let wrapper;
let router;
let pinia;

async function mountDialog(props = {}) {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/leads", component: { template: "<div />" } },
      { path: "/accounts/:id", component: { template: "<div />" } },
    ],
  });
  await router.push("/leads");
  await router.isReady();
  pinia = createPinia();
  wrapper = mount(ConvertLeadDialog, {
    attachTo: document.body,
    props: {
      modelValue: true,
      lead,
      "onUpdate:modelValue": (value) => wrapper.setProps({ modelValue: value }),
      ...props,
    },
    global: { plugins: [pinia, router, vuetify] },
  });
  await flushPromises();
  return wrapper;
}

function body(selector) {
  return document.body.querySelector(selector);
}

function field(name) {
  return wrapper
    .findAllComponents(VTextField)
    .find((f) => f.attributes("name") === name || f.props("name") === name);
}

function select(label) {
  return wrapper.findAllComponents(VSelect).find((s) => s.props("label") === label);
}

function opportunitySwitch() {
  return wrapper.findComponent(VSwitch);
}

async function submit() {
  body('[data-test="submit"]').click();
  await flushPromises();
  await flushPromises();
}

// Vuetify's dialog holds each navigation for one timer tick, so wait for it.
async function expectRoute(path) {
  await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe(path));
}

beforeEach(() => {
  sendJson.mockReset();
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.clearAllMocks();
  document.body.innerHTML = "";
});

describe("ConvertLeadDialog", () => {
  it("shows the lead's name and company and the defaults", async () => {
    await mountDialog();

    const text = body('[data-test="convert-dialog"]').textContent;
    expect(text).toContain("Convert Alex Abbott");
    expect(text).toContain("Creates an account for Northwind Example with this person as a contact.");
    expect(select("Industry").props("modelValue")).toBe("Technology");
    expect(select("Region").props("modelValue")).toBe("North America East");
    expect(field("employee_count").props("modelValue")).toBe(100);
    expect(opportunitySwitch().props("modelValue")).toBe(true);
    expect(field("opportunity_name").props("modelValue")).toBe("Northwind Example - New business");
    expect(field("opportunity_amount").props("modelValue")).toBe(25000);
  });

  it("hides the opportunity fields when the switch is off", async () => {
    await mountDialog();

    opportunitySwitch().vm.$emit("update:modelValue", false);
    await flushPromises();

    expect(field("opportunity_name")).toBeUndefined();
    expect(field("opportunity_amount")).toBeUndefined();
  });

  it("sends the opportunity with the switch on and goes to the account", async () => {
    sendJson.mockResolvedValue({ account_id: 42, contact_id: 7, opportunity_id: 9 });
    await mountDialog();

    select("Industry").vm.$emit("update:modelValue", "Healthcare");
    select("Region").vm.$emit("update:modelValue", "EMEA");
    field("employee_count").vm.$emit("update:modelValue", 250);
    field("opportunity_amount").vm.$emit("update:modelValue", 40000);
    await flushPromises();
    await submit();

    expect(sendJson).toHaveBeenCalledWith("POST", "/api/v1/leads/5/convert", {
      industry: "Healthcare",
      region: "EMEA",
      employee_count: 250,
      opportunity_name: "Northwind Example - New business",
      opportunity_amount: 40000,
    });
    await expectRoute("/accounts/42");
    expect(wrapper.props("modelValue")).toBe(false);
  });

  it("confirms the conversion in the app's snackbar, which survives the route change", async () => {
    sendJson.mockResolvedValue({ account_id: 42, contact_id: 7, opportunity_id: 9 });
    await mountDialog();

    await submit();

    const snackbar = useSnackbarStore(pinia);
    expect(snackbar.show).toBe(true);
    expect(snackbar.text).toBe("Alex Abbott converted to an account");
    expect(snackbar.color).toBe("primary");
  });

  it("leaves the opportunity out with the switch off", async () => {
    sendJson.mockResolvedValue({ account_id: 43, contact_id: 8, opportunity_id: null });
    await mountDialog();

    opportunitySwitch().vm.$emit("update:modelValue", false);
    await flushPromises();
    await submit();

    expect(sendJson).toHaveBeenCalledWith("POST", "/api/v1/leads/5/convert", {
      industry: "Technology",
      region: "North America East",
      employee_count: 100,
    });
    await expectRoute("/accounts/43");
  });

  it("does not send fewer than 1 employee or a zero amount", async () => {
    await mountDialog();

    field("employee_count").vm.$emit("update:modelValue", 0);
    await flushPromises();
    await submit();
    expect(sendJson).not.toHaveBeenCalled();

    field("employee_count").vm.$emit("update:modelValue", 10);
    field("opportunity_amount").vm.$emit("update:modelValue", 0);
    await flushPromises();
    await submit();
    expect(sendJson).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("Must be at least 1");
  });

  it("shows an API error inside the dialog and stays open", async () => {
    sendJson.mockRejectedValue(
      Object.assign(new Error("Lead is already converted"), { status: 409 }),
    );
    await mountDialog();

    await submit();

    expect(body('[data-test="convert-error"]').textContent).toContain(
      "Lead is already converted",
    );
    expect(wrapper.props("modelValue")).toBe(true);
    expect(router.currentRoute.value.fullPath).toBe("/leads");
    expect(useSnackbarStore(pinia).show).toBe(false);
  });

  it("closes on Cancel without a request", async () => {
    await mountDialog();

    body('[data-test="cancel"]').click();
    await flushPromises();

    expect(sendJson).not.toHaveBeenCalled();
    expect(wrapper.props("modelValue")).toBe(false);
    expect(router.currentRoute.value.fullPath).toBe("/leads");
  });

  it("resets the fields each time it opens", async () => {
    sendJson.mockRejectedValue(new Error("Lead is already converted"));
    await mountDialog();

    select("Industry").vm.$emit("update:modelValue", "Retail");
    opportunitySwitch().vm.$emit("update:modelValue", false);
    await flushPromises();
    await submit();
    expect(body('[data-test="convert-error"]')).not.toBeNull();

    await wrapper.setProps({ modelValue: false });
    await wrapper.setProps({ modelValue: true });
    await flushPromises();

    expect(select("Industry").props("modelValue")).toBe("Technology");
    expect(opportunitySwitch().props("modelValue")).toBe(true);
    expect(body('[data-test="convert-error"]')).toBeNull();
  });
});
