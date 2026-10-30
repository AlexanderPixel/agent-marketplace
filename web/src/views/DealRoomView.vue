<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import ActivityFeed from '@/components/ActivityFeed.vue'
import { api, post } from '@/lib/api'
import { flag } from '@/lib/flags'
import { session } from '@/lib/session'
import { dealSeverity, eur, formatDate, units, type Deal, type Party } from '@/lib/tenders'

const { t } = useI18n()
const route = useRoute()
const data = ref<{ deal: Deal; bidder: Party; partner: Party }>()
const approving = ref(false)
let timer: ReturnType<typeof setInterval> | undefined

async function load() {
  data.value = await api(`/deals/${route.params.id}`)
  if (data.value?.deal.status !== 'Negotiating') clearInterval(timer)
}
function start() {
  clearInterval(timer)
  load()
  timer = setInterval(load, 1000)
}
watch(() => [route.params.id, session.orgId], start, { immediate: true })
onBeforeUnmount(() => clearInterval(timer))

const deal = computed(() => data.value?.deal)
const unit = computed(() => (deal.value ? (units[deal.value.unit] ?? deal.value.unit) : ''))
const mySide = computed(() => (deal.value?.bidderOrgId === session.orgId ? 'bidder' : 'partner'))
const party = (side: string) => (side === 'bidder' ? data.value?.bidder : data.value?.partner)
const value = computed(() => (deal.value?.unitPrice && deal.value.quantity ? deal.value.unitPrice * deal.value.quantity : null))

async function approve() {
  approving.value = true
  try {
    await post(`/deals/${route.params.id}/approve`)
    await load()
  } finally {
    approving.value = false
  }
}
</script>

<template>
  <div v-if="deal && data" class="page">
    <RouterLink to="/deals" class="back"><i class="pi pi-arrow-left" /> {{ t('deals.title') }}</RouterLink>

    <div class="card head">
      <div class="parties">
        <div class="party"><span class="pflag">{{ flag(data.bidder.country) }}</span><div><strong>{{ data.bidder.name }}</strong><div class="muted">{{ data.bidder.city }}</div></div></div>
        <i class="pi pi-arrow-right-arrow-left link-icon" />
        <div class="party"><span class="pflag">{{ flag(data.partner.country) }}</span><div><strong>{{ data.partner.name }}</strong><div class="muted">{{ data.partner.city }}</div></div></div>
      </div>
      <div class="head-meta">
        <Tag :severity="dealSeverity[deal.status]" :value="t(`deals.status.${deal.status}`)" :icon="deal.status === 'Negotiating' ? 'pi pi-spin pi-spinner' : undefined" />
        <div class="muted">{{ deal.lotId }} · {{ deal.lotTitle }} · {{ deal.gap }} {{ unit }}</div>
        <RouterLink :to="`/tenders/${encodeURIComponent(deal.tenderId)}`" class="muted small">TED {{ deal.tenderRef }}</RouterLink>
      </div>
    </div>

    <div v-if="deal.status !== 'Negotiating'" class="card outcome" :class="deal.status">
      <i :class="deal.status === 'Closed' ? (deal.autonomous ? 'pi pi-bolt' : 'pi pi-user') : deal.status === 'AwaitingApproval' ? 'pi pi-hourglass' : 'pi pi-times-circle'" />
      <div class="outcome-main">
        <div class="outcome-title">
          {{ deal.status === 'Closed' ? (deal.autonomous ? t('deals.closedAuto') : t('deals.closedHuman')) : deal.status === 'AwaitingApproval' ? t('deals.awaiting') : t('deals.failed') }}
        </div>
        <div class="muted">{{ deal.outcome }}</div>
        <div v-if="deal.unitPrice" class="terms">
          <div><span class="muted">{{ t('deals.quantity') }}</span><strong>{{ deal.quantity }} {{ unit }}</strong></div>
          <div><span class="muted">{{ t('deals.price') }}</span><strong>{{ eur(deal.unitPrice) }}</strong></div>
          <div><span class="muted">{{ t('deals.value') }}</span><strong>{{ eur(value, 0) }}</strong></div>
          <div><span class="muted">{{ t('deals.delivery') }}</span><strong>{{ formatDate(deal.delivery) }}</strong></div>
          <div><span class="muted">{{ t('deals.match') }}</span><strong>{{ deal.matchScore }}%</strong></div>
        </div>
      </div>
      <div class="outcome-actions">
        <Button v-if="deal.status === 'AwaitingApproval'" :label="t('deals.approve')" icon="pi pi-check" :loading="approving" @click="approve" />
        <Button v-if="deal.status === 'Closed'" v-tooltip="t('qual.soon')" :label="t('deals.documents')" icon="pi pi-file-edit" disabled />
      </div>
    </div>

    <div class="layout">
      <div class="card room">
        <div class="room-head">
          <h2>{{ t('deals.room') }}</h2>
          <span v-if="deal.status === 'Negotiating'" class="live"><i class="pi pi-circle-fill" /> {{ t('deals.live') }}</span>
        </div>
        <p class="muted small"><i class="pi pi-lock" /> {{ t('deals.termsOnly') }}</p>
        <TransitionGroup name="msg" tag="div" class="messages">
          <div v-for="o in deal.offers" :key="o.round" class="msg" :class="[o.side, { mine: o.side === mySide, accept: o.accepts }]">
            <div class="who">{{ flag(party(o.side)?.country ?? '') }} {{ party(o.side)?.name }} · <i class="pi pi-microchip-ai" /> agent</div>
            <div class="bubble">
              <div class="chips">
                <span class="chip price">{{ eur(o.unitPrice) }}</span>
                <span class="chip">{{ o.quantity }} {{ unit }}</span>
                <span class="chip">{{ formatDate(o.delivery) }}</span>
                <span v-if="o.accepts" class="chip ok"><i class="pi pi-check" /> {{ t('deals.accepts') }}</span>
              </div>
              <div class="text">{{ o.message }}</div>
            </div>
          </div>
        </TransitionGroup>
        <div v-if="deal.status === 'Negotiating'" class="typing"><span /><span /><span /></div>
      </div>
      <div class="card side">
        <h2>{{ t('qual.activity') }}</h2>
        <ActivityFeed :subject="deal.tenderId" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.back { display: inline-flex; gap: 6px; align-items: center; text-decoration: none; font-size: 14px; }
