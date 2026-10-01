import "@mdi/font/css/materialdesignicons.css";
import { createVuetify } from "vuetify";
import "vuetify/styles";

// Components are imported where they're used (vite-plugin-vuetify), so only
// those reach the bundle.
export const vuetify = createVuetify({
  theme: {
    defaultTheme: "pragmattieSync",
    themes: {
      pragmattieSync: {
        dark: false,
        colors: {
          primary: "#2E3F55",
          secondary: "#1B8A94",
          accent: "#E8B35A",
        },
      },
    },
  },
});
