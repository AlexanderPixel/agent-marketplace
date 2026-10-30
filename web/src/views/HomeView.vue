<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import BidSteps from '@/components/BidSteps.vue'
import { api } from '@/lib/api'
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

const days = computed(() => (featured.value ? daysLeft(featured.value.deadline) : null))

/** Every working area of the app, in the order a supplier meets them. */
const tools = [
  { key: 'tenders', to: '/tenders', icon: 'pi pi-search' },
  { key: 'evidence', to: '/evidence', icon: 'pi pi-id-card' },
  { key: 'partners', to: '/partners', icon: 'pi pi-users' },
  { key: 'deals', to: '/deals', icon: 'pi pi-briefcase' },
  { key: 'compliance', to: '/compliance', icon: 'pi pi-shield' },
  { key: 'intelligence', to: '/intelligence', icon: 'pi pi-chart-line' },
  { key: 'reach', to: '/reach', icon: 'pi pi-box' },
]
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
        <RouterLink to="/tenders" class="secondary-link">{{ t('home.browse') }}</RouterLink>
      </div>
    </section>

    <section class="how">
      <h2>{{ t('home.howTitle') }}</h2>
      <BidSteps :current="1" />
    </section>

    <section class="tools">
      <h2>{{ t('home.toolsTitle') }}</h2>
      <div class="grid">
        <RouterLink v-for="f in tools" :key="f.key" :to="f.to" class="card tool">
          <i :class="f.icon" aria-hidden="true" />
          <div class="tool-title">{{ t(`home.f.${f.key}.title`) }}</div>
          <p class="muted">{{ t(`home.f.${f.key}.text`) }}</p>
          <span class="tool-open">{{ t('home.open') }} <i class="pi pi-arrow-right" aria-hidden="true" /></span>
        </RouterLink>
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
.secondary-link { color: var(--ink-text); font-size: 14.5px; }
.secondary-link:hover { color: #fff; }
.how, .tools { display: flex; flex-direction: column; gap: 12px; }
.tool { display: flex; flex-direction: column; gap: 8px; text-decoration: none; color: var(--text); }
.tool:hover { border-color: var(--accent); }
.tool:hover .tool-open { text-decoration: underline; }
.tool > i { font-size: 20px; color: var(--accent); }
.tool-title { font-size: 17px; font-weight: 600; letter-spacing: -0.015em; }
.tool p { margin: 0; flex: 1; }
.tool-open { color: var(--accent); font-weight: 600; font-size: 14.5px; display: inline-flex; gap: 6px; align-items: center; }
.tool-open i { font-size: 12px; }
</style>
