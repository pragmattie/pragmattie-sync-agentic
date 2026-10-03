<script setup>
defineProps({
  active: {
    type: Boolean,
    default: false,
  },
});
</script>

<template>
  <!-- Keeps its height when idle, so the page doesn't jump as requests start and stop. -->
  <div class="loading-bar mb-4">
    <v-progress-linear
      v-if="active"
      indeterminate
      color="secondary"
      height="4"
      aria-label="Loading"
      data-test="loading-bar"
    />
  </div>
</template>

<style scoped>
.loading-bar {
  height: 4px;
}
</style>
