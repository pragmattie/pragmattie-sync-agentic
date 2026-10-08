<script setup>
import { BarElement, CategoryScale, Chart as ChartJS, LinearScale, Tooltip } from "chart.js";
import { computed, onMounted, ref } from "vue";
import { Bar } from "vue-chartjs";
import { getJson } from "../api";
import {
  barRows,
  failingBarNote,
  realCountNote,
  thresholdRows,
  tierRows,
} from "../signals";
import LoadError from "./LoadError.vue";
import LoadingBar from "./LoadingBar.vue";

ChartJS.register(BarElement, CategoryScale, LinearScale, Tooltip);

const LIGHT = "#6FBAC1";
const DARK = "#0E5A61";

const loading = ref(false);
const error = ref("");
const report = ref(null);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    report.value = await getJson("/api/v1/signals/calibration");
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}

onMounted(load);

const note = computed(() => realCountNote(report.value));
const thresholds = computed(() => thresholdRows(report.value));
const tiers = computed(() => tierRows(report.value));
const bars = computed(() => barRows(report.value));
const fallback = computed(() => failingBarNote(report.value));

const legend = [
  { label: "Precision", color: DARK },
  { label: "Recall", color: LIGHT },
];

function asPercent(ratio) {
  return ratio === null || ratio === undefined ? null : Math.round(ratio * 1000) / 10;
}

const chartData = computed(() => {
  const rows = ["T1", "T2", "T3"].map((tier) => report.value.thresholds?.[tier] ?? {});
  return {
    labels: ["T1+", "T2+", "T3+"],
    datasets: [
      {
        label: "Precision",
        backgroundColor: DARK,
        data: rows.map((row) => asPercent(row.precision)),
      },
      { label: "Recall", backgroundColor: LIGHT, data: rows.map((row) => asPercent(row.recall)) },
    ],
  };
});

const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { display: false },
    tooltip: {
      backgroundColor: "#1F2A38",
      titleColor: "#FFFFFF",
      bodyColor: "#FFFFFF",
      callbacks: {
        title: (items) => (items.length ? `Flag at ${items[0].label}` : ""),
        label: (item) => {
          const value = item.raw === null ? "—" : `${item.raw.toFixed(1)}%`;
          return `${item.dataset.label}: ${value}`;
        },
      },
    },
  },
  scales: {
    x: { grid: { display: false } },
    y: {
      beginAtZero: true,
      max: 100,
      grid: { color: "rgba(46, 63, 85, 0.08)" },
      ticks: { callback: (value) => `${value}%` },
    },
  },
};
</script>

<template>
  <section class="mb-6" data-test="risk-model">
    <div class="d-flex flex-wrap align-center ga-3 mb-2">
      <h2 class="text-h6">Risk model</h2>
      <v-chip
        size="small"
        color="secondary"
        variant="tonal"
        prepend-icon="mdi-flask-outline"
        data-test="calibration-chip"
      >
        Calibration data · simulated
      </v-chip>
    </div>
    <LoadingBar :active="loading" />
    <LoadError title="Couldn't load the risk model grading" :message="error" @retry="load" />

    <template v-if="report && !error">
      <p class="text-body-1 mb-4" data-test="real-counts">{{ note }}</p>

      <v-row class="mb-2">
        <v-col cols="12" lg="7">
          <v-card variant="outlined" class="mb-4" data-test="thresholds">
            <v-card-title>Precision and recall by threshold</v-card-title>
            <v-card-subtitle>
              Precision = flagged PRs that caused an incident · recall = incident PRs flagged
            </v-card-subtitle>
            <v-table density="comfortable">
              <thead>
                <tr>
                  <th>Flag at</th>
                  <th class="text-right">PRs flagged</th>
                  <th class="text-right">Incident PRs caught</th>
                  <th class="text-right">Precision</th>
                  <th class="text-right">Recall</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in thresholds" :key="row.tier" :data-test="`threshold-${row.tier}`">
                  <td>{{ row.tier }}</td>
                  <td class="text-right">{{ row.flagged }}</td>
                  <td class="text-right">{{ row.incidents }}</td>
                  <td class="text-right">{{ row.precision }}</td>
                  <td class="text-right">{{ row.recall }}</td>
                </tr>
              </tbody>
            </v-table>
          </v-card>

          <v-card variant="outlined" data-test="tiers">
            <v-card-title>PRs by tier</v-card-title>
            <v-table density="comfortable">
              <thead>
                <tr>
                  <th>Tier</th>
                  <th class="text-right">PRs</th>
                  <th class="text-right">Incident PRs</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in tiers" :key="row.tier" :data-test="`tier-${row.tier}`">
                  <td>{{ row.tier }}</td>
                  <td class="text-right">{{ row.prs }}</td>
                  <td class="text-right">{{ row.incidents }}</td>
                </tr>
              </tbody>
            </v-table>
          </v-card>
        </v-col>

        <v-col cols="12" lg="5">
          <v-card variant="outlined" class="mb-4" data-test="bars">
            <v-card-title>The bars, on this history</v-card-title>
            <v-list density="compact">
              <v-list-item v-for="bar in bars" :key="bar.key" :data-test="`bar-${bar.key}`">
                <template #prepend>
                  <v-chip
                    size="small"
                    :color="bar.passed ? 'success' : 'error'"
                    variant="flat"
                    class="mr-3"
                    data-test="verdict"
                  >
                    {{ bar.verdict }}
                  </v-chip>
                </template>
                <v-list-item-title class="text-wrap">{{ bar.label }}</v-list-item-title>
              </v-list-item>
            </v-list>
            <v-card-text v-if="fallback" class="pt-0" data-test="pooled-note">
              {{ fallback }}
            </v-card-text>
          </v-card>

          <v-card variant="outlined" data-test="calibration-chart">
            <v-card-title>Precision and recall</v-card-title>
            <v-card-text>
              <div class="d-flex flex-wrap align-center ga-4 mb-2 text-body-2">
                <span v-for="item in legend" :key="item.label" class="legend-item">
                  <span class="swatch" :style="{ background: item.color }" />{{ item.label }}
                </span>
              </div>
              <div class="chart">
                <Bar
                  :data="chartData"
                  :options="chartOptions"
                  role="img"
                  aria-label="Bar chart of precision and recall when flagging at T1, T2 and T3 or above"
                />
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
    </template>
  </section>
</template>

<style scoped>
.chart {
  position: relative;
  height: 220px;
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
