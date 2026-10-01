<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { getJson, sendJson } from "../api";
import LoadError from "../components/LoadError.vue";
import LoadingBar from "../components/LoadingBar.vue";
import PageHeader from "../components/PageHeader.vue";
import { OPEN_STAGES, STAGES } from "../constants";
import { money, moneyFull, shortDate } from "../format";
import { quarterLabel, quarterOf, quarterRange, shiftQuarter } from "../quarters";
import { useRepsStore } from "../stores/reps";
import { useSnackbarStore } from "../stores/snackbar";

const LIMIT = 500;

const reps = useRepsStore();
const snackbar = useSnackbarStore();

const thisQuarter = quarterOf();
const nextQuarter = shiftQuarter(thisQuarter, 1);
const windows = [
  { value: "this", title: `This quarter (${quarterLabel(thisQuarter)})`, quarter: thisQuarter },
  { value: "next", title: `Next quarter (${quarterLabel(nextQuarter)})`, quarter: nextQuarter },
  { value: "all", title: "All open deals", quarter: null },
];

const openStages = STAGES.filter((stage) => stage.open);

const windowValue = ref("this");
const ownerId = ref(null);

// null until the current window and owner have loaded, so the summary and
// board never show another selection's deals.
const deals = ref(null);
const loading = ref(false);
const loadError = ref("");

const params = computed(() => {
  const { quarter } = windows.find((option) => option.value === windowValue.value);
  const range = quarter ? quarterRange(quarter) : null;
  return {
    stage: OPEN_STAGES,
    owner_id: ownerId.value,
    close_from: range?.start,
    close_to: range?.end,
    limit: LIMIT,
  };
});

let requestId = 0;
// After a move the selection hasn't changed, so the board stays up while it refreshes.
async function load({ keepDeals = false } = {}) {
  const id = ++requestId;
  loading.value = true;
  loadError.value = "";
  if (!keepDeals) deals.value = null;
  try {
    const body = await getJson("/api/v1/opportunities", params.value);
    if (id !== requestId) return;
    deals.value = body.items;
  } catch (err) {
    if (id !== requestId) return;
    loadError.value = err.message;
    deals.value = null;
  } finally {
    if (id === requestId) loading.value = false;
  }
}

watch(params, () => load(), { deep: true });

onMounted(() => {
  load();
  reps.load().catch(() => {});
});

function sumAmounts(items) {
  return items.reduce((sum, deal) => sum + Number(deal.amount), 0);
}

const summary = computed(() => {
  const items = deals.value ?? [];
  return {
    open: sumAmounts(items),
    weighted: items.reduce((sum, deal) => sum + (Number(deal.amount) * deal.probability) / 100, 0),
    count: items.length,
  };
});

const columns = computed(() =>
  openStages.map((stage) => {
    const items = (deals.value ?? []).filter((deal) => deal.stage === stage.value);
    return { ...stage, deals: items, total: sumAmounts(items) };
  }),
);

function moveOptions(deal) {
  return STAGES.filter((stage) => stage.value !== deal.stage);
}

async function moveDeal(deal, stage) {
  try {
    await sendJson("PATCH", `/api/v1/opportunities/${deal.id}`, { stage: stage.value });
  } catch (err) {
    snackbar.error(err.message);
    return;
  }
  snackbar.confirm(`${deal.name} moved to ${stage.title}`);
  await load({ keepDeals: true });
}
</script>

<template>
  <v-container fluid>
    <PageHeader
      title="Pipeline"
      subtitle="Open opportunities by stage. Use a card's menu to move it forward."
    >
      <v-select
        v-model="windowValue"
        :items="windows"
        label="Window"
        density="compact"
        hide-details
        style="min-width: 240px"
        data-test="window"
      />
      <v-select
        v-model="ownerId"
        :items="reps.options"
        label="Owner"
        density="compact"
        hide-details
        clearable
        style="min-width: 180px"
        data-test="owner"
      />
    </PageHeader>

    <LoadingBar :active="loading" />

    <LoadError title="Couldn't load the pipeline" :message="loadError" />

    <template v-if="deals">
      <div class="d-flex flex-wrap ga-6 mb-4 text-body-1" data-test="summary">
        <span>
          Open pipeline:
          <strong data-test="summary-open">{{ moneyFull(summary.open) }}</strong>
        </span>
        <span>
          Weighted:
          <strong data-test="summary-weighted">{{ moneyFull(summary.weighted) }}</strong>
        </span>
        <span>
          Deals:
          <strong data-test="summary-count">{{ summary.count }}</strong>
        </span>
      </div>

      <div class="board d-flex ga-4 pb-2">
        <v-card
          v-for="column in columns"
          :key="column.value"
          class="column flex-grow-1"
          variant="tonal"
          :data-test="`column-${column.value}`"
        >
          <v-card-item>
            <div class="d-flex align-center justify-space-between">
              <span class="font-weight-bold">
                {{ column.title }}
                <span class="text-grey" data-test="column-count">({{ column.deals.length }})</span>
              </span>
              <span data-test="column-total">{{ money(column.total) }}</span>
            </div>
            <div class="text-caption text-grey">{{ column.probability }}% win probability</div>
          </v-card-item>

          <v-card-text class="d-flex flex-column ga-3">
            <div v-if="!column.deals.length" class="text-grey" data-test="column-empty">
              No deals
            </div>
            <v-card
              v-for="deal in column.deals"
              :key="deal.id"
              variant="elevated"
              data-test="deal"
            >
              <v-card-text>
                <div class="d-flex align-start justify-space-between ga-2">
                  <router-link
                    :to="`/accounts/${deal.account_id}`"
                    class="font-weight-bold text-primary"
                    data-test="deal-name"
                  >
                    {{ deal.name }}
                  </router-link>
                  <v-menu>
                    <template #activator="{ props: activator }">
                      <v-btn
                        v-bind="activator"
                        icon="mdi-dots-vertical"
                        variant="text"
                        size="x-small"
                        aria-label="Deal actions"
                        data-test="deal-actions"
                      />
                    </template>
                    <v-list density="compact">
                      <v-list-item
                        v-for="stage in moveOptions(deal)"
                        :key="stage.value"
                        :title="`Move to ${stage.title}`"
                        :data-test="`move-${stage.value}`"
                        @click="moveDeal(deal, stage)"
                      />
                    </v-list>
                  </v-menu>
                </div>
                <div class="d-flex justify-space-between mt-1">
                  <span data-test="deal-amount">{{ moneyFull(deal.amount) }}</span>
                  <span class="text-grey">{{ shortDate(deal.close_date) }}</span>
                </div>
                <div class="text-caption mt-1">
                  <span v-if="deal.owner">{{ deal.owner.name }}</span>
                  <span v-else class="text-grey">Unassigned</span>
                </div>
              </v-card-text>
            </v-card>
          </v-card-text>
        </v-card>
      </div>
    </template>
  </v-container>
</template>

<style scoped>
.board {
  overflow-x: auto;
}

.column {
  min-width: 240px;
  flex-basis: 0;
}
</style>
