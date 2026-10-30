<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import SelectButton from 'primevue/selectbutton'
import ToggleSwitch from 'primevue/toggleswitch'
import { useToast } from 'primevue/usetoast'
import { api, post } from '@/lib/api'
import { session } from '@/lib/session'
import { formatDate } from '@/lib/tenders'

type Stage = 'Qualifying' | 'FindingPartner' | 'Negotiating' | 'Drafting' | 'AwaitingSignature' | 'Signed' | 'Declined' | 'Stopped'
interface Run {
  id: string; tenderId: string; tenderRef: string; title: string; verdict: string; stage: Stage; dealId: string | null
  note: string; createdAt: string; decidedAt: string | null; decidedBy: string | null; result: 'won' | 'lost' | null; demo: boolean
}
interface State { enabled: boolean; telegram: { configured: boolean; connected: boolean }; runs: Run[] }

const { t } = useI18n()
const toast = useToast()
const state = ref<State>()
const busy = ref<string>()
let timer: ReturnType<typeof setInterval> | undefined

async function load(quiet = false) {
  if (!session.orgId) return
  state.value = await api<State>('/autobid', {}, quiet)
}
watch(() => session.orgId, () => load(), { immediate: true })
// Poll while the agent may be working, so stages update live.
timer = setInterval(() => { if (state.value?.enabled || state.value?.runs.some((r) => isWorking(r.stage))) void load(true) }, 3000)
onBeforeUnmount(() => clearInterval(timer))

const isWorking = (s: Stage) => ['Qualifying', 'FindingPartner', 'Negotiating', 'Drafting'].includes(s)
const severity: Record<Stage, string> = {
  Qualifying: 'info', FindingPartner: 'info', Negotiating: 'info', Drafting: 'info',
  AwaitingSignature: 'warn', Signed: 'success', Declined: 'secondary', Stopped: 'secondary',
}

async function toggle(enabled: boolean) {
  await api('/autobid', { method: 'PUT', body: JSON.stringify({ enabled }) })
  await load()
}
async function act(key: string, fn: () => Promise<unknown>) {
  busy.value = key
  try {
    const r = (await fn()) as { message?: string } | undefined
    if (r?.message) toast.add({ severity: 'success', summary: r.message, life: 3000 })
    await load()
  } finally {
    busy.value = undefined
  }
}
const decide = (r: Run, sign: boolean) => act(r.id + sign, () => post(`/autobid/runs/${r.id}/decision`, { sign }))
const result = (r: Run, won: boolean) => act(r.id + won, () => post(`/autobid/runs/${r.id}/result`, { won }))
const scan = () => act('scan', () => post('/autobid/scan'))
async function testTelegram() {
  try {
    await post('/autobid/telegram/test')
    toast.add({ severity: 'success', summary: t('autobid.testSent'), life: 3000 })
  } catch {
    toast.add({ severity: 'warn', summary: t('autobid.telegramOff'), life: 4000 })
  }
}

// History filter: what is waiting for you, what you signed, what you turned down, what the agents gave up on.
type Filter = 'all' | 'waiting' | 'signed' | 'declined' | 'stopped'
const filter = ref<Filter>('all')
const matches: Record<Filter, (r: Run) => boolean> = {
  all: () => true,
  waiting: (r) => r.stage === 'AwaitingSignature' || isWorking(r.stage),
  signed: (r) => r.stage === 'Signed',
  declined: (r) => r.stage === 'Declined',
  stopped: (r) => r.stage === 'Stopped',
}
const filters = computed(() => (['all', 'waiting', 'signed', 'declined', 'stopped'] as Filter[]).map((f) => ({
  value: f, label: `${t(`autobid.filter.${f}`)} ${state.value?.runs.filter(matches[f]).length ?? 0}`,
})))
const shown = computed(() => state.value?.runs.filter(matches[filter.value]) ?? [])

const telegramText = computed(() =>
  !state.value?.telegram.configured ? t('autobid.telegramMissing') : state.value.telegram.connected ? t('autobid.telegramOn') : t('autobid.telegramStart'))
</script>

