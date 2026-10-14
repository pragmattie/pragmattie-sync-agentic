<script setup>
import {
  CategoryScale,
  Chart as ChartJS,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
} from "chart.js";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { Line } from "vue-chartjs";
import { getJson } from "../api";
import { formatTime } from "../decisions";
import LoadError from "../components/LoadError.vue";
import LoadingBar from "../components/LoadingBar.vue";
import PageHeader from "../components/PageHeader.vue";
import {
  FLASH_MS,
  NO_FORECASTS,
  NO_REAL_MILESTONES,
  OUT_OF_DATE,
  POLL_MS,
  PROPOSAL_NOTE,
  SIMULATED_HEADING,
  SIMULATED_NOTE,
  changedIds,
  chanceColor,
  dayLabel,
  effectText,
  formatDay,
  hasForecasts,
  itemsText,
  movedText,
  paceText,
  percent,
  splitFeed,
  targetColor,
  targetText,
  trailChartData,
} from "../forecast";

ChartJS.register(CategoryScale, LinearScale, LineElement, PointElement, Tooltip);

const loading = ref(false);
const error = ref("");
const feed = ref(null);
const flashing = ref(new Set());
let pollTimer = null;
// One pending flash per card: its end timer, and the frame that restarts it.
const flashTimers = new Map();

function setFlashing(subject, on) {
  const next = new Set(flashing.value);
  if (on) next.add(subject);
  else next.delete(subject);
  flashing.value = next;
}

function stopFlash(subject) {
  const pending = flashTimers.get(subject);
  if (!pending) return;
  cancelAnimationFrame(pending.frame);
  clearTimeout(pending.timer);
  flashTimers.delete(subject);
}

function startFlash(subject, pending) {
  setFlashing(subject, true);
  pending.timer = setTimeout(() => {
    setFlashing(subject, false);
    flashTimers.delete(subject);
  }, FLASH_MS);
}

// A card that changes again while it flashes restarts: the class goes, and comes back on the
// next frame so the animation replays, with a fresh timer.
function flash(subjects) {
  for (const subject of subjects) {
    const restart = flashTimers.has(subject);
    stopFlash(subject);
    const pending = {};
    flashTimers.set(subject, pending);
    if (restart) {
      setFlashing(subject, false);
      pending.frame = requestAnimationFrame(() => startFlash(subject, pending));
    } else {
      startFlash(subject, pending);
    }
  }
}

async function load() {
  loading.value = !feed.value;
  try {
    const next = await getJson("/api/v1/signals/forecast");
    flash(changedIds(feed.value, next));
    feed.value = next;
    error.value = "";
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  load();
  pollTimer = setInterval(load, POLL_MS);
});

onUnmounted(() => {
  clearInterval(pollTimer);
  for (const subject of [...flashTimers.keys()]) stopFlash(subject);
});

const epics = computed(() => splitFeed(feed.value));
const sprint = computed(() => feed.value?.sprint ?? null);
const proposals = computed(() => feed.value?.proposals ?? {});
const empty = computed(() => feed.value && !hasForecasts(feed.value));

const trailOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { display: false },
    tooltip: {
      backgroundColor: "#1F2A38",
      callbacks: { label: (item) => `${item.dataset.label} ${dayLabel(item.raw)}` },
    },
  },
  scales: {
    x: { grid: { display: false }, ticks: { maxTicksLimit: 4 } },
    y: {
      grid: { color: "rgba(46, 63, 85, 0.08)" },
      ticks: { precision: 0, maxTicksLimit: 4, callback: (value) => dayLabel(value) },
    },
  },
};
</script>

