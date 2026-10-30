<script setup lang="ts">
import BidSteps from '@/components/BidSteps.vue'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import ActivityFeed from '@/components/ActivityFeed.vue'
import { api, post } from '@/lib/api'
import { advance, autopilotAt, beat, pointAt, showSection, stopAutopilot } from '@/lib/autopilot'
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

// Autopilot step 4: watch the agents; on a close go to the documents. Approval stays with a person.
const router = useRouter()
watch(() => deal.value?.status, async (status) => {
  if (!autopilotAt('negotiate') || !status || status === 'Negotiating') return
  if (status === 'AwaitingApproval') return stopAutopilot('needsApproval')
  if (status !== 'Closed') return stopAutopilot('dealFailed')
  advance('documents', 'documents')
  await nextTick()
  await showSection('[data-ap="outcome"]', 'start')
  await beat(3200)
  await pointAt('[data-ap="documents"]', { click: true })
  if (autopilotAt('documents')) router.push(`/deals/${route.params.id}/documents`)
})

// While the agents talk, keep the newest offer on screen and the pointer on it.
watch(() => deal.value?.offers.length, async (n, before) => {
  if (!autopilotAt('negotiate') || !n || n === before) return
  await nextTick()
  const offers = document.querySelectorAll('.messages .msg')
  await pointAt(offers[offers.length - 1])
})

