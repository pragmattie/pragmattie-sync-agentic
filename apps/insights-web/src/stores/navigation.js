import { defineStore } from "pinia";

export const useNavigationStore = defineStore("navigation", {
  state: () => ({
    drawerOpen: true,
  }),
  actions: {
    toggleDrawer() {
      this.drawerOpen = !this.drawerOpen;
    },
  },
});