<template>
  <v-container>
    <PageHeader
      title="Delivery forecast"
      subtitle="When each remaining milestone of the real build is likely to finish, and why the dates moved."
    />

    <LoadingBar :active="loading" />
    <LoadError title="Couldn't load the delivery forecast" :message="error" @retry="load" />

    <v-card v-if="empty" variant="outlined" data-test="empty-forecasts">
      <v-card-text class="text-body-1">{{ NO_FORECASTS }}</v-card-text>
    </v-card>

    <template v-else-if="feed">
      <section data-test="real-section">
        <h2 class="text-h5 mb-3">Real build</h2>
        <v-card
          v-if="!epics.real.length"
          variant="outlined"
          class="mb-4"
          data-test="empty-real"
        >
          <v-card-text class="text-body-1">{{ NO_REAL_MILESTONES }}</v-card-text>
        </v-card>

        <div v-for="epic in epics.real" :key="epic.subject" class="mb-4">
          <v-card
            variant="outlined"
            :class="{ flash: flashing.has(epic.subject) }"
            data-test="real-card"
          >
            <v-card-title data-test="card-label">{{ epic.label }}</v-card-title>
            <v-card-subtitle>{{ epic.remaining_items }} items left</v-card-subtitle>
            <v-card-subtitle v-if="epic.ahead_items > 0" data-test="ahead">
              After {{ epic.ahead_items }} items in earlier milestones
            </v-card-subtitle>
            <v-card-subtitle v-if="epic.gate_days > 0" data-test="gate-days">
              Includes {{ epic.gate_days }} working day(s) for gates and specs (measured from past
              milestones)
            </v-card-subtitle>
            <v-card-text>
              <v-row>
                <v-col cols="12" md="6">
                  <div class="d-flex ga-6 mb-2">
                    <div>
                      <div class="text-body-2 text-medium-emphasis">P50</div>
                      <div class="text-h6" data-test="p50">{{ formatDay(epic.p50) }}</div>
                    </div>
                    <div>
                      <div class="text-body-2 text-medium-emphasis">P85</div>
                      <div class="text-h6" data-test="p85">{{ formatDay(epic.p85) }}</div>
                    </div>
                  </div>
                  <v-chip
                    v-if="targetColor(epic)"
                    :color="targetColor(epic)"
                    variant="tonal"
                    size="small"
                    class="mb-2"
                    data-test="target"
                  >
                    {{ targetText(epic) }}
                  </v-chip>
                  <div v-else class="text-body-2 mb-2" data-test="target">
                    {{ targetText(epic) }}
                  </div>
                  <div class="text-body-2" data-test="moved">
                    Moved: {{ movedText(epic.moved) }}
                  </div>
                  <div class="text-body-2 text-medium-emphasis mt-1" data-test="pace">
                    {{ paceText(epic) }}
                  </div>
                </v-col>
                <v-col cols="12" md="6">
                  <div class="trail">
                    <Line
                      :data="trailChartData(epic.trail)"
                      :options="trailOptions"
                      role="img"
                      :aria-label="`Line chart of past P50 and P85 forecasts for ${epic.label}`"
                    />
                  </div>
                </v-col>
              </v-row>
            </v-card-text>
          </v-card>

          <v-card
            v-if="proposals[epic.subject]"
            variant="tonal"
            color="primary"
            class="mt-2"
            data-test="proposal"
          >
            <v-card-title class="text-subtitle-1">Planner proposal</v-card-title>
            <v-card-subtitle>
              Drafted {{ formatTime(proposals[epic.subject].created_at) }}
            </v-card-subtitle>
            <v-card-text>
              <p class="text-body-1 mb-2" data-test="proposal-summary">
                {{ proposals[epic.subject].summary }}
              </p>
              <v-alert
                v-if="proposals[epic.subject].current === false"
                type="warning"
                variant="tonal"
                density="compact"
                class="mb-2"
                data-test="proposal-out-of-date"
              >
                {{ OUT_OF_DATE }}
              </v-alert>
              <v-list density="compact" bg-color="transparent">
                <v-list-item
                  v-for="(option, index) in proposals[epic.subject].options"
                  :key="index"
                  data-test="proposal-option"
                >
                  <v-list-item-title>
                    {{ option.title }}
                    <v-chip
                      v-if="index === proposals[epic.subject].recommended"
                      size="x-small"
                      color="secondary"
                      class="ml-2"
                      data-test="recommended"
                    >
                      Recommended
                    </v-chip>
                  </v-list-item-title>
                  <v-list-item-subtitle>
                    {{ option.action }}: {{ itemsText(option.items) }}
                  </v-list-item-subtitle>
                  <div class="text-body-2" data-test="proposal-effect">
                    {{ effectText(option) }}
                  </div>
                </v-list-item>
              </v-list>
              <p class="text-body-2 font-weight-medium mt-2" data-test="proposal-note">
                {{ PROPOSAL_NOTE }}
              </p>
            </v-card-text>
          </v-card>
        </div>
      </section>

      <section class="mt-8" data-test="simulated-section">
        <h2 class="text-h5" data-test="simulated-heading">{{ SIMULATED_HEADING }}</h2>
        <p class="text-body-1 mb-3" data-test="simulated-note">{{ SIMULATED_NOTE }}</p>

        <v-card v-if="sprint" variant="outlined" class="mb-4" data-test="sprint">
          <v-card-title>{{ sprint.label }}</v-card-title>
          <v-card-subtitle>
            As of {{ formatDay(sprint.as_of) }}, ends {{ formatDay(sprint.end_date) }}
          </v-card-subtitle>
          <v-card-text>
            <div class="d-flex flex-wrap ga-6 mb-2 text-body-1">
              <span v-if="typeof sprint.on_time_probability === 'number'" data-test="sprint-chance">
                On-time chance:
                <v-chip
                  :color="chanceColor(percent(sprint.on_time_probability))"
                  size="small"
                  variant="tonal"
                >
                  {{ percent(sprint.on_time_probability) }}%
                </v-chip>
              </span>
              <span>P50 {{ formatDay(sprint.p50) }}</span>
              <span>P85 {{ formatDay(sprint.p85) }}</span>
            </div>
            <template v-if="sprint.at_risk?.length">
              <div class="text-subtitle-2 mt-2">At-risk items</div>
              <ul class="ml-5 text-body-2">
                <li
                  v-for="item in sprint.at_risk"
                  :key="item.number ?? item.title"
                  data-test="at-risk"
                >
                  {{ item.number ? `#${item.number} ${item.title}` : item.title }}:
                  {{ item.reason }}
                </li>
              </ul>
            </template>
          </v-card-text>
        </v-card>

        <v-table v-if="epics.simulated.length" density="compact" data-test="simulated-epics">
          <thead>
            <tr>
              <th>Epic</th>
              <th>Items left</th>
              <th>P50</th>
              <th>P85</th>
              <th>Target</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="epic in epics.simulated" :key="epic.subject">
              <td>{{ epic.label }}</td>
              <td>{{ epic.remaining_items }}</td>
              <td>{{ formatDay(epic.p50) }}</td>
              <td>{{ formatDay(epic.p85) }}</td>
              <td>{{ targetText(epic) }}</td>
            </tr>
          </tbody>
        </v-table>
      </section>
    </template>
  </v-container>
</template>

<style scoped>
.trail {
  position: relative;
  height: 140px;
}

.flash {
  animation: flash 2s ease-out;
}

@keyframes flash {
  from {
    background-color: rgba(232, 179, 90, 0.45);
  }
  to {
    background-color: transparent;
  }
}
</style>
