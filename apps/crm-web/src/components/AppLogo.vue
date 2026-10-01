<script setup>
import { logoColors, markArcs, markDot } from "../logo";
</script>

<template>
  <svg
    class="app-logo"
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 200 40"
    height="32"
    role="img"
    aria-label="PragMattie Sync"
  >
    <rect width="40" height="40" rx="8" :fill="logoColors.navy" />
    <path
      v-for="d in markArcs"
      :key="d"
      :d="d"
      fill="none"
      :stroke="logoColors.teal"
      stroke-width="4"
      stroke-linecap="round"
    />
    <circle :cx="markDot.cx" :cy="markDot.cy" :r="markDot.r" :fill="logoColors.gold" />
    <text x="50" y="27" font-family="inherit" font-size="20" font-weight="700">
      <tspan :fill="logoColors.navy">PragMattie</tspan>
      <tspan :fill="logoColors.teal" dx="5">Sync</tspan>
    </text>
  </svg>
</template>

<style scoped>
.app-logo {
  display: block;
  width: auto;
}
</style>