// The closing figures count up once when the deal closes; reduced motion shows them straight away.
const shown = ref({ quantity: 0, price: 0, value: 0, match: 0 })
watch(() => deal.value?.unitPrice, () => {
  const d = deal.value
  if (!d?.unitPrice) return
  const target = { quantity: d.quantity ?? 0, price: d.unitPrice, value: value.value ?? 0, match: d.matchScore ?? 0 }
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return void (shown.value = target)
  const started = performance.now()
  const tick = (now: number) => {
    const p = Math.min(1, (now - started) / 900)
    const e = 1 - (1 - p) ** 3
    shown.value = { quantity: target.quantity * e, price: target.price * e, value: target.value * e, match: target.match * e }
    if (p < 1) requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
}, { immediate: true })

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
    <BidSteps :current="deal.status === 'Closed' ? 5 : 4" />
    <RouterLink to="/deals" class="back"><i class="pi pi-arrow-left" /> {{ t('deals.title') }}</RouterLink>

    <div class="card head" :class="{ negotiating: deal.status === 'Negotiating' }">
      <div class="parties">
        <div class="party"><span class="pflag">{{ flag(data.bidder.country) }}</span><div><strong>{{ data.bidder.name }}</strong><div class="muted">{{ data.bidder.city }}</div></div></div>
        <i class="pi pi-arrow-right-arrow-left link-icon" :class="{ busy: deal.status === 'Negotiating' }" aria-hidden="true" />
        <div class="party"><span class="pflag">{{ flag(data.partner.country) }}</span><div><strong>{{ data.partner.name }}</strong><div class="muted">{{ data.partner.city }}</div></div></div>
      </div>
      <div class="head-meta">
        <Tag :severity="dealSeverity[deal.status]" :value="t(`deals.status.${deal.status}`)" :icon="deal.status === 'Negotiating' ? 'pi pi-spin pi-spinner' : undefined" />
        <div class="muted">{{ deal.lotId }} · {{ deal.lotTitle }} · {{ deal.gap }} {{ unit }}</div>
        <RouterLink :to="`/tenders/${encodeURIComponent(deal.tenderId)}`" class="muted small">TED {{ deal.tenderRef }}</RouterLink>
      </div>
    </div>

    <Transition name="reveal" appear>
      <div v-if="deal.status !== 'Negotiating'" class="ink-panel outcome" data-ap="outcome" :class="deal.status">
        <div class="outcome-head">
          <span class="stamp" aria-hidden="true">
            <i :class="deal.status === 'Closed' ? (deal.autonomous ? 'pi pi-bolt' : 'pi pi-user') : deal.status === 'AwaitingApproval' ? 'pi pi-hourglass' : 'pi pi-times'" />
          </span>
          <div class="outcome-heading">
            <div class="outcome-title">
              {{ deal.status === 'Closed' ? (deal.autonomous ? t('deals.closedAuto') : t('deals.closedHuman')) : deal.status === 'AwaitingApproval' ? t('deals.awaiting') : t('deals.failed') }}
            </div>
            <!-- The terms grid already states the numbers, so a closed deal gets a short line instead of repeating them. -->
            <div class="muted">{{ deal.unitPrice ? t('deals.closedNote') : deal.outcome }}</div>
          </div>
          <div class="outcome-actions">
            <Button v-if="deal.status === 'AwaitingApproval'" :label="t('deals.approve')" icon="pi pi-check" :loading="approving" @click="approve" />
            <RouterLink v-if="deal.status === 'Closed'" :to="`/deals/${deal.id}/documents`">
              <Button data-ap="documents" :label="t('deals.documents')" icon="pi pi-file-edit" />
            </RouterLink>
          </div>
        </div>
        <dl v-if="deal.unitPrice" class="terms">
          <div style="--i: 0"><dt>{{ t('deals.quantity') }}</dt><dd>{{ Math.round(shown.quantity) }} {{ unit }}</dd></div>
          <div style="--i: 1"><dt>{{ t('deals.price') }}</dt><dd>{{ eur(shown.price) }}</dd></div>
          <div style="--i: 2"><dt>{{ t('deals.value') }}</dt><dd>{{ eur(shown.value, 0) }}</dd></div>
          <div style="--i: 3"><dt>{{ t('deals.delivery') }}</dt><dd>{{ formatDate(deal.delivery) }}</dd></div>
          <div style="--i: 4"><dt>{{ t('deals.match') }}</dt><dd>{{ Math.round(shown.match) }}%</dd></div>
        </dl>
      </div>
    </Transition>

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
.party { display: flex; gap: 10px; align-items: center; font-size: 17px; }
.pflag { font-size: 30px; }
.link-icon { color: var(--accent); font-size: 18px; padding: 8px; border: 1px dashed var(--dash); }
.head-meta { display: flex; flex-direction: column; gap: 4px; align-items: flex-end; text-align: right; }
.small { font-size: 12.5px; }
.outcome { display: flex; flex-direction: column; gap: 24px; }
.outcome-head { display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }
.stamp { width: 48px; height: 48px; flex: none; display: grid; place-items: center; background: var(--accent); color: #fff; clip-path: var(--chamfer-sm); font-size: 20px; }
.outcome.Closed .stamp { background: var(--good); }
.outcome.AwaitingApproval .stamp { background: #b07a00; }
.outcome.Failed .stamp { background: var(--bad); }
.outcome-heading { flex: 1 1 360px; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.outcome-title { font-size: 28px; font-weight: 500; letter-spacing: -0.03em; line-height: 1.15; }
.outcome-actions { flex: none; }
.terms { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); margin: 0; border: 1px dashed var(--ink-line); }
.terms div { display: flex; flex-direction: column; gap: 8px; padding: 16px 20px; border-right: 1px dashed var(--ink-line); }
.terms div:last-child { border-right: 0; }
.terms dt { font-size: 13px; color: var(--ink-muted); }
.terms dd { margin: 0; font-size: 28px; font-weight: 500; letter-spacing: -0.03em; white-space: nowrap; font-variant-numeric: tabular-nums; }

/* One orchestrated reveal when the deal resolves: panel rises in, the stamp lands, the figures follow. */
.reveal-enter-active { transition: opacity 0.45s ease, transform 0.45s cubic-bezier(0.2, 0.8, 0.2, 1); }
.reveal-enter-from { opacity: 0; transform: translateY(10px); }
.reveal-enter-active .stamp { animation: stamp 0.5s 0.15s cubic-bezier(0.2, 0.8, 0.2, 1.4) both; }
.reveal-enter-active .terms div { animation: rise 0.4s calc(0.25s + var(--i) * 0.06s) ease both; }
@keyframes stamp { from { transform: scale(0.4) rotate(-12deg); opacity: 0; } to { transform: none; opacity: 1; } }
@keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

/* While negotiating: a thin running line under the parties and a gently moving link icon. */
.head { position: relative; overflow: hidden; }
.head.negotiating::after { content: ''; position: absolute; left: 0; bottom: 0; height: 2px; width: 30%; background: var(--accent); animation: scan 1.6s ease-in-out infinite; }
@keyframes scan { from { transform: translateX(-100%); } to { transform: translateX(340%); } }
.link-icon.busy { animation: sway 1.4s ease-in-out infinite; }
@keyframes sway { 50% { transform: translateX(3px); } }

@media (prefers-reduced-motion: reduce) {
  .reveal-enter-active, .reveal-enter-active .stamp, .reveal-enter-active .terms div, .msg-enter-active { transition: none; animation: none; }
  .head.negotiating::after, .link-icon.busy, .typing span, .live i { animation: none; }
}
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; align-items: start; }
@media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }
.side { position: sticky; top: 16px; max-height: calc(100vh - 32px); overflow: auto; }
.room-head { display: flex; justify-content: space-between; align-items: center; }
.live { color: var(--accent); font-size: 13px; font-weight: 600; display: inline-flex; gap: 6px; align-items: center; }
.live i { font-size: 9px; animation: pulse 1.2s infinite; }
@keyframes pulse { 50% { opacity: 0.3; } }
.messages { display: flex; flex-direction: column; gap: 14px; margin-top: 8px; }
.msg { display: flex; flex-direction: column; gap: 4px; max-width: 78%; }
.msg.partner { align-self: flex-end; align-items: flex-end; }
.who { font-size: 12.5px; color: var(--muted); }
.bubble { background: var(--ground); border: 1px solid var(--border); padding: 10px 14px; display: flex; flex-direction: column; gap: 6px; }
.msg.partner .bubble { background: var(--accent-soft); }
.msg.mine .bubble { background: var(--ink); color: #f6f6f6; border-color: var(--ink); }
.msg.mine .chip { background: transparent; color: #f6f6f6; border-color: var(--ink-line); }
.msg.accept .bubble { outline: 2px solid var(--good); outline-offset: 2px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-family: var(--mono); font-size: 12px; padding: 2px 8px; background: var(--surface); border: 1px solid var(--border); }
.chip.price { font-weight: 500; background: var(--accent); color: #fff; border-color: var(--accent); }
.chip.ok { color: var(--good); border-color: var(--good); }
.text { font-size: 14.5px; }
.typing { display: flex; gap: 4px; padding: 14px 4px 0; }
.typing span { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); animation: pulse 1s infinite; }
.typing span:nth-child(2) { animation-delay: 0.2s; }
.typing span:nth-child(3) { animation-delay: 0.4s; }
.msg-enter-active { transition: all 0.35s ease; }
.msg-enter-from { opacity: 0; transform: translateY(8px); }
</style>
