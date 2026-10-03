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

defineEmits(["retry"]);
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
  >
    <template #append>
      <v-btn variant="text" data-test="retry" @click="$emit('retry')">Retry</v-btn>
    </template>
  </v-alert>
</template>
