import { defineStore } from "pinia";
import { getJson } from "../api";

export const useRepsStore = defineStore("reps", {
  state: () => ({
    reps: [],
    loaded: false,
    loading: null,
    error: null,
  }),
  getters: {
    options: (state) => state.reps.map((rep) => ({ value: rep.id, title: rep.name })),
  },
  actions: {
    // Loads the reps once; later calls share the same result.
    load() {
      if (this.loaded) return Promise.resolve(this.reps);
      if (!this.loading) {
        this.error = null;
        this.loading = getJson("/api/v1/reps")
          .then((reps) => {
            this.reps = reps;
            this.loaded = true;
            return reps;
          })
          .catch((error) => {
            this.error = error.message;
            throw error;
          })
          .finally(() => {
            this.loading = null;
          });
      }
      return this.loading;
    },
  },
});
