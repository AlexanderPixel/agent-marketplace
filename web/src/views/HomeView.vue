<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import { api } from '@/lib/api'
import { autopilotRunning, startAutopilot } from '@/lib/autopilot'
import { useRouter } from 'vue-router'
import { currentOrg } from '@/lib/session'
import { flag } from '@/lib/flags'
import { daysLeft, formatDate, localTitle, type TenderSummary } from '@/lib/tenders'

const { t } = useI18n()
const org = computed(currentOrg)
const featured = ref<TenderSummary>()

onMounted(async () => {
  const list = await api<{ rows: TenderSummary[] }>('/tenders')
  featured.value = list.rows.find((r) => r.featured)
})

const router = useRouter()
async function runAutopilot() {
  if (featured.value) await startAutopilot(router, featured.value.id)
}

const days = computed(() => (featured.value ? daysLeft(featured.value.deadline) : null))

</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ org ? org.name : t('home.title') }}</h1>
        <p class="muted">{{ t('home.subtitle') }}</p>
      </div>
    </div>

    <section v-if="featured" class="ink-panel start">
      <div class="start-main">
        <div class="eyebrow">{{ t('home.recommended') }}</div>
        <h2 class="start-title">{{ flag(featured.buyerCountry) }} {{ localTitle(featured) }}</h2>
        <p class="muted">{{ featured.buyerName }}</p>
        <p class="muted">{{ t('home.recommendedHint') }}</p>
        <div class="start-facts">
          <span>{{ t('tenders.deadline') }}: <strong>{{ formatDate(featured.deadline) }}</strong></span>
          <span v-if="days !== null && days >= 0">{{ t('tenders.daysLeft', { n: days }) }}</span>
        </div>
      </div>
      <div class="start-actions">
        <RouterLink :to="`/tenders/${encodeURIComponent(featured.id)}`" class="btn-link">
          <Button :label="t('home.check')" icon="pi pi-bolt" />
        </RouterLink>
        <Button :label="t('autopilot.run')" icon="pi pi-play" severity="secondary" :disabled="autopilotRunning()" @click="runAutopilot" />
        <span class="ap-hint">{{ t('autopilot.hint') }}</span>
        <RouterLink to="/tenders" class="secondary-link">{{ t('home.browse') }}</RouterLink>
      </div>
    </section>

  </div>
</template>

<style scoped>
.start { display: flex; gap: 24px; justify-content: space-between; align-items: flex-end; flex-wrap: wrap; }
.start-main { flex: 1 1 420px; display: flex; flex-direction: column; gap: 8px; max-width: 72ch; }
.start-main p { margin: 0; }
.eyebrow { font-size: 13px; font-weight: 600; color: var(--accent-on-dark); }
.start-title { color: #fff; font-size: 26px; font-weight: 500; letter-spacing: -0.02em; line-height: 1.2; }
.start-facts { display: flex; gap: 16px; flex-wrap: wrap; font-size: 14.5px; color: var(--ink-text); margin-top: 4px; }
.start-facts strong { color: #fff; }
.start-actions { display: flex; flex-direction: column; gap: 12px; align-items: flex-start; }
.btn-link { text-decoration: none; }
.ap-hint { font-size: 13px; color: var(--ink-text); max-width: 34ch; }
.secondary-link { color: var(--ink-text); font-size: 14.5px; }
.secondary-link:hover { color: #fff; }
</style>
