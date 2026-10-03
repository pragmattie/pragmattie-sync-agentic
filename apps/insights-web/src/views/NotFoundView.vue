<script setup>
import PageHeader from "../components/PageHeader.vue";
</script>

<template>
  <v-container>
    <PageHeader title="Page not found" />
    <p class="text-body-1">
      There's no page at this address.
      <router-link to="/signals" class="text-secondary" data-test="not-found-link">
        Go to Engineering signals
      </router-link>
    </p>
  </v-container>
</template>
