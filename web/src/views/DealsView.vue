<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import Tag from 'primevue/tag'
import { api } from '@/lib/api'
import AutoBidPanel from '@/components/AutoBidPanel.vue'
import { flag } from '@/lib/flags'
import { session } from '@/lib/session'
import { dealSeverity, eur, formatDate, units, type Deal, type Party } from '@/lib/tenders'

const { t } = useI18n()
const router = useRouter()
const deals = ref<{ deal: Deal; bidder: Party; partner: Party }[]>([])

async function load() {
  if (!session.orgId) return
  deals.value = await api('/deals')
}
watch(() => session.orgId, load, { immediate: true })

const other = (r: { deal: Deal; bidder: Party; partner: Party }) => (r.deal.bidderOrgId === session.orgId ? r.partner : r.bidder)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ t('deals.title') }}</h1>
        <p class="muted">{{ t('deals.subtitle') }}</p>
      </div>
    </div>

    <AutoBidPanel />

    <div class="layout">
      <div class="list">
        <p v-if="!deals.length" class="card muted">{{ t('deals.empty') }}</p>
        <div v-for="r in deals" :key="r.deal.id" class="card deal" @click="router.push(`/deals/${r.deal.id}`)">
          <span class="dflag">{{ flag(other(r).country) }}</span>
          <div class="deal-main">
            <div class="deal-name">{{ other(r).name }}</div>
            <div class="muted">{{ r.deal.lotId }} · {{ r.deal.lotTitle }} · TED {{ r.deal.tenderRef }}</div>
          </div>
          <div class="deal-terms">
            <strong v-if="r.deal.unitPrice">{{ r.deal.quantity }} {{ units[r.deal.unit] ?? r.deal.unit }} × {{ eur(r.deal.unitPrice) }}</strong>
            <span class="muted">{{ formatDate(r.deal.createdAt, true) }}</span>
          </div>
          <Tag :severity="dealSeverity[r.deal.status]" :value="t(`deals.status.${r.deal.status}`)" :icon="r.deal.autonomous ? 'pi pi-bolt' : undefined" />
        </div>
      </div>

    </div>
  </div>
</template>

<style scoped>
.layout { display: grid; gap: 16px; }
.list { display: flex; flex-direction: column; gap: 10px; }
.deal { display: flex; gap: 14px; align-items: center; cursor: pointer; padding: 14px 18px; }
.deal:hover { border-color: var(--accent); }
.dflag { font-size: 28px; }
.deal-main { flex: 1; }
.deal-name { font-weight: 600; }
.deal-terms { display: flex; flex-direction: column; align-items: flex-end; font-size: 14px; }
</style>
