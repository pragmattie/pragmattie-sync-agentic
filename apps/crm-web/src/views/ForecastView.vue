<script setup>
import { Bar } from "vue-chartjs";
import { computed, onMounted, ref, watch } from "vue";
import { getJson } from "../api";
import { monthStacks, quarterOptions, quotaMeter, stageBars } from "../charts/forecast";
import { barValueLabels, LABEL_COLOR, moneyAxis, moneyTooltip } from "../charts/setup";
import PageHeader from "../components/PageHeader.vue";
import { FORECAST_COLORS } from "../constants";
import { money, moneyFull, monthLabel, percent } from "../format";

const quarters = quarterOptions();
const quarter = ref(quarters[2].value);

const forecast = ref(null);
const loading = ref(false);
const loadError = ref("");

let requestId = 0;
async function load() {
  const id = ++requestId;
  loading.value = true;
  loadError.value = "";
  try {
    const body = await getJson("/api/v1/forecast", { quarter: quarter.value });
    if (id !== requestId) return;
    forecast.value = body;
  } catch (err) {
    if (id !== requestId) return;
    loadError.value = err.message;
  } finally {
    if (id === requestId) loading.value = false;
  }
}

watch(quarter, load);
onMounted(load);

function ofQuota(value) {
  const quota = Number(forecast.value.quota);
  return quota > 0 ? percent((Number(value) / quota) * 100) : percent(0);
}

const tiles = computed(() => {
  const f = forecast.value;
  if (!f) return [];
  return [
    { key: "quota", label: "Quota", value: money(f.quota), note: "Sum of rep quotas" },
    { key: "won", label: "Closed won", value: money(f.won), note: `${ofQuota(f.won)} of quota` },
    {
      key: "commit",
      label: "Commit",
      value: money(f.commit),
      note: `Won + negotiation · ${ofQuota(f.commit)}`,
    },
    {
      key: "best-case",
      label: "Best case",
      value: money(f.best_case),
      note: `Commit + proposal · ${ofQuota(f.best_case)}`,
    },
    {
      key: "weighted",
      label: "Weighted",
      value: money(f.weighted),
      note: "Won + open deals × win probability",
    },
  ];
});

const legend = [
  { key: "won", label: "Closed won", color: FORECAST_COLORS.won },
  { key: "negotiation", label: "Negotiation", color: FORECAST_COLORS.negotiation },
  { key: "proposal", label: "Proposal", color: FORECAST_COLORS.proposal },
];

const meter = computed(() => (forecast.value ? quotaMeter(forecast.value) : null));

const meterLabel = computed(() => {
  const f = forecast.value;
  if (!f) return "";
  return (
    `Closed won ${moneyFull(f.won)}, commit ${moneyFull(f.commit)} ` +
    `and best case ${moneyFull(f.best_case)} against a quota of ${moneyFull(f.quota)}`
  );
});

const monthView = ref("chart");

const monthChart = computed(() => {
  const stacks = monthStacks(forecast.value);
  return {
    labels: stacks.labels,
    datasets: legend.map((item) => ({
      label: item.label,
      data: stacks[item.key],
      backgroundColor: item.color,
      maxBarThickness: 64,
    })),
  };
});

const monthOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false }, tooltip: moneyTooltip },
  scales: {
    x: { stacked: true, grid: { display: false }, ticks: { color: LABEL_COLOR } },
    y: moneyAxis({ stacked: true }),
  },
};

const stageChart = computed(() => {
  const bars = stageBars(forecast.value);
  return {
    labels: bars.labels,
    datasets: [
      {
        label: "Open pipeline",
        data: bars.values,
        backgroundColor: FORECAST_COLORS.negotiation,
        maxBarThickness: 28,
      },
    ],
  };
});

const stageOptions = {
  indexAxis: "y",
  responsive: true,
  maintainAspectRatio: false,
  layout: { padding: { right: 56 } },
  plugins: { legend: { display: false }, tooltip: moneyTooltip },
  scales: {
    x: moneyAxis(),
    y: { grid: { display: false }, ticks: { color: LABEL_COLOR } },
  },
};

const stagePlugins = [barValueLabels];

const repHeaders = [
  { title: "Rep", key: "rep", sortable: false },
  { title: "Quota", key: "quota", align: "end", sortable: false },
  { title: "Closed won", key: "won", align: "end", sortable: false },
  { title: "Commit", key: "commit", align: "end", sortable: false },
  { title: "Weighted", key: "weighted", align: "end", sortable: false },
  { title: "Attainment", key: "attainment", sortable: false },
];
</script>

