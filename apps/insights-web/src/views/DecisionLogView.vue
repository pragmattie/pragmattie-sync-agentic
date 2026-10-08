<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { getJson } from "../api";
import DecisionDrawer from "../components/DecisionDrawer.vue";
import LoadError from "../components/LoadError.vue";
import LoadingBar from "../components/LoadingBar.vue";
import PageHeader from "../components/PageHeader.vue";
import {
  agentLabel,
  decisionCost,
  FILTER_KEYS,
  filtersFromQuery,
  formatCost,
  formatTime,
  kindOf,
  listParams,
  PAGE_SIZE,
  SOURCE_OPTIONS,
  STATUS_OPTIONS,
  statusColor,
  SUBJECT_OPTIONS,
  subjectLabel,
  TIER_OPTIONS,
  totalsLine,
} from "../decisions";

const route = useRoute();
const router = useRouter();

const headers = [
  { title: "Time", key: "created_at", sortable: false },
  { title: "Agent", key: "agent", sortable: false },
  { title: "Subject", key: "subject", sortable: false },
  { title: "Tier", key: "tier", sortable: false },
  { title: "Score", key: "final_score", sortable: false, align: "end" },
  { title: "Status", key: "status", sortable: false },
  { title: "Trigger", key: "trigger", sortable: false },
  { title: "Cost", key: "cost", sortable: false, align: "end" },
];

const state = computed(() => filtersFromQuery(route.query));
const filters = computed(() => state.value.filters);
const page = computed(() => state.value.page);
const hasFilters = computed(() => Object.keys(filters.value).length > 0);

const loading = ref(false);
const error = ref("");
const result = ref(null);
const agents = ref([]);

const agentOptions = computed(() =>
  agents.value.map((row) => ({ title: agentLabel(row.agent), value: row.agent })),
);
const rows = computed(() => result.value?.decisions ?? []);
const total = computed(() => result.value?.total ?? 0);
const summary = computed(() => result.value && totalsLine(result.value.totals, total.value));

let latest = 0;

async function load() {
  const request = ++latest;
  loading.value = true;
  error.value = "";
  try {
    const body = await getJson("/api/v1/signals/decisions", listParams(filters.value, page.value));
    if (request === latest) result.value = body;
  } catch (err) {
    if (request === latest) error.value = err.message;
  } finally {
    if (request === latest) loading.value = false;
  }
}

async function loadAgents() {
  try {
    agents.value = await getJson("/api/v1/signals/decisions/agents");
  } catch {
    // The list itself reports a failing server; the agent filter just stays empty.
  }
}

onMounted(loadAgents);

// Re-query whenever the filters or the page in the URL change, but not when
// only the open decision does.
watch(() => JSON.stringify(state.value), load, { immediate: true });

function updateQuery(changes) {
  const query = { ...route.query, ...changes };
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") delete query[key];
  }
  router.replace({ query });
}

function setFilter(key, value) {
  updateQuery({ [key]: value === "all" ? undefined : value, page: undefined });
}

function setPage(value) {
  if (value !== page.value) updateQuery({ page: value > 1 ? String(value) : undefined });
}

function clearFilters() {
  updateQuery({ ...Object.fromEntries(FILTER_KEYS.map((key) => [key, undefined])), page: null });
}

// The open decision lives in the URL too, so a link to one can be shared.
const openId = computed(() => {
  const value = Number.parseInt(route.query.decision, 10);
  return Number.isNaN(value) ? null : value;
});
const selected = ref(null);
const drawerError = ref("");
const drawerOpen = computed({
  get: () => openId.value !== null,
  set: (open) => {
    if (!open) updateQuery({ decision: undefined });
  },
});

let latestDecision = 0;

// A decision that fails to load (a stale shared link, say) reports in the
// drawer and leaves the list and totals alone.
watch(
  openId,
  async (id) => {
    const request = ++latestDecision;
    selected.value = null;
    drawerError.value = "";
    if (id === null) return;
    const row = rows.value.find((item) => item.id === id);
    if (row) {
      selected.value = row;
      return;
    }
    try {
      const body = await getJson(`/api/v1/signals/decisions/${id}`);
      if (request === latestDecision) selected.value = body;
    } catch {
      if (request === latestDecision) drawerError.value = `Couldn't load decision ${id}.`;
    }
  },
  { immediate: true },
);

function openDecision(_event, { item }) {
  updateQuery({ decision: String(item.id) });
}

function rowProps({ item }) {
  return { class: "decision-row", "data-test": `row-${item.id}` };
}
</script>

