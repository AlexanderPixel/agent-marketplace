<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import InputNumber from 'primevue/inputnumber'
import DatePicker from 'primevue/datepicker'
import Tag from 'primevue/tag'
import { useToast } from 'primevue/usetoast'
import { api } from '@/lib/api'
import AutoBidPanel from '@/components/AutoBidPanel.vue'
import { flag } from '@/lib/flags'
import { session } from '@/lib/session'
import { dealSeverity, eur, formatDate, units, type Deal, type Party } from '@/lib/tenders'

interface Mandate {
  product: string; buying: boolean; targetPrice: number; limitPrice: number; minQuantity: number; maxQuantity: number
  deliveryLimit: string; autonomyThreshold: number; autonomousValueCapEur: number
}

const { t } = useI18n()
const router = useRouter()
const toast = useToast()
const deals = ref<{ deal: Deal; bidder: Party; partner: Party }[]>([])
const mandate = ref<Mandate>()
const delivery = ref<Date>()
const saving = ref(false)

async function load() {
  if (!session.orgId) return
  deals.value = await api('/deals')
  mandate.value = await api<Mandate>('/mandate')
  delivery.value = mandate.value ? new Date(mandate.value.deliveryLimit) : undefined
}
watch(() => session.orgId, load, { immediate: true })

async function save() {
  if (!mandate.value || !delivery.value) return
  saving.value = true
  try {
    const d = delivery.value
    const deliveryLimit = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    mandate.value = await api<Mandate>('/mandate', { method: 'PUT', body: JSON.stringify({ ...mandate.value, deliveryLimit }) })
    toast.add({ severity: 'success', summary: t('deals.saved'), life: 2500 })
  } finally {
    saving.value = false
  }
}

const other = (r: { deal: Deal; bidder: Party; partner: Party }) => (r.deal.bidderOrgId === session.orgId ? r.partner : r.bidder)
const buying = computed(() => mandate.value?.buying)
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

      <div v-if="mandate" class="card mandate">
        <h2><i class="pi pi-lock" /> {{ t('deals.mandate') }}</h2>
        <p class="muted small">{{ t('deals.mandateHint') }}</p>
        <div class="product">
          <Tag :value="buying ? t('deals.buying') : t('deals.selling')" :severity="buying ? 'info' : 'success'" /> {{ mandate.product }}
        </div>
        <label>{{ t('deals.target') }}<InputNumber v-model="mandate.targetPrice" :min-fraction-digits="0" :max-fraction-digits="2" fluid /></label>
        <label>{{ buying ? t('deals.limitBuy') : t('deals.limitSell') }}<InputNumber v-model="mandate.limitPrice" :max-fraction-digits="2" fluid /></label>
        <label>{{ t('deals.qty') }}
          <span class="pair"><InputNumber v-model="mandate.minQuantity" fluid /><span>–</span><InputNumber v-model="mandate.maxQuantity" fluid /></span>
        </label>
        <label>{{ buying ? t('deals.deliveryBuy') : t('deals.deliverySell') }}<DatePicker v-model="delivery" date-format="yy-mm-dd" fluid /></label>
        <label>{{ t('deals.threshold') }}<InputNumber v-model="mandate.autonomyThreshold" :min="0" :max="100" suffix=" %" fluid /></label>
        <label>{{ t('deals.cap') }}<InputNumber v-model="mandate.autonomousValueCapEur" mode="currency" currency="EUR" :locale="session.language" fluid /></label>
        <Button :label="t('deals.save')" icon="pi pi-save" :loading="saving" @click="save" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; align-items: start; }
@media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }
.list { display: flex; flex-direction: column; gap: 10px; }
.deal { display: flex; gap: 14px; align-items: center; cursor: pointer; padding: 14px 18px; }
.deal:hover { border-color: var(--accent); }
.dflag { font-size: 28px; }
.deal-main { flex: 1; }
.deal-name { font-weight: 600; }
.deal-terms { display: flex; flex-direction: column; align-items: flex-end; font-size: 14px; }
.mandate { display: flex; flex-direction: column; gap: 12px; }
.mandate h2 { display: flex; gap: 8px; align-items: center; }
.mandate label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); }
.small { margin: 0; font-size: 13px; }
.product { display: flex; gap: 8px; align-items: center; font-weight: 500; }
.pair { display: flex; gap: 6px; align-items: center; }
</style>
