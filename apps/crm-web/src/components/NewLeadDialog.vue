<script setup>
import { ref, watch } from "vue";
import { sendJson } from "../api";
import { LEAD_SOURCES } from "../constants";

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  ownerOptions: {
    type: Array,
    default: () => [],
  },
});

const emit = defineEmits(["update:modelValue", "created"]);

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const required = (value) => (!!value && !!String(value).trim()) || "Required";
const validEmail = (value) => EMAIL_PATTERN.test(value || "") || "Enter a valid email";

function blankLead() {
  return {
    first_name: "",
    last_name: "",
    email: "",
    company: "",
    title: "",
    source: "web",
    owner_id: null,
    score: 50,
  };
}

const form = ref(null);
const lead = ref(blankLead());
const saving = ref(false);
const error = ref("");

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      lead.value = blankLead();
      error.value = "";
    }
  },
);

function close() {
  emit("update:modelValue", false);
}

async function save() {
  const { valid } = await form.value.validate();
  if (!valid) return;
  saving.value = true;
  error.value = "";
  try {
    const created = await sendJson("POST", "/api/v1/leads", {
      ...lead.value,
      title: lead.value.title || null,
    });
    emit("created", created);
    close();
  } catch (err) {
    error.value = err.message;
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    max-width="560"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card title="New lead">
      <v-form ref="form" @submit.prevent="save">
        <v-card-text>
          <v-alert v-if="error" type="error" variant="tonal" class="mb-4" data-test="form-error">
            {{ error }}
          </v-alert>
          <v-row density="compact">
            <v-col cols="12" sm="6">
              <v-text-field
                v-model="lead.first_name"
                label="First name"
                :rules="[required]"
                name="first_name"
              />
            </v-col>
            <v-col cols="12" sm="6">
              <v-text-field
                v-model="lead.last_name"
                label="Last name"
                :rules="[required]"
                name="last_name"
              />
            </v-col>
            <v-col cols="12">
              <v-text-field
                v-model="lead.email"
                label="Email"
                type="email"
                :rules="[required, validEmail]"
                name="email"
              />
            </v-col>
            <v-col cols="12">
              <v-text-field
                v-model="lead.company"
                label="Company"
                :rules="[required]"
                name="company"
              />
            </v-col>
            <v-col cols="12">
              <v-text-field v-model="lead.title" label="Job title" name="title" />
            </v-col>
            <v-col cols="12" sm="6">
              <v-select v-model="lead.source" label="Source" :items="LEAD_SOURCES" />
            </v-col>
            <v-col cols="12" sm="6">
              <v-select v-model="lead.owner_id" label="Owner" :items="ownerOptions" clearable />
            </v-col>
            <v-col cols="12">
              <v-slider
                v-model="lead.score"
                label="Score"
                :min="0"
                :max="100"
                :step="1"
                thumb-label
                color="secondary"
              >
                <template #append>
                  <span class="text-body-2" style="min-width: 2.5em">{{ lead.score }}</span>
                </template>
              </v-slider>
            </v-col>
          </v-row>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="close">Cancel</v-btn>
          <v-btn type="submit" color="primary" variant="flat" :loading="saving" data-test="save">
            Save
          </v-btn>
        </v-card-actions>
      </v-form>
    </v-card>
  </v-dialog>
</template>
