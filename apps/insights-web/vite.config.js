import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";
import vuetify from "vite-plugin-vuetify";

// usePolling lets file-change detection work when the source is bind-mounted
// into a Linux container from a Windows host.
export default defineConfig({
  // autoImport bundles only the Vuetify components the app uses.
  plugins: [vue(), vuetify({ autoImport: true })],
  server: {
    host: true,
    port: 5174,
    strictPort: true,
    watch: {
      usePolling: true,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/tests/setup.js"],
    server: {
      deps: {
        inline: ["vuetify"],
      },
    },
  },
});
