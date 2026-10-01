<script setup>
defineProps({
  title: {
    type: String,
    required: true,
  },
  subtitle: {
    type: String,
    default: "",
  },
});
</script>

<template>
  <div class="d-flex flex-wrap align-center ga-4 mb-6">
    <div class="flex-grow-1">
      <h1 class="text-h4">{{ title }}</h1>
      <p v-if="subtitle" class="text-body-1 text-medium-emphasis mt-1">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.default" class="d-flex flex-wrap align-center ga-2">
      <slot />
    </div>
  </div>
</template>
