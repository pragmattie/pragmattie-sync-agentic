import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import LoadError from "../components/LoadError.vue";
import LoadingBar from "../components/LoadingBar.vue";
import PageHeader from "../components/PageHeader.vue";
import { vuetify } from "../plugins/vuetify";

const global = { plugins: [vuetify] };

describe("PageHeader", () => {
  it("shows the title, subtitle and controls", () => {
    const wrapper = mount(PageHeader, {
      props: { title: "Engineering signals", subtitle: "The last 30 days" },
      slots: { default: "<button>Refresh</button>" },
      global,
    });

    expect(wrapper.find("h1").text()).toBe("Engineering signals");
    expect(wrapper.text()).toContain("The last 30 days");
    expect(wrapper.find("button").text()).toBe("Refresh");
  });

  it("leaves out the subtitle when there is none", () => {
    const wrapper = mount(PageHeader, { props: { title: "Engineering signals" }, global });

    expect(wrapper.findAll("p")).toHaveLength(0);
  });
});

describe("LoadError", () => {
  it("shows the error and emits retry", async () => {
    const wrapper = mount(LoadError, {
      props: { title: "Couldn't load the signals", message: "Database is unavailable" },
      global,
    });

    const alert = wrapper.find("[data-test='load-error']");
    expect(alert.text()).toContain("Couldn't load the signals");
    expect(alert.text()).toContain("Database is unavailable");
    await wrapper.find("[data-test='retry']").trigger("click");
    expect(wrapper.emitted("retry")).toHaveLength(1);
  });

  it("renders nothing without a message", () => {
    const wrapper = mount(LoadError, { props: { title: "Couldn't load" }, global });

    expect(wrapper.find("[data-test='load-error']").exists()).toBe(false);
  });
});

describe("LoadingBar", () => {
  it("shows an indeterminate bar while active", () => {
    const wrapper = mount(LoadingBar, { props: { active: true }, global });

    const bar = wrapper.find("[data-test='loading-bar']");
    expect(bar.exists()).toBe(true);
    expect(bar.find(".v-progress-linear__indeterminate").exists()).toBe(true);
  });

  it("hides the bar when idle", () => {
    const wrapper = mount(LoadingBar, { global });

    expect(wrapper.find("[data-test='loading-bar']").exists()).toBe(false);
  });
});
