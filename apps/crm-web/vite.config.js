import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// usePolling lets file-change detection work when the source is bind-mounted
// into a Linux container from a Windows host.
export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    port: 5173,
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
