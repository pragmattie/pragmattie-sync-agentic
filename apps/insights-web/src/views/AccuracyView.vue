<script setup>
import {
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
} from "chart.js";
import { computed, onMounted, ref } from "vue";
import { Bar, Line } from "vue-chartjs";
import { getJson } from "../api";
import LoadError from "../components/LoadError.vue";
import LoadingBar from "../components/LoadingBar.vue";
import PageHeader from "../components/PageHeader.vue";
import {
  COLORS,
  FORECAST_NOTE,
  MILESTONE_NOTE,
  SELECTOR_NOTE,
  TRIAGE_NOTE,
  forecastChartData,
  headlineTiles,
  milestoneRows,
  realState,
  riskChartData,
  riskNote,
  selectorChartData,
  selectorThin,
  sprintChips,
  triageChartData,
  triageThin,
} from "../accuracy";

ChartJS.register(BarElement, CategoryScale, LinearScale, LineElement, PointElement, Tooltip);

const loading = ref(false);
const error = ref("");
const report = ref(null);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    report.value = await getJson("/api/v1/signals/accuracy");
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}

onMounted(load);

const tiles = computed(() => headlineTiles(report.value));
const minPoints = computed(() => report.value.min_points);
const triageState = computed(() => realState(report.value.triage, minPoints.value));
const selectorState = computed(() => realState(report.value.test_selector, minPoints.value));
const milestones = computed(() => milestoneRows(report.value.milestones));

const legend = [
  { label: "Met by P85 (so far)", color: COLORS.p85 },
  { label: "Met by P50 (so far)", color: COLORS.p50 },
];

const tooltip = { backgroundColor: "#1F2A38", titleColor: "#FFFFFF", bodyColor: "#FFFFFF" };

const forecastOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { display: false },
    tooltip: { ...tooltip, callbacks: { label: (item) => `${item.dataset.label} ${item.raw}%` } },
  },
  scales: {
    x: { grid: { display: false } },
    y: {
      min: 0,
      max: 100,
      grid: { color: "rgba(46, 63, 85, 0.08)" },
      ticks: { stepSize: 25, callback: (value) => `${value}%` },
    },
  },
};

const barOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: true, position: "bottom" }, tooltip },
  scales: {
    x: { stacked: true, grid: { display: false } },
    y: {
      stacked: true,
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
      title="Prediction accuracy"
      subtitle="How often the orchestrator's predictions matched what happened, and whether that is improving."
    />

    <LoadingBar :active="loading" />
    <LoadError title="Couldn't load prediction accuracy" :message="error" @retry="load" />

    <template v-if="report && !error">
      <div class="d-flex flex-wrap ga-4 mb-4">
        <v-card
          v-for="tile in tiles"
          :key="tile.key"
          variant="outlined"
          class="tile flex-grow-1"
          :data-test="`tile-${tile.key}`"
        >
          <v-card-text>
            <div class="d-flex align-center justify-space-between ga-2">
              <span class="text-body-2 text-medium-emphasis" data-test="tile-title">
                {{ tile.title }}
              </span>
              <v-chip
                size="x-small"
                variant="tonal"
                :color="tile.simulated ? 'secondary' : 'primary'"
                data-test="tile-chip"
              >
                {{ tile.simulated ? "Simulated" : "Real" }}
              </v-chip>
            </div>
            <div class="text-h4 font-weight-medium my-1" data-test="tile-value">
              {{ tile.value }}
            </div>
            <div class="text-body-2" data-test="tile-target">{{ tile.target }}</div>
            <div class="text-caption text-medium-emphasis" data-test="tile-detail">
              {{ tile.detail }}
            </div>
          </v-card-text>
        </v-card>
      </div>

      <v-row>
        <v-col cols="12" lg="6">
          <v-card variant="outlined" class="h-100" data-test="forecast-card">
            <v-card-title>Delivery forecasts</v-card-title>
            <v-card-subtitle class="wrap">
              Each finished sprint forecast again as of its working day
              {{ report.forecast.as_of_workday }}, using only what was known then
            </v-card-subtitle>
            <v-card-text>
              <div class="chart">
                <Line
                  :data="forecastChartData(report.forecast)"
                  :options="forecastOptions"
                  role="img"
                  aria-label="Line chart of the running share of sprints finished by their P85 and P50"
                />
              </div>
              <div class="d-flex flex-wrap ga-4 text-caption mt-2" data-test="forecast-legend">
                <span v-for="item in legend" :key="item.label" class="d-flex align-center ga-1">
                  <span class="swatch" :style="{ backgroundColor: item.color }" />
                  {{ item.label }}
                </span>
                <span class="text-medium-emphasis">dashed: target</span>
              </div>
              <div class="d-flex flex-wrap ga-1 mt-3">
                <v-chip
                  v-for="chip in sprintChips(report.forecast)"
                  :key="chip.key"
                  size="small"
                  variant="tonal"
                  :color="chip.color"
                  data-test="sprint-chip"
                >
                  {{ chip.text }}
                  <v-tooltip activator="parent" location="top" :text="chip.tooltip" />
                </v-chip>
              </div>
              <p class="note text-body-2 text-medium-emphasis mt-3">{{ FORECAST_NOTE }}</p>
            </v-card-text>
          </v-card>
        </v-col>

        <v-col cols="12" lg="6">
          <v-card variant="outlined" class="h-100" data-test="risk-card">
            <v-card-title>Risk score</v-card-title>
            <v-card-subtitle class="wrap">
              PRs that went on to cause an incident, by sprint: did the score put them at T2 or
              above?
            </v-card-subtitle>
            <v-card-text>
              <div class="chart">
                <Bar
                  :data="riskChartData(report.risk)"
                  :options="barOptions"
                  role="img"
                  aria-label="Bar chart of incident PRs flagged T2 or above, and missed, in each sprint"
                />
              </div>
              <p class="note text-body-2 text-medium-emphasis mt-3" data-test="risk-note">
                {{ riskNote(report.risk) }}
              </p>
            </v-card-text>
          </v-card>
        </v-col>

        <v-col cols="12" lg="6">
          <v-card variant="outlined" class="h-100" data-test="triage-card">
            <v-card-title>Triage agent</v-card-title>
            <v-card-subtitle class="wrap">
              Real issues it labelled, by week: kept as it set them, or changed by a person
            </v-card-subtitle>
            <v-card-text>
              <div v-if="triageState === 'enough'" class="chart">
                <Bar
                  :data="triageChartData(report.triage)"
                  :options="barOptions"
                  role="img"
                  aria-label="Bar chart of triage labels kept, and corrected by a person, each week"
                />
              </div>
              <p v-else class="text-body-1" data-test="triage-thin">
                {{ triageThin(report.triage, minPoints) }}
              </p>
              <p class="note text-body-2 text-medium-emphasis mt-3">{{ TRIAGE_NOTE }}</p>
            </v-card-text>
          </v-card>
        </v-col>

        <v-col cols="12" lg="6">
          <v-card variant="outlined" class="h-100" data-test="selector-card">
            <v-card-title>Test selector</v-card-title>
            <v-card-subtitle class="wrap">
              Real commits whose CI finished, by week: would skipping its picks have been safe?
            </v-card-subtitle>
            <v-card-text>
              <div v-if="selectorState === 'enough'" class="chart">
                <Bar
                  :data="selectorChartData(report.test_selector)"
                  :options="barOptions"
                  role="img"
                  aria-label="Bar chart of commits safe to skip, and missed, each week"
                />
              </div>
              <p v-else class="text-body-1" data-test="selector-thin">
                {{ selectorThin(report.test_selector, minPoints) }}
              </p>
              <p class="note text-body-2 text-medium-emphasis mt-3">{{ SELECTOR_NOTE }}</p>
            </v-card-text>
          </v-card>
        </v-col>

        <v-col cols="12">
          <v-card variant="outlined" data-test="milestone-card">
            <v-card-title>Milestone forecasts</v-card-title>
            <v-card-subtitle class="wrap">
              Each real milestone's first forecast against when its work actually finished
            </v-card-subtitle>
            <v-card-text>
              <v-table density="compact" data-test="milestone-table">
                <thead>
                  <tr>
                    <th>Milestone</th>
                    <th>First forecast</th>
                    <th>P50</th>
                    <th>P85</th>
                    <th>Finished</th>
                    <th>Against P50</th>
                    <th>Target met</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in milestones" :key="row.key" data-test="milestone-row">
                    <td>{{ row.label }}</td>
                    <template v-if="row.graded">
                      <td>{{ row.firstForecast }}</td>
                      <td>{{ row.p50 }}</td>
                      <td>{{ row.p85 }}</td>
                      <td>{{ row.finished }}</td>
                      <td>{{ row.error }}</td>
                      <td>{{ row.targetMet }}</td>
                    </template>
                    <td
                      v-else
                      colspan="6"
                      class="text-medium-emphasis"
                      data-test="milestone-reason"
                    >
                      {{ row.reason }}
                    </td>
                  </tr>
                </tbody>
              </v-table>
              <p class="note text-body-2 text-medium-emphasis mt-3">{{ MILESTONE_NOTE }}</p>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
    </template>
  </v-container>
</template>

<style scoped>
.tile {
  min-width: 200px;
  flex-basis: 0;
}

.chart {
  position: relative;
  height: 260px;
}

.note {
  max-width: 68ch;
}

.wrap {
  white-space: normal;
}

.swatch {
  display: inline-block;
  width: 12px;
  height: 3px;
}
</style>