<template>
  <v-container fluid>
    <PageHeader
      title="Decision log"
      subtitle="Every decision the agents made: what they saw, what they decided, what they did and what it cost."
    />
    <LoadingBar :active="loading" />
    <LoadError title="Couldn't load the decision log" :message="error" @retry="load" />

    <v-row density="compact" class="mb-2" data-test="filters">
      <v-col cols="12" sm="6" md="3">
        <v-select
          :model-value="filters.agent ?? null"
          :items="agentOptions"
          label="Agent"
          density="compact"
          variant="outlined"
          clearable
          hide-details
          data-test="filter-agent"
          @update:model-value="setFilter('agent', $event)"
        />
      </v-col>
      <v-col cols="12" sm="6" md="2">
        <v-select
          :model-value="filters.subject ?? null"
          :items="SUBJECT_OPTIONS"
          label="Subject"
          density="compact"
          variant="outlined"
          clearable
          hide-details
          data-test="filter-subject"
          @update:model-value="setFilter('subject', $event)"
        />
      </v-col>
      <v-col cols="12" sm="6" md="2">
        <v-select
          :model-value="filters.status ?? null"
          :items="STATUS_OPTIONS"
          label="Status"
          density="compact"
          variant="outlined"
          clearable
          hide-details
          data-test="filter-status"
          @update:model-value="setFilter('status', $event)"
        />
      </v-col>
      <v-col cols="12" sm="6" md="2">
        <v-select
          :model-value="filters.tier ?? null"
          :items="TIER_OPTIONS"
          label="Tier"
          density="compact"
          variant="outlined"
          clearable
          hide-details
          data-test="filter-tier"
          @update:model-value="setFilter('tier', $event)"
        />
      </v-col>
      <v-col cols="12" md="3" class="d-flex align-center">
        <v-btn-toggle
          :model-value="filters.source ?? 'all'"
          mandatory
          density="compact"
          variant="outlined"
          color="primary"
          divided
          aria-label="Source"
          data-test="filter-source"
          @update:model-value="setFilter('source', $event)"
        >
          <v-btn
            v-for="option in SOURCE_OPTIONS"
            :key="option.value"
            :value="option.value"
            :data-test="`source-${option.value}`"
          >
            {{ option.title }}
          </v-btn>
        </v-btn-toggle>
      </v-col>
    </v-row>

    <p v-if="summary && !error" class="text-body-2 text-medium-emphasis mb-2" data-test="totals">
      {{ summary }}
    </p>

    <v-data-table-server
      :headers="headers"
      :items="rows"
      :items-length="total"
      :items-per-page="PAGE_SIZE"
      :items-per-page-options="[{ value: PAGE_SIZE, title: String(PAGE_SIZE) }]"
      :page="page"
      :loading="loading"
      :row-props="rowProps"
      item-value="id"
      density="comfortable"
      hover
      data-test="decisions-table"
      @update:page="setPage"
      @click:row="openDecision"
    >
      <template #[`item.created_at`]="{ item }">
        <span class="text-no-wrap">{{ formatTime(item.created_at) }}</span>
      </template>
      <template #[`item.agent`]="{ item }">{{ agentLabel(item.agent) }}</template>
      <template #[`item.subject`]="{ item }">
        <div class="d-flex align-center ga-2">
          <span>{{ subjectLabel(item) }}</span>
          <v-chip
            v-if="item.subject_source === 'synthetic'"
            size="x-small"
            variant="tonal"
            prepend-icon="mdi-flask-outline"
            data-test="chip-simulated"
          >
            Simulated
          </v-chip>
        </div>
        <div class="text-caption text-medium-emphasis">{{ kindOf(item) }}</div>
      </template>
      <template #[`item.tier`]="{ item }">{{ item.tier ?? "—" }}</template>
      <template #[`item.final_score`]="{ item }">{{ item.final_score ?? "—" }}</template>
      <template #[`item.status`]="{ item }">
        <v-chip :color="statusColor(item.status)" size="small" variant="flat">
          {{ item.status }}
        </v-chip>
      </template>
      <template #[`item.trigger`]="{ item }">
        <v-chip
          v-if="item.trigger === 'trial'"
          color="secondary"
          size="small"
          variant="tonal"
          data-test="chip-trial"
        >
          Trial
        </v-chip>
        <span v-else>{{ item.trigger }}</span>
      </template>
      <template #[`item.cost`]="{ item }">{{ formatCost(decisionCost(item)) }}</template>
      <template #no-data>
        <div class="py-6" data-test="empty">
          <p class="text-body-1 mb-2">No decisions match these filters.</p>
          <v-btn
            v-if="hasFilters"
            variant="outlined"
            color="primary"
            data-test="clear-filters"
            @click="clearFilters"
          >
            Clear filters
          </v-btn>
        </div>
      </template>
    </v-data-table-server>

    <DecisionDrawer
      v-model="drawerOpen"
      :decision="drawerOpen ? selected : null"
      :error="drawerOpen ? drawerError : ''"
    />
  </v-container>
</template>

<style scoped>
:deep(.decision-row) {
  cursor: pointer;
}
</style>
