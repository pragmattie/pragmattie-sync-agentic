<script setup>
import {
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Filler,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
} from "chart.js";
import { computed, onMounted, ref, watch } from "vue";
import { Bar, Line } from "vue-chartjs";
import { getJson } from "../api";
import LoadError from "../components/LoadError.vue";
import LoadingBar from "../components/LoadingBar.vue";
import PageHeader from "../components/PageHeader.vue";
import {
  DAY_OPTIONS,
  DEFAULT_DAYS,
  NO_REAL_WORK,
  REAL_NOTE,
  SIMULATED_CHIP,
  SIMULATED_NOTE,
  SOURCES,
  STAGE_COLORS,
  flowChartData,
  flowTiles,
  noRealWork,
  throughputChartData,
} from "../flow";

ChartJS.register(
  BarElement,
  CategoryScale,
  Filler,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
);

const source = ref("github");
const days = ref(DEFAULT_DAYS);
const loading = ref(false);
const error = ref("");
const result = ref(null);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    result.value = await getJson("/api/v1/signals/flow", {
      source: source.value,
      days: days.value,
    });
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch([source, days], load);

const simulated = computed(() => source.value === "synthetic");
const emptyReal = computed(() => noRealWork(result.value));
const tiles = computed(() => flowTiles(result.value));
const flowData = computed(() => flowChartData(result.value));
const throughputData = computed(() => throughputChartData(result.value));
const legend = computed(() =>
  (result.value?.stages ?? []).map((stage) => ({ label: stage, color: STAGE_COLORS[stage] })),
);

const tooltip = {
  backgroundColor: "#1F2A38",
  titleColor: "#FFFFFF",
  bodyColor: "#FFFFFF",
};

const flowOptions = {
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: "index", intersect: false },
  plugins: {
    legend: { display: false },
    tooltip: { ...tooltip, itemSort: (a, b) => b.datasetIndex - a.datasetIndex },
  },
  scales: {
    x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
    y: {
      stacked: true,
      beginAtZero: true,
      grid: { color: "rgba(46, 63, 85, 0.08)" },
      ticks: { precision: 0 },
    },
  },
};

const throughputOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { display: false },
    tooltip: {
      ...tooltip,
      callbacks: {
        title: (items) => (items.length ? `Week of ${items[0].label}` : ""),
        label: (item) => `${item.raw} merged`,
      },
    },
  },
  scales: {
    x: { grid: { display: false } },
    y: {
      beginAtZero: true,
      grid: { color: "rgba(46, 63, 85, 0.08)" },
      ticks: { precision: 0 },
    },
  },
};
</script>

<template>
  <v-container>
    <PageHeader
      title="Delivery flow"
      subtitle="How work moved through the stages, from backlog to production, day by day."
    >
      <v-btn-toggle
        v-model="source"
        mandatory
        density="comfortable"
        variant="outlined"
        color="primary"
        divided
        data-test="source-toggle"
      >
        <v-btn
          v-for="option in SOURCES"
          :key="option.value"
          :value="option.value"
          :data-test="`source-${option.value}`"
        >
          {{ option.title }}
        </v-btn>
      </v-btn-toggle>
      <v-btn-toggle
        v-model="days"
        mandatory
        density="comfortable"
        variant="outlined"
        color="primary"
        divided
        aria-label="Days shown"
        data-test="days-toggle"
      >
        <v-btn
          v-for="option in DAY_OPTIONS"
          :key="option"
          :value="option"
          :data-test="`days-${option}`"
        >
          {{ option }} days
        </v-btn>
      </v-btn-toggle>
    </PageHeader>

    <div class="mb-4" data-test="source-label">
      <template v-if="simulated">
        <v-chip
          size="small"
          color="secondary"
          variant="tonal"
          prepend-icon="mdi-flask-outline"
          class="mb-2"
          data-test="simulated-chip"
        >
          {{ SIMULATED_CHIP }}
        </v-chip>
        <p class="text-body-1" data-test="simulated-note">{{ SIMULATED_NOTE }}</p>
      </template>
      <p v-else class="text-body-1" data-test="real-note">{{ REAL_NOTE }}</p>
    </div>

    <LoadingBar :active="loading" />
    <LoadError title="Couldn't load the delivery flow" :message="error" @retry="load" />

    <v-card v-if="emptyReal && !error" variant="outlined" data-test="empty-real">
      <v-card-text class="text-body-1">{{ NO_REAL_WORK }}</v-card-text>
      <v-card-actions>
        <v-btn
          color="primary"
          variant="tonal"
          data-test="view-simulated"
          @click="source = 'synthetic'"
        >
          View the simulated history
        </v-btn>
      </v-card-actions>
    </v-card>

    <v-row v-else-if="result && !error">
      <v-col cols="12" lg="8">
        <v-card variant="outlined" class="h-100" data-test="flow-chart">
          <v-card-title>Items in each stage</v-card-title>
          <v-card-subtitle>At the end of each day, last {{ result.days }} days</v-card-subtitle>
          <v-card-text>
            <div class="d-flex flex-wrap align-center ga-4 mb-2 text-body-2">
              <span v-for="item in legend" :key="item.label" class="legend-item">
                <span class="swatch" :style="{ background: item.color }" />{{ item.label }}
              </span>
            </div>
            <div class="chart">
              <Line
                :data="flowData"
                :options="flowOptions"
                role="img"
                aria-label="Stacked area chart of how many items stood in each delivery stage on each day"
              />
            </div>
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" lg="4">
        <v-row dense class="mb-2">
          <v-col v-for="tile in tiles" :key="tile.key" cols="12" sm="4" lg="12">
            <v-card variant="outlined" :data-test="`tile-${tile.key}`">
              <v-card-text>
                <div class="text-body-2 text-medium-emphasis">{{ tile.title }}</div>
                <div class="text-h5 font-weight-medium" data-test="tile-value">
                  {{ tile.value }}
                </div>
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>
        <div class="text-caption text-medium-emphasis mb-3">
          Cycle time runs from In progress to Merged, for items merged in the window.
        </div>
        <v-card variant="outlined" data-test="throughput">
          <v-card-title class="text-subtitle-1">Weekly throughput</v-card-title>
          <v-card-subtitle>Items reaching Merged, weeks from Monday</v-card-subtitle>
          <v-card-text>
            <div class="chart-small">
              <Bar
                :data="throughputData"
                :options="throughputOptions"
                role="img"
                aria-label="Bar chart of items merged in each week"
              />
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<style scoped>
.chart {
  position: relative;
  height: 380px;
}

.chart-small {
  position: relative;
  height: 160px;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.swatch {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 2px;
}
</style>
