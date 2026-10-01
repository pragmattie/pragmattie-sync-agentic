import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import PageHeader from "../components/PageHeader.vue";
import { vuetify } from "../plugins/vuetify";

describe("PageHeader", () => {
  it("shows the title, subtitle and actions", () => {
    const wrapper = mount(PageHeader, {
      props: { title: "Leads", subtitle: "Every open lead" },
      slots: { default: "<button>New lead</button>" },
      global: { plugins: [vuetify] },
    });

    expect(wrapper.find("h1").text()).toBe("Leads");
    expect(wrapper.text()).toContain("Every open lead");
    expect(wrapper.find("button").text()).toBe("New lead");
  });

  it("leaves out the subtitle when there is none", () => {
    const wrapper = mount(PageHeader, {
      props: { title: "Accounts" },
      global: { plugins: [vuetify] },
    });

    expect(wrapper.findAll("p")).toHaveLength(0);
  });
});
