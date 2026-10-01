import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import pkg from "../../package.json";
import { vuetify } from "../plugins/vuetify";
import AboutView from "../views/AboutView.vue";

function mountAbout() {
  return mount(AboutView, { global: { plugins: [vuetify] } });
}

describe("AboutView", () => {
  it("names the product and says what it is", () => {
    const wrapper = mountAbout();

    expect(wrapper.find("h1").text()).toBe("PragMattie Sync CRM");
    expect(wrapper.text()).toContain("A CRM for mid-market B2B sales teams");
  });

  it("shows the version from package.json", () => {
    const wrapper = mountAbout();

    expect(pkg.version).toMatch(/^\d+\.\d+\.\d+/);
    expect(wrapper.find("[data-test='version']").text()).toBe(`Version ${pkg.version}`);
  });

  it("lists the product's sections in this release", () => {
    const wrapper = mountAbout();

    expect(wrapper.text()).toContain("What's in this release");
    const titles = wrapper
      .findAll("[data-test='release-sections'] .v-list-item-title")
      .map((title) => title.text());
    expect(titles).toEqual(["Leads", "Accounts", "Pipeline", "Forecast"]);
  });
});
