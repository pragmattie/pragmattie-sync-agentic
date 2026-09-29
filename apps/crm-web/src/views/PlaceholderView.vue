<script setup>
defineProps({
  title: {
    type: String,
    required: true,
  },
});
</script>

<template>
  <v-container>
    <h1 class="text-h4 mb-2">{{ title }}</h1>
    <p class="text-body-1">This section is coming soon.</p>
  </v-container>
</template>
