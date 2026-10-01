<script setup>
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { sendJson } from "../api";
import { INDUSTRIES, REGIONS } from "../constants";

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  lead: {
    type: Object,
    default: null,
  },
});

const emit = defineEmits(["update:modelValue"]);

const router = useRouter();

const atLeastOne = (value) => (value !== "" && Number(value) >= 1) || "Must be at least 1";
const required = (value) => (!!value && !!String(value).trim()) || "Required";

function defaults(lead) {
  return {
    industry: "Technology",
    region: "North America East",
    employee_count: 100,
    create_opportunity: true,
    opportunity_name: `${lead?.company ?? ""} - New business`,
    opportunity_amount: 25000,
  };
}

const form = ref(null);
const fields = ref(defaults(props.lead));
const saving = ref(false);
const error = ref("");

const title = computed(
  () => `Convert ${props.lead?.first_name ?? ""} ${props.lead?.last_name ?? ""}`,
);
const subtitle = computed(
  () => `Creates an account for ${props.lead?.company ?? ""} with this person as a contact.`,
);

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      fields.value = defaults(props.lead);
      error.value = "";
    }
  },
  { immediate: true },
);

function close() {
  emit("update:modelValue", false);
}

async function convert() {
  const { valid } = await form.value.validate();
  if (!valid) return;
  saving.value = true;
  error.value = "";
  const payload = {
    industry: fields.value.industry,
    region: fields.value.region,
    employee_count: Number(fields.value.employee_count),
  };
  if (fields.value.create_opportunity) {
    payload.opportunity_name = fields.value.opportunity_name;
    payload.opportunity_amount = Number(fields.value.opportunity_amount);
  }
  try {
    const result = await sendJson("POST", `/api/v1/leads/${props.lead.id}/convert`, payload);
    close();
    await router.push(`/accounts/${result.account_id}`);
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
    <v-card :title="title" :subtitle="subtitle" data-test="convert-dialog">
      <v-form ref="form" @submit.prevent="convert">
        <v-card-text>
          <v-alert v-if="error" type="error" variant="tonal" class="mb-4" data-test="convert-error">
            {{ error }}
          </v-alert>
          <v-row density="compact">
            <v-col cols="12" sm="6">
              <v-select
                v-model="fields.industry"
                label="Industry"
                :items="INDUSTRIES"
                data-test="industry"
              />
            </v-col>
            <v-col cols="12" sm="6">
              <v-select v-model="fields.region" label="Region" :items="REGIONS" data-test="region" />
            </v-col>
            <v-col cols="12">
              <v-text-field
                v-model.number="fields.employee_count"
                label="Employees"
                type="number"
                min="1"
                :rules="[atLeastOne]"
                name="employee_count"
              />
            </v-col>
            <v-col cols="12">
              <v-switch
                v-model="fields.create_opportunity"
                label="Also create an opportunity"
                color="secondary"
                hide-details
                data-test="create-opportunity"
              />
            </v-col>
            <template v-if="fields.create_opportunity">
              <v-col cols="12" sm="7">
                <v-text-field
                  v-model="fields.opportunity_name"
                  label="Opportunity name"
                  :rules="[required]"
                  name="opportunity_name"
                />
              </v-col>
              <v-col cols="12" sm="5">
                <v-text-field
                  v-model.number="fields.opportunity_amount"
                  label="Amount (USD)"
                  type="number"
                  min="1"
                  :rules="[atLeastOne]"
                  name="opportunity_amount"
                />
              </v-col>
            </template>
          </v-row>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" data-test="cancel" @click="close">Cancel</v-btn>
          <v-btn type="submit" color="primary" variant="flat" :loading="saving" data-test="submit">
            Convert
          </v-btn>
        </v-card-actions>
      </v-form>
    </v-card>
  </v-dialog>
</template>
