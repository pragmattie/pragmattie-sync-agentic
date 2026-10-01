<script setup>
import { SNACKBAR_TIMEOUT_MS, useSnackbarStore } from "../stores/snackbar";

const snackbar = useSnackbarStore();
</script>

<template>
  <v-snackbar
    v-model="snackbar.show"
    :color="snackbar.color"
    :timeout="SNACKBAR_TIMEOUT_MS"
    data-test="snackbar"
  >
    {{ snackbar.text }}
  </v-snackbar>
</template>
