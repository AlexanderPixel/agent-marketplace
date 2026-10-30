<script setup lang="ts">
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'

const route = useRoute()
const { t } = useI18n()
</script>

<template>
  <div class="page">
    <h1>{{ t(String(route.meta.title)) }}</h1>
    <div class="card muted">{{ t('home.features') }}</div>
  </div>
</template>
