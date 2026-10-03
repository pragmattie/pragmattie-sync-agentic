import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { afterEach, describe, expect, it } from "vitest";
import App from "../App.vue";
import { vuetify } from "../plugins/vuetify";
import { navItems, pageTitle, router } from "../router";
import { useNavigationStore } from "../stores/navigation";

async function mountApp(path = "/signals") {
  await router.push(path);
  await router.isReady();

  const pinia = createPinia();
  const wrapper = mount(App, {
    global: {
      plugins: [pinia, router, vuetify],
    },
  });
  return { wrapper, navigation: useNavigationStore(pinia) };
}

enableAutoUnmount(afterEach);

describe("App", () => {
  it("shows the mark and the Delivery Insights wordmark in the app bar", async () => {
    const { wrapper } = await mountApp();

    const bar = wrapper.find("header");
    expect(bar.find("svg[aria-label='PragMattie Sync']").exists()).toBe(true);
    expect(bar.find("[data-test='wordmark']").text()).toBe("Delivery Insights");
  });

  it("lists the pages in the drawer", async () => {
    const { wrapper } = await mountApp();

    expect(navItems).toEqual([{ title: "Engineering signals", to: "/signals", name: "signals" }]);
    const drawer = wrapper.find("[data-test='nav-drawer']");
    const links = drawer.findAll("a");
    expect(links.map((link) => link.text())).toEqual(["Engineering signals"]);
    expect(links[0].attributes("href")).toBe("/signals");
  });

  it("toggles the drawer from the menu button", async () => {
    const { wrapper, navigation } = await mountApp();

    expect(navigation.drawerOpen).toBe(true);
    await wrapper.find("[data-test='nav-toggle']").trigger("click");
    expect(navigation.drawerOpen).toBe(false);
    await wrapper.find("[data-test='nav-toggle']").trigger("click");
    expect(navigation.drawerOpen).toBe(true);
  });

  it("credits PragMattie Growth Partners and says data is simulated in the footer", async () => {
    const { wrapper } = await mountApp();

    const footer = wrapper.find("footer");
    expect(footer.find("[data-test='credit']").text()).toBe(
      "PragMattie Sync is a fictional demo company created by PragMattie Growth Partners, LLC.",
    );
    expect(footer.find("[data-test='simulated-note']").text()).toBe(
      "Some data here is simulated; each page says how much.",
    );
  });

  it("shows the signals placeholder", async () => {
    const { wrapper } = await mountApp();

    expect(wrapper.find("h1").text()).toBe("Engineering signals");
    expect(wrapper.text()).toContain("Engineering signals are coming soon.");
  });

  it("links nowhere in the CRM", async () => {
    const { wrapper } = await mountApp();

    const hrefs = wrapper.findAll("a").map((link) => link.attributes("href"));
    expect(hrefs.every((href) => href.startsWith("/"))).toBe(true);
    expect(wrapper.html()).not.toContain("localhost:5173");
  });
});

describe("router", () => {
  it("redirects / to the signals page", async () => {
    await router.push("/");

    expect(router.currentRoute.value.path).toBe("/signals");
  });

  it("shows not found with a link back to signals for an unknown path", async () => {
    const { wrapper } = await mountApp("/no-such-page");

    expect(router.currentRoute.value.name).toBe("not-found");
    expect(wrapper.find("h1").text()).toBe("Page not found");
    expect(wrapper.find("[data-test='not-found-link']").attributes("href")).toBe("/signals");
  });

  it("titles the browser tab", async () => {
    await mountApp("/signals");

    expect(document.title).toBe("Engineering signals · Delivery Insights · PragMattie Sync");
    expect(pageTitle({ meta: {} })).toBe("Delivery Insights · PragMattie Sync");
  });
});
