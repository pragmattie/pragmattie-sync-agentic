<script setup>
import { computed, ref, watch } from "vue";
import { getJson } from "../api";
import PageHeader from "../components/PageHeader.vue";
import { OPEN_STAGES, stageTitle } from "../constants";
import { money, moneyFull, shortDate } from "../format";

const props = defineProps({
  id: {
    type: [String, Number],
    required: true,
  },
});

const opportunityHeaders = [
  { title: "Name", key: "name", sortable: false },
  { title: "Stage", key: "stage", sortable: false },
  { title: "Amount", key: "amount", sortable: false, align: "end" },
  { title: "Close date", key: "close_date", sortable: false },
  { title: "Owner", key: "owner", sortable: false },
];

const employees = new Intl.NumberFormat("en-US");

const account = ref(null);
const loading = ref(false);
const loadError = ref("");

let requestId = 0;
async function load() {
  const id = ++requestId;
  loading.value = true;
  loadError.value = "";
  account.value = null;
  try {
    const body = await getJson(`/api/v1/accounts/${props.id}`);
    if (id !== requestId) return;
    account.value = body;
  } catch (err) {
    if (id !== requestId) return;
    loadError.value = err.message;
  } finally {
    if (id === requestId) loading.value = false;
  }
}

watch(() => props.id, load, { immediate: true });

function sumAmounts(opportunities) {
  return opportunities.reduce((total, opportunity) => total + Number(opportunity.amount || 0), 0);
}

const tiles = computed(() => {
  const opportunities = account.value?.opportunities ?? [];
  const open = opportunities.filter((opportunity) => OPEN_STAGES.includes(opportunity.stage));
  const won = opportunities.filter((opportunity) => opportunity.stage === "closed_won");
  const openPipeline = sumAmounts(open);
  const closedWon = sumAmounts(won);
  return [
    {
      key: "open-pipeline",
      label: "Open pipeline",
      value: money(openPipeline),
      title: moneyFull(openPipeline),
    },
    { key: "open-deals", label: "Open deals", value: String(open.length) },
    {
      key: "closed-won",
      label: "Closed won (all time)",
      value: money(closedWon),
      title: moneyFull(closedWon),
    },
    {
      key: "employees",
      label: "Employees",
      value: employees.format(account.value?.employee_count ?? 0),
    },
  ];
});

const subtitle = computed(() =>
  account.value ? `${account.value.industry} · ${account.value.region}` : "",
);

function stageColor(stage) {
  if (stage === "closed_won") return "success";
  if (stage === "closed_lost") return "grey";
  return "secondary";
}
</script>

<template>
  <v-container fluid>
    <v-btn
      to="/accounts"
      variant="text"
      color="secondary"
      class="mb-2 px-0"
      data-test="back"
    >
      ← Accounts
    </v-btn>

    <v-progress-linear v-if="loading" indeterminate color="secondary" class="mb-4" />

    <v-alert v-if="loadError" type="error" variant="tonal" data-test="load-error">
      {{ loadError }}
    </v-alert>

    <template v-if="account">
      <PageHeader :title="account.name" :subtitle="subtitle">
        <v-btn
          v-if="account.website"
          :href="account.website"
          target="_blank"
          rel="noopener"
          variant="outlined"
          color="secondary"
          data-test="website"
        >
          Website ↗
        </v-btn>
      </PageHeader>

      <v-row class="mb-2">
        <v-col v-for="tile in tiles" :key="tile.key" cols="12" sm="6" md="3">
          <v-card :data-test="`tile-${tile.key}`" class="h-100">
            <v-card-text>
              <div class="text-subtitle-1 text-medium-emphasis">{{ tile.label }}</div>
              <div class="text-h4 font-weight-bold" :title="tile.title" data-test="tile-value">
                {{ tile.value }}
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>

      <v-row>
        <v-col cols="12" lg="8">
          <v-card>
            <v-card-title>Opportunities</v-card-title>
            <v-data-table
              :headers="opportunityHeaders"
              :items="account.opportunities"
              :items-per-page="10"
              :items-per-page-options="[10]"
              item-value="id"
              no-data-text="No opportunities yet"
              data-test="opportunities"
            >
              <template #[`item.stage`]="{ item }">
                <v-chip
                  size="small"
                  :color="stageColor(item.stage)"
                  :data-test="`stage-${item.id}`"
                >
                  {{ stageTitle(item.stage) }}
                </v-chip>
              </template>
              <template #[`item.amount`]="{ item }">{{ moneyFull(item.amount) }}</template>
              <template #[`item.close_date`]="{ item }">{{ shortDate(item.close_date) }}</template>
              <template #[`item.owner`]="{ item }">
                <span v-if="item.owner">{{ item.owner.name }}</span>
                <span v-else class="text-grey">—</span>
              </template>
            </v-data-table>
          </v-card>
        </v-col>

        <v-col cols="12" lg="4">
          <v-card class="mb-4" data-test="contacts">
            <v-card-title>Contacts</v-card-title>
            <v-card-text v-if="!account.contacts.length" class="text-medium-emphasis">
              No contacts yet
            </v-card-text>
            <v-list v-else lines="three">
              <v-list-item
                v-for="contact in account.contacts"
                :key="contact.id"
                data-test="contact"
              >
                <v-list-item-title>{{ contact.first_name }} {{ contact.last_name }}</v-list-item-title>
                <v-list-item-subtitle v-if="contact.title">{{ contact.title }}</v-list-item-subtitle>
                <div class="text-body-2">
                  <a :href="`mailto:${contact.email}`">{{ contact.email }}</a>
                  <span v-if="contact.phone" class="text-medium-emphasis">
                    · {{ contact.phone }}
                  </span>
                </div>
              </v-list-item>
            </v-list>
          </v-card>

          <v-card data-test="details">
            <v-card-title>Details</v-card-title>
            <v-list density="compact">
              <v-list-item title="Owner" :subtitle="account.owner?.name ?? 'Unassigned'" />
              <v-list-item title="Annual revenue" :subtitle="moneyFull(account.annual_revenue)" />
              <v-list-item title="Customer since" :subtitle="shortDate(account.created_at)" />
            </v-list>
          </v-card>
        </v-col>
      </v-row>
    </template>
  </v-container>
</template>
