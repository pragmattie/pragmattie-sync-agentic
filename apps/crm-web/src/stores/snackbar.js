import { defineStore } from "pinia";

export const SNACKBAR_TIMEOUT_MS = 3000;

// One snackbar for the whole app, shown by AppSnackbar in App.vue, so a
// confirmation survives a route change (e.g. converting a lead).
export const useSnackbarStore = defineStore("snackbar", {
  state: () => ({
    show: false,
    text: "",
    color: "primary",
  }),
  actions: {
    confirm(text) {
      this.text = text;
      this.color = "primary";
      this.show = true;
    },
    error(text) {
      this.text = text;
      this.color = "error";
      this.show = true;
    },
  },
});
