<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { getJson, sendJson } from "../api";
import NewLeadDialog from "../components/NewLeadDialog.vue";
import PageHeader from "../components/PageHeader.vue";
import { LEAD_SOURCES, LEAD_STATUSES, leadStatus } from "../constants";
import { shortDate } from "../format";
import { useRepsStore } from "../stores/reps";

const SEARCH_DEBOUNCE_MS = 300;
const DEFAULT_STATUSES = ["new", "working", "qualified"];

const headers = [
  { title: "Name", key: "last_name", sortable: true },
  { title: "Company", key: "company", sortable: true },
  { title: "Source", key: "source", sortable: false },
  { title: "Status", key: "status", sortable: true },
  { title: "Score", key: "score", sortable: true },
  { title: "Owner", key: "owner", sortable: false },
  { title: "Created", key: "created_at", sortable: true },
  { title: "", key: "actions", sortable: false, align: "end" },
];

const reps = useRepsStore();

const searchInput = ref("");
const search = ref("");
const statuses = ref([...DEFAULT_STATUSES]);
const source = ref(null);
const ownerId = ref(null);

const page = ref(1);
const itemsPerPage = ref(25);
const sortBy = ref([{ key: "created_at", order: "desc" }]);

const leads = ref([]);
const total = ref(0);
const loading = ref(false);
const loadError = ref("");

const newLeadOpen = ref(false);
// The lead being converted; 2.4's conversion dialog opens from this.
const convertingLead = ref(null);
const snackbar = ref({ show: false, text: "", color: "success" });

let searchTimer = null;
watch(searchInput, (value) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    search.value = (value || "").trim();
  }, SEARCH_DEBOUNCE_MS);
});
onBeforeUnmount(() => clearTimeout(searchTimer));

// Any change to search or filters goes back to page 1.
watch([search, statuses, source, ownerId], () => {
  page.value = 1;
});

const sortParam = computed(() => {
  const [first] = sortBy.value;
  if (!first) return "-created_at";
  return first.order === "desc" ? `-${first.key}` : first.key;
});

const params = computed(() => ({
  q: search.value,
  status: statuses.value,
  source: source.value,
  owner_id: ownerId.value,
  sort: sortParam.value,
  limit: itemsPerPage.value,
  offset: (page.value - 1) * itemsPerPage.value,
}));

let requestId = 0;
async function load() {
  const id = ++requestId;
  loading.value = true;
  loadError.value = "";
  try {
    const body = await getJson("/api/v1/leads", params.value);
    if (id !== requestId) return;
    leads.value = body.items;
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

function notify(text, color = "success") {
  snackbar.value = { show: true, text, color };
}

function capitalise(value) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
}

function markOptions(lead) {
  return LEAD_STATUSES.filter(
    (status) => status.value !== lead.status && status.value !== "converted",
  );
}

async function markStatus(lead, status) {
  try {
    await sendJson("PATCH", `/api/v1/leads/${lead.id}`, { status: status.value });
    notify(`${lead.first_name} ${lead.last_name} marked ${status.value}`);
    await load();
  } catch (err) {
    notify(err.message, "error");
  }
}

function onCreated(lead) {
  notify(`Lead created for ${lead.company}`);
  load();
}
</script>

<template>
  <v-container fluid>
    <PageHeader title="Leads" subtitle="Inbound and outbound interest, scored and assigned to reps.">
      <v-btn color="primary" prepend-icon="mdi-plus" data-test="new-lead" @click="newLeadOpen = true">
        New lead
      </v-btn>
    </PageHeader>

    <v-card>
      <v-card-text>
        <v-row density="compact" align="center">
          <v-col cols="12" md="4">
            <v-text-field
              v-model="searchInput"
              label="Search name, company or email"
              prepend-inner-icon="mdi-magnify"
              density="compact"
              hide-details
              clearable
              data-test="search"
            />
          </v-col>
          <v-col cols="6" md="2">
            <v-select
              v-model="source"
              label="Source"
              :items="LEAD_SOURCES"
              density="compact"
              hide-details
              clearable
              data-test="source"
            />
          </v-col>
          <v-col cols="6" md="2">
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
        <v-chip-group v-model="statuses" multiple column class="mt-2" data-test="statuses">
          <v-chip
            v-for="status in LEAD_STATUSES"
            :key="status.value"
            :value="status.value"
            :color="status.color"
            :prepend-icon="status.icon"
            filter
            variant="outlined"
            :data-test="`status-${status.value}`"
          >
            {{ status.title }}
          </v-chip>
        </v-chip-group>
      </v-card-text>

      <v-alert v-if="loadError" type="error" variant="tonal" class="mx-4 mb-4">
        {{ loadError }}
      </v-alert>

      <v-data-table-server
        v-model:page="page"
        v-model:items-per-page="itemsPerPage"
        v-model:sort-by="sortBy"
        :headers="headers"
        :items="leads"
        :items-length="total"
        :items-per-page-options="[10, 25, 50, 100]"
        :loading="loading"
        item-value="id"
        must-sort
      >
        <template #[`item.last_name`]="{ item }">
          <div>{{ item.first_name }} {{ item.last_name }}</div>
          <div v-if="item.title" class="text-caption text-grey">{{ item.title }}</div>
        </template>
        <template #[`item.company`]="{ item }">
          <div>{{ item.company }}</div>
          <div class="text-caption text-grey">{{ item.email }}</div>
        </template>
        <template #[`item.source`]="{ item }">{{ capitalise(item.source) }}</template>
        <template #[`item.status`]="{ item }">
          <v-chip
            size="small"
            :color="leadStatus(item.status)?.color"
            :prepend-icon="leadStatus(item.status)?.icon"
          >
            {{ leadStatus(item.status)?.title ?? item.status }}
          </v-chip>
        </template>
        <template #[`item.score`]="{ item }">
          <div class="d-flex align-center ga-2">
            <v-progress-linear
              :model-value="item.score"
              color="secondary"
              height="6"
              rounded
              style="width: 60px"
            />
            <span>{{ item.score }}</span>
          </div>
        </template>
        <template #[`item.owner`]="{ item }">
          <span v-if="item.owner">{{ item.owner.name }}</span>
          <span v-else class="text-grey">Unassigned</span>
        </template>
        <template #[`item.created_at`]="{ item }">{{ shortDate(item.created_at) }}</template>
        <template #[`item.actions`]="{ item }">
          <v-btn
            v-if="item.status === 'converted'"
            :to="`/accounts/${item.converted_account_id}`"
            variant="text"
            size="small"
            color="secondary"
            data-test="account-link"
          >
            Account →
          </v-btn>
          <v-menu v-else>
            <template #activator="{ props: activator }">
              <v-btn
                v-bind="activator"
                icon="mdi-dots-vertical"
                variant="text"
                size="small"
                aria-label="Lead actions"
                data-test="actions"
              />
            </template>
            <v-list density="compact">
              <v-list-item
                v-if="item.status !== 'disqualified'"
                title="Convert to account"
                data-test="convert"
                @click="convertingLead = item"
              />
              <v-list-item
                v-for="status in markOptions(item)"
                :key="status.value"
                :title="`Mark ${status.value}`"
                :data-test="`mark-${status.value}`"
                @click="markStatus(item, status)"
              />
            </v-list>
          </v-menu>
        </template>
      </v-data-table-server>
    </v-card>

    <NewLeadDialog v-model="newLeadOpen" :owner-options="reps.options" @created="onCreated" />

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="4000">
      {{ snackbar.text }}
    </v-snackbar>
  </v-container>
</template>