<template>
  <v-container fluid>
    <PageHeader
      title="Forecast"
      subtitle="Where the quarter will land, based on deal stage and close date."
    >
      <v-select
        v-model="quarter"
        :items="quarters"
        label="Quarter"
        density="compact"
        hide-details
        style="min-width: 200px"
        data-test="quarter"
      />
    </PageHeader>

    <v-alert v-if="loadError" type="error" variant="tonal" class="mb-4" data-test="forecast-error">
      Couldn't load the forecast: {{ loadError }}
    </v-alert>

    <v-progress-linear v-if="loading" indeterminate color="secondary" class="mb-2" />

    <template v-if="forecast">
      <v-row class="mb-2">
        <v-col v-for="tile in tiles" :key="tile.key" cols="12" sm="6" md="">
          <v-card class="h-100" :data-test="`tile-${tile.key}`">
            <v-card-text>
              <div class="text-subtitle-1 text-medium-emphasis" data-test="tile-label">
                {{ tile.label }}
              </div>
              <div class="text-h4 font-weight-bold my-1" data-test="tile-value">
                {{ tile.value }}
              </div>
              <div class="text-body-2 text-medium-emphasis" data-test="tile-note">
                {{ tile.note }}
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>

      <v-card class="mb-6">
        <v-card-title>Progress to quota</v-card-title>
        <v-card-text>
          <div class="text-body-1 mb-6" data-test="meter-summary">
            <strong>{{ moneyFull(forecast.won) }}</strong> of
            {{ moneyFull(forecast.quota) }} closed
          </div>
          <div class="meter" role="img" :aria-label="meterLabel" data-test="meter">
            <div class="meter-track">
              <div
                v-for="item in legend"
                :key="item.key"
                class="meter-segment"
                :style="{ width: `${meter[item.key]}%`, backgroundColor: item.color }"
                :data-test="`segment-${item.key}`"
              />
            </div>
            <div class="meter-quota" :style="{ left: `${meter.quota}%` }" data-test="quota-marker">
              <span class="meter-quota-label">Quota</span>
            </div>
          </div>
          <div class="d-flex flex-wrap ga-4 mt-3 text-body-2" data-test="meter-legend">
            <span v-for="item in legend" :key="item.key" class="d-flex align-center ga-1">
              <span class="swatch" :style="{ backgroundColor: item.color }" />
              {{ item.label }}
            </span>
          </div>
        </v-card-text>
      </v-card>

      <v-row>
        <v-col cols="12" md="6">
          <v-card class="h-100">
            <v-card-title class="d-flex align-center">
              <span class="flex-grow-1">Forecast by month</span>
              <v-btn-toggle
                v-model="monthView"
                mandatory
                density="compact"
                variant="outlined"
                divided
                data-test="month-view"
              >
                <v-btn value="chart" size="small" data-test="month-view-chart">Chart</v-btn>
                <v-btn value="table" size="small" data-test="month-view-table">Table</v-btn>
              </v-btn-toggle>
            </v-card-title>
            <v-card-text>
              <template v-if="monthView === 'chart'">
                <div class="d-flex flex-wrap ga-4 mb-2 text-body-2">
                  <span v-for="item in legend" :key="item.key" class="d-flex align-center ga-1">
                    <span class="swatch" :style="{ backgroundColor: item.color }" />
                    {{ item.label }}
                  </span>
                </div>
                <div class="chart" data-test="month-chart">
                  <Bar :data="monthChart" :options="monthOptions" />
                </div>
              </template>
              <v-table v-else density="compact" data-test="month-table">
                <thead>
                  <tr>
                    <th>Month</th>
                    <th class="text-end">Closed won</th>
                    <th class="text-end">Commit</th>
                    <th class="text-end">Best case</th>
                    <th class="text-end">Weighted</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="month in forecast.by_month" :key="month.month">
                    <td>{{ monthLabel(month.month) }}</td>
                    <td class="text-end">{{ moneyFull(month.won) }}</td>
                    <td class="text-end">{{ moneyFull(month.commit) }}</td>
                    <td class="text-end">{{ moneyFull(month.best_case) }}</td>
                    <td class="text-end">{{ moneyFull(month.weighted) }}</td>
                  </tr>
                </tbody>
              </v-table>
            </v-card-text>
          </v-card>
        </v-col>

        <v-col cols="12" md="6">
          <v-card class="h-100">
            <v-card-title>Open pipeline by stage</v-card-title>
            <v-card-text>
              <div class="chart" data-test="stage-chart">
                <Bar :data="stageChart" :options="stageOptions" :plugins="stagePlugins" />
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>

      <v-card class="mt-6">
        <v-card-title>By rep</v-card-title>
        <v-data-table
          :headers="repHeaders"
          :items="forecast.by_rep ?? []"
          item-value="rep.id"
          :items-per-page="-1"
          hide-default-footer
          density="comfortable"
          data-test="rep-table"
        >
          <template #[`item.rep`]="{ item }">{{ item.rep.name }}</template>
          <template #[`item.quota`]="{ item }">{{ moneyFull(item.quota) }}</template>
          <template #[`item.won`]="{ item }">{{ moneyFull(item.won) }}</template>
          <template #[`item.commit`]="{ item }">{{ moneyFull(item.commit) }}</template>
          <template #[`item.weighted`]="{ item }">{{ moneyFull(item.weighted) }}</template>
          <template #[`item.attainment`]="{ item }">
            <div class="d-flex align-center ga-2" data-test="attainment">
              <div class="attainment-track">
                <div
                  class="attainment-bar"
                  :style="{ width: `${Math.min(100, Math.max(0, item.attainment_pct))}%` }"
                  data-test="attainment-bar"
                />
              </div>
              <span>{{ percent(item.attainment_pct) }}</span>
            </div>
          </template>
        </v-data-table>
      </v-card>
    </template>
  </v-container>
</template>

<style scoped>
.meter {
  position: relative;
}

.meter-track {
  display: flex;
  height: 28px;
  border-radius: 4px;
  overflow: hidden;
  background: #e6e8eb;
}

.meter-segment {
  height: 100%;
}

.meter-quota {
  position: absolute;
  top: -6px;
  bottom: -6px;
  border-left: 2px solid #2e3f55;
}

.meter-quota-label {
  position: absolute;
  bottom: 100%;
  transform: translateX(-50%);
  font-size: 0.75rem;
  color: #2e3f55;
  white-space: nowrap;
}

.swatch {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 2px;
}

.chart {
  position: relative;
  height: 280px;
}

.attainment-track {
  width: 96px;
  height: 8px;
  border-radius: 4px;
  background: #e6e8eb;
  overflow: hidden;
}

.attainment-bar {
  height: 100%;
  background: #1b8a94;
}
</style>
