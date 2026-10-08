<script setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
import {
  agentLabel,
  decisionCost,
  formatCost,
  formatNumber,
  formatTime,
  MISSING,
  prettyJson,
  signalRows,
  statusColor,
  subjectLabel,
} from "../decisions";

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  decision: {
    type: Object,
    default: null,
  },
  error: {
    type: String,
    default: "",
  },
});

defineEmits(["update:modelValue"]);

const route = useRoute();

function orMissing(value) {
  return value === undefined || value === null || value === "" ? MISSING : value;
}

const details = computed(() => {
  const row = props.decision;
  // The version and the hash are recorded separately; show each that is present.
  const prompt = [row.prompt_version, row.prompt_hash?.slice(0, 12)].filter(Boolean).join(" · ");
  return [
    ["Time", formatTime(row.created_at)],
    ["Model", orMissing(row.model_id)],
    ["Prompt", prompt || MISSING],
    ["Version decided on", orMissing(row.head_sha)],
    ["Attempt", orMissing(row.attempt)],
    ["Trigger", orMissing(row.trigger)],
    ["Latency", row.latency_ms == null ? MISSING : `${formatNumber(row.latency_ms)} ms`],
    [
      "Tokens",
      row.input_tokens == null && row.output_tokens == null
        ? MISSING
        : `${formatNumber(row.input_tokens)} in / ${formatNumber(row.output_tokens)} out`,
    ],
    ["Cost", formatCost(decisionCost(row))],
  ];
});

const override = computed(() => {
  const value = props.decision?.human_override;
  if (!value) return [];
  const rows = [
    ["Who", value.actor],
    ["From", value.from_tier],
    ["To", value.to_tier],
    ["Why", value.reason],
    ["Outcome", value.why],
    ["Changed", value.dimensions?.join(", ")],
  ];
  return rows.filter(([, text]) => text !== undefined && text !== null && text !== "");
});

const signals = computed(() => signalRows(props.decision?.signals));
</script>

<template>
  <v-navigation-drawer
    :model-value="modelValue"
    location="end"
    temporary
    disable-route-watcher
    width="560"
    data-test="decision-drawer"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div v-if="error" class="pa-4 d-flex align-start ga-2">
      <v-alert type="error" variant="tonal" class="flex-grow-1" data-test="drawer-load-error">
        {{ error }}
      </v-alert>
      <v-btn
        icon="mdi-close"
        variant="text"
        aria-label="Close"
        @click="$emit('update:modelValue', false)"
      />
    </div>
    <div v-else-if="decision" class="pa-4">
      <div class="d-flex align-start ga-2 mb-4">
        <div class="flex-grow-1">
          <h2 class="text-h6" data-test="drawer-title">
            {{ agentLabel(decision.agent) }} · {{ subjectLabel(decision) }}
          </h2>
          <v-chip
            :color="statusColor(decision.status)"
            size="small"
            variant="flat"
            class="mt-1"
            data-test="drawer-status"
          >
            {{ decision.status }}
          </v-chip>
        </div>
        <v-btn
          icon="mdi-close"
          variant="text"
          aria-label="Close"
          data-test="drawer-close"
          @click="$emit('update:modelValue', false)"
        />
      </div>

      <v-table density="compact" class="mb-4" data-test="drawer-details">
        <tbody>
          <tr v-for="[label, value] in details" :key="label">
            <th class="text-left">{{ label }}</th>
            <td class="text-break">{{ value }}</td>
          </tr>
        </tbody>
      </v-table>

      <div v-if="decision.error" class="mb-4">
        <h3 class="text-subtitle-1 mb-1">Error</h3>
        <pre class="raw error-box" data-test="drawer-error">{{ decision.error }}</pre>
      </div>

      <div v-if="override.length" class="mb-4" data-test="drawer-override">
        <h3 class="text-subtitle-1 mb-1">Changed by a person</h3>
        <v-table density="compact">
          <tbody>
            <tr v-for="[label, value] in override" :key="label" :data-test="`override-${label}`">
              <th class="text-left">{{ label }}</th>
              <td>{{ value }}</td>
            </tr>
          </tbody>
        </v-table>
      </div>

      <div v-if="signals.length" class="mb-4" data-test="drawer-signals">
        <h3 class="text-subtitle-1 mb-1">Signals</h3>
        <v-table density="compact">
          <thead>
            <tr>
              <th>Signal</th>
              <th class="text-right">Points</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in signals" :key="row.name">
              <td>{{ row.name }}</td>
              <td class="text-right">{{ row.points }}</td>
            </tr>
          </tbody>
        </v-table>
      </div>

      <div v-if="decision.output" class="mb-4">
        <h3 class="text-subtitle-1 mb-1">Output</h3>
        <pre class="raw" data-test="drawer-output">{{ prettyJson(decision.output) }}</pre>
      </div>

      <div v-if="decision.action_taken" class="mb-4">
        <h3 class="text-subtitle-1 mb-1">What it did</h3>
        <pre class="raw" data-test="drawer-action">{{ prettyJson(decision.action_taken) }}</pre>
      </div>

      <p v-if="decision.supersedes_id" class="text-body-2" data-test="drawer-supersedes">
        Replaces
        <router-link :to="{ query: { ...route.query, decision: decision.supersedes_id } }">
          decision #{{ decision.supersedes_id }}
        </router-link>
      </p>
    </div>
  </v-navigation-drawer>
</template>

<style scoped>
.raw {
  max-height: 320px;
  overflow: auto;
  padding: 12px;
  border-radius: 4px;
  background: rgba(46, 63, 85, 0.06);
  font-size: 0.8125rem;
  white-space: pre;
}

.error-box {
  white-space: pre-wrap;
  color: rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.08);
  border: 1px solid rgb(var(--v-theme-error));
}
</style>
