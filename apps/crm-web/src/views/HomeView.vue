<script setup>
import { computed, onMounted, ref } from "vue";
import { getJson } from "../api";
import PageHeader from "../components/PageHeader.vue";
import { money } from "../format";

const count = new Intl.NumberFormat("en-US");

const summary = ref(null);
const error = ref("");

const tiles = computed(() => {
  if (!summary.value) return [];
  const s = summary.value;
  return [
    {
      key: "open-leads",
      label: "Open leads",
      value: count.format(s.open_leads),
      icon: "mdi-account-search-outline",
      to: "/leads",
    },
    {
      key: "open-deals",
      label: "Open deals",
      value: count.format(s.open_deals),
      icon: "mdi-handshake-outline",
      to: "/pipeline",
    },
    {
      key: "open-pipeline",
      label: "Open pipeline",
      value: money(s.open_pipeline),
      icon: "mdi-chart-timeline-variant",
      to: "/pipeline",
    },
    {
      key: "won",
      label: `Won in ${s.quarter}`,
      value: money(s.won_this_quarter),
      icon: "mdi-trophy-outline",
      to: "/forecast",
    },
  ];
});

onMounted(async () => {
  try {
    summary.value = await getJson("/api/v1/summary");
  } catch (e) {
    error.value = e.message;
  }
});
</script>

<template>
  <v-container>
    <PageHeader
      title="Welcome to PragMattie Sync"
      subtitle="A CRM for mid-market B2B sales teams."
    />

    <v-alert v-if="error" type="warning" variant="tonal" data-test="summary-error">
      Couldn't load the sales summary: {{ error }}
    </v-alert>

    <v-row v-else>
      <v-col v-for="tile in tiles" :key="tile.key" cols="12" sm="6" md="3">
        <v-card :to="tile.to" :data-test="`tile-${tile.key}`" class="h-100">
          <v-card-text class="d-flex align-center ga-4">
            <v-icon :icon="tile.icon" size="40" color="secondary" />
            <div>
              <div class="text-subtitle-1 text-medium-emphasis">{{ tile.label }}</div>
              <div class="text-h4 font-weight-bold">{{ tile.value }}</div>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>
