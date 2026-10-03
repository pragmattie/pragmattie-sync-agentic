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
  doraTiles,
  hasHistory,
  isFlaky,
  moduleRows,
  sourceNote,
  sprintLabels,
} from "../signals";

ChartJS.register(BarElement, CategoryScale, LinearScale, LineElement, PointElement, Tooltip);

const LIGHT = "#6FBAC1";
const DARK = "#0E5A61";

const loading = ref(false);
const error = ref("");
const data = ref(null);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [summary, sprints, cycleTime, ci, modules, sources] = await Promise.all([
      getJson("/api/v1/signals/summary", { days: 30 }),
      getJson("/api/v1/signals/sprints"),
      getJson("/api/v1/signals/cycle-time", { bucket: "sprint" }),
      getJson("/api/v1/signals/ci", { weeks: 26 }),
      getJson("/api/v1/signals/modules"),
      getJson("/api/v1/signals/sources"),
    ]);
    data.value = { summary, sprints, cycleTime, ci, modules, sources };
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}

onMounted(load);

const empty = computed(() => data.value && !hasHistory(data.value));
const note = computed(() => sourceNote(data.value.sources));
const tiles = computed(() => doraTiles(data.value.summary));
const sprints = computed(() => data.value.sprints ?? []);
const cycleTime = computed(() => data.value.cycleTime ?? []);
const modules = computed(() => moduleRows(data.value.modules ?? []));
const suites = computed(() => data.value.ci?.by_suite ?? []);
const showCharts = computed(() => sprints.value.length > 0);

const DELTA_ICONS = { up: "mdi-arrow-up", down: "mdi-arrow-down", flat: "mdi-minus" };

function sprintTitle(row) {
  const name = `${row.sprint}${row.in_progress ? " (in progress)" : ""}`;
  return row.goal ? [name, row.goal] : [name];
}

function chartOptions({ unit, axisUnit = "", rows, tooltip }) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#1F2A38",
        titleColor: "#FFFFFF",
        bodyColor: "#FFFFFF",
        footerColor: "#FFFFFF",
        callbacks: {
          title: (items) => (items.length ? sprintTitle(rows[items[0].dataIndex]) : ""),
          label: (item) => {
            const value = item.raw === null ? "—" : `${item.raw}${unit}`;
            return `${item.dataset.label}: ${value}`;
          },
          ...tooltip,
        },
      },
    },
    scales: {
      x: { grid: { display: false } },
      y: {
        beginAtZero: true,
        grid: { color: "rgba(46, 63, 85, 0.08)" },
        ticks: { callback: (value) => `${value}${axisUnit}` },
      },
    },
  };
}

const velocityLegend = [
  { label: "Committed", color: LIGHT },
  { label: "Completed", color: DARK },
];
const cycleLegend = [
  { label: "Median", color: DARK },
  { label: "85th percentile", color: LIGHT },
];

const velocityData = computed(() => ({
  labels: sprintLabels(sprints.value),
  datasets: [
    {
      label: "Committed",
      backgroundColor: LIGHT,
      data: sprints.value.map((row) => row.committed),
    },
    {
      label: "Completed",
      backgroundColor: DARK,
      data: sprints.value.map((row) => row.completed),
    },
  ],
}));
const velocityOptions = computed(() => chartOptions({ unit: " pts", rows: sprints.value }));

const cycleData = computed(() => ({
  labels: sprintLabels(cycleTime.value),
  datasets: [
    {
      label: "Median",
      borderColor: DARK,
      backgroundColor: DARK,
      data: cycleTime.value.map((row) => row.median_hours),
      tension: 0.3,
    },
    {
      label: "85th percentile",
      borderColor: LIGHT,
      backgroundColor: LIGHT,
      data: cycleTime.value.map((row) => row.p85_hours),
      tension: 0.3,
    },
  ],
}));
const cycleOptions = computed(() =>
  chartOptions({
    unit: "h",
    axisUnit: "h",
    rows: cycleTime.value,
    tooltip: {
      footer: (items) => {
        if (!items.length) return "";
        const merged = cycleTime.value[items[0].dataIndex].merged;
        return `${merged} PR${merged === 1 ? "" : "s"} merged`;
      },
    },
  }),
);
const hasInProgress = computed(() => sprints.value.some((row) => row.in_progress));
</script>

