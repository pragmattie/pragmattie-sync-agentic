<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { getJson } from "../api";
import PageHeader from "../components/PageHeader.vue";
import { INDUSTRIES } from "../constants";
import { money, moneyFull } from "../format";
import { useRepsStore } from "../stores/reps";

const SEARCH_DEBOUNCE_MS = 300;
const PAGE_SIZE = 25;

const headers = [
  { title: "Account", key: "name", sortable: false },
  { title: "Industry", key: "industry", sortable: false },
  { title: "Region", key: "region", sortable: false },
  { title: "Employees", key: "employee_count", sortable: false, align: "end" },
  { title: "Contacts", key: "contact_count", sortable: false, align: "end" },
  { title: "Open pipeline", key: "open_pipeline", sortable: false, align: "end" },
  { title: "Owner", key: "owner", sortable: false },
];

const employees = new Intl.NumberFormat("en-US");

const router = useRouter();
const reps = useRepsStore();

const searchInput = ref("");
const search = ref("");
const industry = ref(null);
const ownerId = ref(null);
const page = ref(1);

const accounts = ref([]);
const total = ref(0);
const loading = ref(false);
const loadError = ref("");

let searchTimer = null;
watch(searchInput, (value) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    search.value = (value || "").trim();
  }, SEARCH_DEBOUNCE_MS);
});
onBeforeUnmount(() => clearTimeout(searchTimer));

// Any change to search or filters goes back to page 1.
watch([search, industry, ownerId], () => {
  page.value = 1;
});

const params = computed(() => ({
  q: search.value,
  industry: industry.value,
  owner_id: ownerId.value,
  limit: PAGE_SIZE,
  offset: (page.value - 1) * PAGE_SIZE,
}));

let requestId = 0;
async function load() {
  const id = ++requestId;
  loading.value = true;
  loadError.value = "";
  try {
    const body = await getJson("/api/v1/accounts", params.value);
    if (id !== requestId) return;
    accounts.value = body.items;
    total.value = body.total;
  } catch (err) {
    if (id !== requestId) return;
    loadError.value = err.message;
  } finally {
    if (id === requestId) loading.value = false;
  }
}

watch(params, load, { deep: true });

onMounted(() => {
  load();
  reps.load().catch(() => {});
});

function openAccount(_event, { item }) {
  router.push(`/accounts/${item.id}`);
}

function hasPipeline(value) {
  return Number(value) > 0;
}
</script>

<template>
  <v-container fluid>
    <PageHeader
      title="Accounts"
      subtitle="Customer and prospect companies, with their open pipeline."
    />

    <v-card>
      <v-card-text>
        <v-row density="compact" align="center">
          <v-col cols="12" md="4">
            <v-text-field
              v-model="searchInput"
              label="Search accounts"
              prepend-inner-icon="mdi-magnify"
              density="compact"
              hide-details
              clearable
              data-test="search"
            />
          </v-col>
          <v-col cols="6" md="3">
            <v-select
              v-model="industry"
              label="Industry"
              :items="INDUSTRIES"
              density="compact"
              hide-details
              clearable
              data-test="industry"
            />
          </v-col>
          <v-col cols="6" md="3">
            <v-select
              v-model="ownerId"
              label="Owner"
              :items="reps.options"
              density="compact"
              hide-details
              clearable
              data-test="owner"
            />
          </v-col>
        </v-row>
      </v-card-text>

      <v-alert v-if="loadError" type="error" variant="tonal" class="mx-4 mb-4">
        {{ loadError }}
      </v-alert>

      <v-data-table-server
        v-model:page="page"
        :items-per-page="PAGE_SIZE"
        :items-per-page-options="[PAGE_SIZE]"
        :headers="headers"
        :items="accounts"
        :items-length="total"
        :loading="loading"
        item-value="id"
        hover
        style="cursor: pointer"
        @click:row="openAccount"
      >
        <template #[`item.name`]="{ item }">
          <span class="font-weight-bold" data-test="account-name">{{ item.name }}</span>
        </template>
        <template #[`item.employee_count`]="{ item }">
          {{ employees.format(item.employee_count) }}
        </template>
        <template #[`item.open_pipeline`]="{ item }">
          <span
            v-if="hasPipeline(item.open_pipeline)"
            :title="moneyFull(item.open_pipeline)"
            data-test="pipeline"
          >
            {{ money(item.open_pipeline) }}
          </span>
          <span v-else class="text-grey" data-test="pipeline">—</span>
        </template>
        <template #[`item.owner`]="{ item }">
          <span v-if="item.owner">{{ item.owner.name }}</span>
          <span v-else class="text-grey">Unassigned</span>
        </template>
      </v-data-table-server>
    </v-card>
  </v-container>
</template>
