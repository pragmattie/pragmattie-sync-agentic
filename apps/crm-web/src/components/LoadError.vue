<script setup>
defineProps({
  title: {
    type: String,
    required: true,
  },
  message: {
    type: String,
    default: "",
  },
});
</script>

<template>
  <v-alert
    v-if="message"
    type="error"
    variant="tonal"
    :title="title"
    :text="message"
    class="mb-4"
    data-test="load-error"
  />
</template>
