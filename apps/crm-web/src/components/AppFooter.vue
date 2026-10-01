<script setup>
const year = new Date().getFullYear();
</script>

<template>
  <v-footer class="d-flex flex-wrap justify-space-between ga-2 text-body-2 border-t">
    <span data-test="copyright">© {{ year }} PragMattie Sync</span>
    <router-link to="/about" class="text-secondary" data-test="about-link">About</router-link>
  </v-footer>
</template>
