<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { currentOrg } from '@/lib/session'
import { flag } from '@/lib/flags'

const { t } = useI18n()
const org = computed(currentOrg)
const features = [
  { to: '/tenders', icon: 'pi pi-globe', title: 'eForms + multilingual', text: 'TED and e-Zamówienia notices in any EU language, summarised in yours, with citations to the original.' },
  { to: '/evidence', icon: 'pi pi-id-card', title: 'eCertis equivalence', text: '“Germany asks for X, the Polish equivalent is Y.”' },
  { to: '/partners', icon: 'pi pi-users', title: 'Cross-border partners', text: 'Cover a shortfall with a partner from another EU country.' },
  { to: '/deals', icon: 'pi pi-bolt', title: 'Autonomous deals', text: 'Agents negotiate within pre-approved limits; full match closes without a human.' },
  { to: '/compliance', icon: 'pi pi-shield', title: 'Buy-European', text: 'BOM EU content, SAFE/EDIP badge, origin, ownership, sanctions.' },
  { to: '/intelligence', icon: 'pi pi-chart-line', title: 'Demand intelligence', text: 'SAFE allocations, prime-award cascade, framework call-offs, news.' },
  { to: '/reach', icon: 'pi pi-box', title: 'REACH/PFAS + financing', text: 'Flag PFAS-exposed parts and prepare documents banks ask for.' },
]
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ t('home.title') }}</h1>
        <p class="muted">{{ t('home.subtitle') }}</p>
      </div>
    </div>
    <div v-if="org" class="card org">
      <div class="org-flag">{{ flag(org.country) }}</div>
      <div>
        <h2>{{ org.name }}</h2>
        <div class="muted">{{ org.city }}, {{ org.country }} · {{ org.description }}</div>
      </div>
    </div>
    <h2>{{ t('home.features') }}</h2>
    <div class="grid">
      <RouterLink v-for="f in features" :key="f.title" :to="f.to" class="card feature">
        <i :class="f.icon" />
        <h2>{{ f.title }}</h2>
        <p class="muted">{{ f.text }}</p>
      </RouterLink>
    </div>
  </div>
</template>

<style scoped>
.org { display: flex; gap: 16px; align-items: center; }
.org-flag { font-size: 40px; }
.feature { display: block; color: inherit; text-decoration: none; transition: border-color 0.2s, transform 0.2s; }
.feature:hover { border-color: var(--accent); transform: translateY(-2px); }
.feature i { font-size: 22px; color: var(--accent); }
.feature h2 { margin-top: 10px; }
.feature p { margin: 6px 0 0; }
</style>
