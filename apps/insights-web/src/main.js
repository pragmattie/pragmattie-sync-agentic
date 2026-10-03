import { createPinia } from "pinia";
import { createApp } from "vue";
import App from "./App.vue";
import { setFavicon } from "./logo";
import { vuetify } from "./plugins/vuetify";
import { router } from "./router";

setFavicon();

createApp(App).use(createPinia()).use(router).use(vuetify).mount("#app");