.head { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
.parties { display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }
.party { display: flex; gap: 10px; align-items: center; }
.pflag { font-size: 30px; }
.link-icon { color: var(--accent); font-size: 18px; }
.head-meta { display: flex; flex-direction: column; gap: 4px; align-items: flex-end; text-align: right; }
.small { font-size: 12.5px; }
.outcome { display: flex; gap: 18px; align-items: center; border-left: 6px solid var(--muted); flex-wrap: wrap; }
.outcome > i { font-size: 32px; }
.outcome.Closed { border-left-color: var(--good); background: var(--good-soft); }
.outcome.Closed > i { color: var(--good); }
.outcome.AwaitingApproval { border-left-color: var(--warn); background: var(--warn-soft); }
.outcome.AwaitingApproval > i { color: var(--warn); }
.outcome.Failed { border-left-color: var(--bad); background: var(--bad-soft); }
.outcome-main { flex: 1 1 420px; display: flex; flex-direction: column; gap: 6px; }
.outcome-title { font-size: 20px; font-weight: 600; }
.terms { display: flex; gap: 28px; flex-wrap: wrap; margin-top: 6px; }
.terms div { display: flex; flex-direction: column; }
.terms strong { font-size: 18px; }
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; align-items: start; }
@media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }
.side { position: sticky; top: 16px; max-height: calc(100vh - 32px); overflow: auto; }
.room-head { display: flex; justify-content: space-between; align-items: center; }
.live { color: var(--good); font-size: 13px; font-weight: 600; display: inline-flex; gap: 6px; align-items: center; }
.live i { font-size: 9px; animation: pulse 1.2s infinite; }
@keyframes pulse { 50% { opacity: 0.3; } }
.messages { display: flex; flex-direction: column; gap: 14px; margin-top: 8px; }
.msg { display: flex; flex-direction: column; gap: 4px; max-width: 78%; }
.msg.partner { align-self: flex-end; align-items: flex-end; }
.who { font-size: 12px; color: var(--muted); }
.bubble { background: var(--ground); border: 1px solid var(--border); border-radius: 14px; padding: 10px 14px; display: flex; flex-direction: column; gap: 6px; }
.msg.bidder .bubble { border-top-left-radius: 4px; }
.msg.partner .bubble { border-top-right-radius: 4px; background: var(--accent-soft); }
.msg.accept .bubble { border-color: var(--good); background: var(--good-soft); }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-size: 12.5px; border-radius: 6px; padding: 2px 8px; background: var(--surface); border: 1px solid var(--border); }
.chip.price { font-weight: 700; }
.chip.ok { color: var(--good); border-color: var(--good); }
.text { font-size: 14.5px; }
.typing { display: flex; gap: 4px; padding: 14px 4px 0; }
.typing span { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); animation: pulse 1s infinite; }
.typing span:nth-child(2) { animation-delay: 0.2s; }
.typing span:nth-child(3) { animation-delay: 0.4s; }
.msg-enter-active { transition: all 0.35s ease; }
.msg-enter-from { opacity: 0; transform: translateY(8px); }
</style>
