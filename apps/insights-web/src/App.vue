<script setup>
import AppFooter from "./components/AppFooter.vue";
import AppLogo from "./components/AppLogo.vue";
import { navItems } from "./router";
import { useNavigationStore } from "./stores/navigation";

const navigation = useNavigationStore();
</script>

<template>
  <v-app>
    <v-app-bar color="surface" class="border-b" flat>
      <v-app-bar-nav-icon
        color="primary"
        aria-label="Toggle navigation"
        data-test="nav-toggle"
        @click="navigation.toggleDrawer"
      />
      <router-link to="/" class="d-flex align-center ga-2 text-decoration-none ml-2">
        <AppLogo />
        <span class="text-h6 text-primary" data-test="wordmark">Delivery Insights</span>
      </router-link>
    </v-app-bar>

    <v-navigation-drawer v-model="navigation.drawerOpen" data-test="nav-drawer">
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

    <AppFooter />
  </v-app>
</template>
