<script setup>
import logo from "../assets/pragmattie-growth-partners-logo.jpg";
</script>

<template>
  <v-footer app class="d-flex flex-wrap justify-space-between align-center ga-2 text-body-2 border-t">
    <span class="d-flex flex-wrap align-center ga-3" data-test="credit">
      PragMattie Sync is a fictional demo company created by
      <img :src="logo" alt="PragMattie Growth Partners, LLC" class="brand-logo" />
    </span>
    <span class="text-medium-emphasis" data-test="simulated-note">
      Some data here is simulated; each page says how much.
    </span>
  </v-footer>
</template>

<style scoped>
.brand-logo {
  height: 34px;
}
</style>