<template>
  <section v-if="state" class="ink-panel autobid">
    <div class="head">
      <div class="head-text">
        <h2>{{ t('autobid.title') }}</h2>
        <p class="muted">{{ t('autobid.hint') }}</p>
      </div>
      <label class="switch">
        <ToggleSwitch :model-value="state.enabled" @update:model-value="toggle" />
        <strong>{{ state.enabled ? t('autobid.on') : t('autobid.off') }}</strong>
      </label>
    </div>

    <div class="bar">
      <span class="tg" :class="{ ok: state.telegram.connected }"><i class="pi pi-send" aria-hidden="true" /> {{ telegramText }}</span>
      <div class="bar-actions">
        <Button v-if="state.telegram.connected" :label="t('autobid.test')" size="small" text class="on-dark" @click="testTelegram" />
        <Button :label="t('autobid.scan')" icon="pi pi-search" size="small" severity="secondary" :disabled="!state.enabled" :loading="busy === 'scan'" @click="scan" />
      </div>
    </div>

    <p v-if="!state.runs.length" class="muted empty">{{ state.enabled ? t('autobid.waiting') : t('autobid.none') }}</p>
    <template v-else>
    <div class="history-head">
      <h3>{{ t('autobid.history') }}</h3>
      <SelectButton v-model="filter" :options="filters" option-label="label" option-value="value" :allow-empty="false" size="small" class="filters" />
    </div>
    <p v-if="!shown.length" class="muted empty">{{ t('autobid.nothingHere') }}</p>
    <ol v-else class="runs">
      <li v-for="r in shown" :key="r.id" class="run">
        <div class="run-main">
          <RouterLink :to="`/tenders/${encodeURIComponent(r.tenderId)}`" class="run-title">{{ r.title }}</RouterLink>
          <div class="run-meta">
            <span class="mono">{{ r.tenderRef }}</span> · {{ formatDate(r.createdAt, true) }}<span v-if="r.demo"> · {{ t('autobid.demo') }}</span>
            <span v-if="r.decidedBy"> · {{ t('autobid.decided', { who: r.decidedBy, when: formatDate(r.decidedAt, true) }) }}</span>
          </div>
          <div class="run-note">{{ r.note }}</div>
        </div>
        <div class="run-side">
          <Tag :value="t(`autobid.stage.${r.stage}`)" :severity="severity[r.stage]" :icon="isWorking(r.stage) ? 'pi pi-spin pi-spinner' : undefined" />
          <Tag v-if="r.result" :value="t(`autobid.result.${r.result}`)" :severity="r.result === 'won' ? 'success' : 'secondary'" />
          <div class="run-actions">
            <template v-if="r.stage === 'AwaitingSignature'">
              <Button :label="t('autobid.sign')" icon="pi pi-check" size="small" :loading="busy === r.id + true" @click="decide(r, true)" />
              <Button :label="t('autobid.decline')" size="small" severity="secondary" :loading="busy === r.id + false" @click="decide(r, false)" />
            </template>
            <template v-else-if="r.stage === 'Signed' && !r.result">
              <Button :label="t('autobid.won')" icon="pi pi-trophy" size="small" :loading="busy === r.id + true" @click="result(r, true)" />
              <Button :label="t('autobid.lost')" size="small" severity="secondary" :loading="busy === r.id + false" @click="result(r, false)" />
            </template>
            <RouterLink v-if="r.dealId" :to="`/deals/${r.dealId}/documents`" class="docs-link">{{ t('autobid.documents') }}</RouterLink>
          </div>
        </div>
      </li>
    </ol>
    </template>
  </section>
</template>

<style scoped>
.autobid { display: flex; flex-direction: column; gap: 18px; }
.head { display: flex; gap: 20px; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; }
.head-text { display: flex; flex-direction: column; gap: 6px; max-width: 70ch; }
.head-text h2 { color: #fff; font-size: 22px; font-weight: 500; letter-spacing: -0.02em; }
.head-text p { margin: 0; }
.switch { display: flex; gap: 10px; align-items: center; font-size: 15px; cursor: pointer; }
.bar { display: flex; gap: 12px; justify-content: space-between; align-items: center; flex-wrap: wrap; padding: 12px 14px; border: 1px dashed var(--ink-line); }
.tg { display: inline-flex; gap: 8px; align-items: center; font-size: 14px; color: var(--ink-text); }
.tg.ok { color: #9fe0bb; }
.bar-actions { display: flex; gap: 8px; align-items: center; }
.on-dark { color: var(--ink-text) !important; }
.empty { margin: 0; }
.history-head { display: flex; gap: 12px; justify-content: space-between; align-items: center; flex-wrap: wrap; }
.history-head h3 { margin: 0; font-size: 16px; font-weight: 600; color: #fff; }
.runs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; border: 1px dashed var(--ink-line); }
.run { display: flex; gap: 16px; justify-content: space-between; padding: 16px 18px; border-bottom: 1px dashed var(--ink-line); flex-wrap: wrap; }
.run:last-child { border-bottom: 0; }
.run-main { flex: 1 1 380px; display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.run-title { color: #fff; font-weight: 600; font-size: 16px; text-decoration: none; }
.run-title:hover { text-decoration: underline; }
.run-meta { font-size: 13px; color: var(--ink-muted); }
.run-note { font-size: 14.5px; color: var(--ink-text); }
.run-side { display: flex; flex-direction: column; gap: 8px; align-items: flex-end; }
.run-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.docs-link { color: var(--accent-on-dark); font-size: 14px; }
</style>
