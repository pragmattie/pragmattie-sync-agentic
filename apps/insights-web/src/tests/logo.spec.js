import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import AppLogo from "../components/AppLogo.vue";
import { faviconHref, logoColors, markSvg, setFavicon } from "../logo";

describe("AppLogo", () => {
  it("draws the mark inline in the theme colours", () => {
    const wrapper = mount(AppLogo);
    const html = wrapper.html();

    expect(wrapper.find("svg").attributes("aria-label")).toBe("PragMattie Sync");
    expect(wrapper.findAll("path")).toHaveLength(2);
    expect(html).toContain(logoColors.navy);
    expect(html).toContain(logoColors.teal);
    expect(html).toContain(logoColors.gold);
  });
});

describe("favicon", () => {
  it("is the mark as an inline SVG data URL", () => {
    expect(markSvg()).toContain(logoColors.navy);
    expect(faviconHref()).toBe(`data:image/svg+xml,${encodeURIComponent(markSvg())}`);
  });

  it("replaces the page's icon link", () => {
    document.head.innerHTML = '<link rel="icon" href="data:," />';

    setFavicon();

    const links = document.head.querySelectorAll("link[rel='icon']");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe(faviconHref());
  });
});
