import "@mdi/font/css/materialdesignicons.css";
import * as components from "vuetify/components";
import * as directives from "vuetify/directives";
import "@mdi/font/css/materialdesignicons.css";
import "vuetify/styles";
import { createVuetify } from "vuetify";

export const vuetify = createVuetify({
  components,
  directives,
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
