import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { defineComponent, h } from "vue";
import { VApp } from "vuetify/components";
import AppSnackbar from "../components/AppSnackbar.vue";
import { vuetify } from "../plugins/vuetify";
import { SNACKBAR_TIMEOUT_MS, useSnackbarStore } from "../stores/snackbar";

let wrapper;
let pinia;

function mountSnackbar() {
  const Host = defineComponent({ render: () => h(VApp, () => h(AppSnackbar)) });
  wrapper = mount(Host, { attachTo: document.body, global: { plugins: [pinia, vuetify] } });
}

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.innerHTML = "";
});

describe("snackbar store", () => {
  it("shows a confirmation in the primary colour", () => {
    const snackbar = useSnackbarStore();
    snackbar.confirm("Alex Abbott marked qualified");

    expect(snackbar.show).toBe(true);
    expect(snackbar.text).toBe("Alex Abbott marked qualified");
    expect(snackbar.color).toBe("primary");
  });

  it("shows a failure in the error colour", () => {
    const snackbar = useSnackbarStore();
    snackbar.error("Stage change not allowed");

    expect(snackbar.color).toBe("error");
  });
});

describe("AppSnackbar", () => {
  it("shows the store's message for 3 seconds", async () => {
    mountSnackbar();
    useSnackbarStore().confirm("Lead created for Contoso Example");
    await flushPromises();

    const snackbar = wrapper.findComponent({ name: "VSnackbar" });
    expect(SNACKBAR_TIMEOUT_MS).toBe(3000);
    expect(snackbar.props("timeout")).toBe(3000);
    expect(snackbar.props("color")).toBe("primary");
    expect(document.body.textContent).toContain("Lead created for Contoso Example");
  });
});