<template>
  <v-container>
    <PageHeader
      title="Engineering signals"
      subtitle="How the PragMattie Sync team delivers: the history the prediction models learn from."
    />
    <LoadingBar :active="loading" />
    <LoadError title="Couldn't load the engineering signals" :message="error" @retry="load" />

    <p v-if="empty" class="text-body-1" data-test="empty-history">
      No engineering history yet. Run <code>python -m sdlc.synth</code> in the orchestrator to
      generate it.
    </p>

    <template v-else-if="data && !error">
      <v-alert
        type="info"
        variant="tonal"
        icon="mdi-flask-outline"
        class="mb-6"
        data-test="source-note"
      >
        {{ note }}
      </v-alert>

      <h2 class="text-h6 mb-3">Last 30 days · DORA delivery measures</h2>
      <v-row class="mb-4">
        <v-col v-for="tile in tiles" :key="tile.key" cols="12" sm="6" lg="3">
          <v-card variant="outlined" class="h-100" :data-test="`tile-${tile.key}`">
            <v-card-text>
              <div class="text-body-2 text-medium-emphasis">{{ tile.title }}</div>
              <div class="text-h5 font-weight-medium my-1" data-test="tile-value">
                {{ tile.value }}
              </div>
              <div v-if="tile.note" class="text-caption text-medium-emphasis">{{ tile.note }}</div>
              <div
                v-if="tile.delta"
                class="d-flex align-center ga-1 text-caption mt-1"
                :class="tile.delta.color ? `text-${tile.delta.color}` : 'text-medium-emphasis'"
                data-test="tile-delta"
              >
                <v-icon :icon="DELTA_ICONS[tile.delta.direction]" size="small" />
                {{ tile.delta.text }}
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>

      <v-row v-if="showCharts" class="mb-4">
        <v-col cols="12" lg="6">
          <v-card variant="outlined" class="h-100" data-test="velocity">
            <v-card-title>Sprint velocity</v-card-title>
            <v-card-subtitle>Story points committed vs completed by sprint end</v-card-subtitle>
            <v-card-text>
              <div class="d-flex flex-wrap align-center ga-4 mb-2 text-body-2">
                <span v-for="item in velocityLegend" :key="item.label" class="legend-item">
                  <span class="swatch" :style="{ background: item.color }" />{{ item.label }}
                </span>
                <span v-if="hasInProgress" class="text-medium-emphasis">* in progress</span>
              </div>
              <div class="chart">
                <Bar
                  :data="velocityData"
                  :options="velocityOptions"
                  role="img"
                  aria-label="Bar chart of story points committed and completed in each sprint"
                />
              </div>
            </v-card-text>
          </v-card>
        </v-col>
        <v-col cols="12" lg="6">
          <v-card variant="outlined" class="h-100" data-test="cycle-time">
            <v-card-title>Pull request cycle time</v-card-title>
            <v-card-subtitle>Hours from opened to merged, for PRs merged in each sprint</v-card-subtitle>
            <v-card-text>
              <div class="d-flex flex-wrap align-center ga-4 mb-2 text-body-2">
                <span v-for="item in cycleLegend" :key="item.label" class="legend-item">
                  <span class="swatch" :style="{ background: item.color }" />{{ item.label }}
                </span>
                <span v-if="hasInProgress" class="text-medium-emphasis">* in progress</span>
              </div>
              <div class="chart">
                <Line
                  :data="cycleData"
                  :options="cycleOptions"
                  role="img"
                  aria-label="Line chart of median and 85th percentile hours from PR opened to merged in each sprint"
                />
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>

      <v-card v-if="modules.length" variant="outlined" class="mb-6" data-test="modules">
        <v-card-title>Risk and effort by module</v-card-title>
        <v-card-subtitle>
          Incident rate = share of merged PRs that caused a production incident
        </v-card-subtitle>
        <v-table density="comfortable">
          <thead>
            <tr>
              <th>Module</th>
              <th class="text-right">Merged PRs</th>
              <th class="text-right">Incidents</th>
              <th>Incident rate</th>
              <th class="text-right">Days per story point</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in modules" :key="row.module" :data-test="`module-${row.module}`">
              <td>{{ row.name }}</td>
              <td class="text-right">{{ row.merged_prs }}</td>
              <td class="text-right">{{ row.incidents }}</td>
              <td>
                <div class="d-flex align-center ga-2">
                  <div class="rate-track">
                    <div class="rate-bar" :style="{ width: `${row.barWidth}%` }" />
                  </div>
                  <span>{{ row.incident_rate ?? "—" }}%</span>
                </div>
              </td>
              <td class="text-right" :class="{ 'font-weight-bold': row.overEstimate }">
                <v-icon
                  v-if="row.overEstimate"
                  icon="mdi-alert-outline"
                  color="warning"
                  size="small"
                  title="Runs well over estimate compared with other modules"
                  aria-label="Runs well over estimate compared with other modules"
                  data-test="over-estimate"
                />
                {{ row.days_per_point ?? "—" }}
              </td>
            </tr>
          </tbody>
        </v-table>
      </v-card>

      <v-card v-if="suites.length" variant="outlined" class="mb-6" data-test="ci">
        <v-card-title>CI health</v-card-title>
        <v-card-subtitle>Last 26 weeks · flaky = failed, then passed on re-run</v-card-subtitle>
        <v-table density="comfortable">
          <thead>
            <tr>
              <th>Test suite</th>
              <th class="text-right">Runs</th>
              <th class="text-right">Pass rate (%)</th>
              <th class="text-right">Flaky failures (%)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in suites" :key="row.suite" :data-test="`suite-${row.suite}`">
              <td>{{ row.suite }}</td>
              <td class="text-right">{{ row.runs }}</td>
              <td class="text-right">{{ row.pass_rate ?? "—" }}</td>
              <td class="text-right" :class="{ 'font-weight-bold': isFlaky(row.flaky_rate) }">
                <v-icon
                  v-if="isFlaky(row.flaky_rate)"
                  icon="mdi-alert-outline"
                  color="warning"
                  size="small"
                  title="Flaky suite"
                  aria-label="Flaky suite"
                  data-test="flaky"
                />
                {{ row.flaky_rate ?? "—" }}
              </td>
            </tr>
          </tbody>
        </v-table>
      </v-card>
    </template>
  </v-container>
</template>

<style scoped>
.chart {
  position: relative;
  height: 280px;
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

.rate-track {
  width: 80px;
  height: 8px;
  border-radius: 4px;
  background: rgba(46, 63, 85, 0.08);
}

.rate-bar {
  height: 100%;
  border-radius: 4px;
  background: #1b8a94;
}
</style>
