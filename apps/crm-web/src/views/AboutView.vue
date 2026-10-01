<script setup>
import { version } from "../../package.json";
import PageHeader from "../components/PageHeader.vue";

const sections = [
  { title: "Leads", text: "Capture, qualify and convert new leads." },
  { title: "Accounts", text: "Companies and their contacts in one place." },
  { title: "Pipeline", text: "Every open deal by stage, owner and close date." },
  { title: "Forecast", text: "Where the quarter will land, by rep and stage." },
];
</script>

<template>
  <v-container>
    <PageHeader
      title="PragMattie Sync CRM"
      subtitle="A CRM for mid-market B2B sales teams: leads, accounts, pipeline and forecasting in one place."
    />

    <p class="text-body-1 mb-6" data-test="version">Version {{ version }}</p>

    <h2 class="text-h6 mb-2">What's in this release</h2>
    <v-list density="compact" data-test="release-sections">
      <v-list-item
        v-for="section in sections"
        :key="section.title"
        :title="section.title"
        :subtitle="section.text"
      />
    </v-list>
  </v-container>
</template>
