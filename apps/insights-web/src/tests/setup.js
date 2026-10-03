// jsdom has neither of these, but Vuetify's layout components expect them.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub;

// The build test runs in Node, where there is no window to patch.
if (typeof window !== "undefined") {
  window.matchMedia =
    window.matchMedia ||
    function matchMedia() {
      return {
        matches: false,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
      };
    };
}

// Vuetify's overlays (menus, dialogs) position themselves against it.
globalThis.visualViewport = globalThis.visualViewport || {
  width: 1024,
  height: 768,
  offsetLeft: 0,
  offsetTop: 0,
  scale: 1,
  addEventListener: () => {},
  removeEventListener: () => {},
};
