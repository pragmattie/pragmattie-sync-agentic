<script setup>
import AppSnackbar from "./components/AppSnackbar.vue";
import { navItems } from "./router";
import { useNavigationStore } from "./stores/navigation";

const navigation = useNavigationStore();
</script>

<template>
  <v-app>
    <v-app-bar color="primary">
      <v-app-bar-nav-icon @click="navigation.toggleDrawer" />
      <v-app-bar-title>PragMattie Sync CRM</v-app-bar-title>
    </v-app-bar>

    <v-navigation-drawer v-model="navigation.drawerOpen">
      <v-list nav>
        <v-list-item
          v-for="item in navItems"
          :key="item.name"
          :to="item.to"
          :title="item.title"
        />
      </v-list>
    </v-navigation-drawer>

    <v-main>
      <router-view />
    </v-main>

    <AppSnackbar />
  </v-app>
</template>
