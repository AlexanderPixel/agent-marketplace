<script setup lang="ts">
import BidSteps from '@/components/BidSteps.vue'
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import ActivityFeed from '@/components/ActivityFeed.vue'
import { api, post } from '@/lib/api'
import { autopilotAt, beat, pointAt, showSection } from '@/lib/autopilot'
import { flag } from '@/lib/flags'
import { session } from '@/lib/session'
import { localTitle, units, type TenderLot, type TenderSummary } from '@/lib/tenders'

interface Listing {
  orgId: string; name: string; country: string; city: string; description: string
  certificates: string[]; products: string[]; capacity: number | null; unit: string | null; leadTimeDays: number | null
}
interface Candidate {
  partner: Listing; score: number; gatesPassed: boolean
  gates: { name: string; passed: boolean; detail: string }[]
  breakdown: { name: string; points: number; max: number; detail: string }[]
}
interface SearchResult { tender: TenderSummary; lot: TenderLot; gap: number; candidates: Candidate[] }

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const starting = ref<string>()
const threshold = 85
const network = ref<Listing[]>([])
const result = ref<SearchResult>()
const searching = ref(false)

const query = computed(() =>
  route.query.tender && route.query.lot && route.query.gap
    ? { tenderId: String(route.query.tender), lotId: String(route.query.lot), gap: Number(route.query.gap) }
    : null,
)

async function load() {
  result.value = undefined
  if (!session.orgId) return
  network.value = await api<Listing[]>('/partners')
  if (query.value) {
    searching.value = true
    try {
      result.value = await post<SearchResult>('/partners/search', query.value)
    } finally {
      searching.value = false
    }
    void autoNegotiate()
  }
}

// Autopilot, partner step: point at the partner the server picks (best score with all hard gates passed).
async function autoNegotiate() {
  if (!autopilotAt('partner')) return
  const best = [...(result.value?.candidates ?? [])].filter((c) => c.gatesPassed).sort((a, b) => b.score - a.score)[0]
  if (!best) return
  await nextTick()
  await showSection(`[data-ap="candidate-${best.partner.orgId}"]`, 'start')
  await beat(1500)
  await pointAt(`[data-ap="negotiate-${best.partner.orgId}"]`, { click: true })
}
watch([() => session.orgId, query], load, { immediate: true })

async function negotiate(c: Candidate) {
  if (!query.value) return
  starting.value = c.partner.orgId
  try {
    const { id } = await post<{ id: string }>('/deals', { ...query.value, partnerOrgId: c.partner.orgId })
    router.push(`/deals/${id}`)
  } finally {
    starting.value = undefined
  }
}

const unitName = (u: string | null | undefined) => (u ? (units[u] ?? u) : '')
const status = (c: Candidate) => (!c.gatesPassed ? 'failed' : c.score >= threshold ? 'autonomous' : 'approval')
</script>

<template>
  <div class="page">
    <BidSteps v-if="query" :current="3" />
    <div class="page-head">
      <div>
        <h1>{{ t('partners.title') }}</h1>
        <p class="muted">{{ t('partners.subtitle') }}</p>
      </div>
    </div>

    <div class="layout">
      <div class="main">
        <template v-if="query">
          <div v-if="result" class="card gap">
            <i class="pi pi-users" />
            <div>
              <div class="gap-title">{{ t('partners.gapFor', { gap: result.gap, unit: unitName(result.lot.unit), lot: `${result.lot.id} · ${result.lot.title}` }) }}</div>
              <RouterLink :to="`/tenders/${encodeURIComponent(result.tender.id)}`" class="muted">{{ flag('DE') }} {{ localTitle(result.tender) }}</RouterLink>
            </div>
          </div>
          <p v-if="searching" class="muted"><i class="pi pi-spin pi-spinner" /> {{ t('partners.searching') }}</p>

          <div v-for="c in result?.candidates ?? []" :key="c.partner.orgId" class="card candidate" :class="status(c)" :data-ap="`candidate-${c.partner.orgId}`">
            <div class="cand-head">
              <div class="ring" :style="{ '--p': c.score }"><span>{{ c.score }}%</span></div>
              <div class="cand-main">
                <div class="cand-name">{{ flag(c.partner.country) }} {{ c.partner.name }}</div>
                <div class="muted">{{ c.partner.city }}, {{ c.partner.country }} · {{ c.partner.description }}</div>
                <div class="chips">
                  <Tag v-for="cert in c.partner.certificates" :key="cert" :value="cert" severity="secondary" icon="pi pi-verified" />
                  <Tag :value="`${t('partners.capacity')}: ${c.partner.capacity} ${unitName(c.partner.unit)}`" severity="secondary" />
                  <Tag :value="t('partners.leadTime', { n: c.partner.leadTimeDays })" severity="secondary" />
                </div>
              </div>
            </div>

            <div class="cols">
              <div>
                <div class="col-title">{{ t('partners.gates') }}</div>
                <div v-for="g in c.gates" :key="g.name" class="gate">
                  <i :class="g.passed ? 'pi pi-check-circle good' : 'pi pi-times-circle bad'" />
                  <div><strong>{{ g.name }}</strong> <span class="muted">{{ g.detail }}</span></div>
                </div>
              </div>
              <div>
                <div class="col-title">{{ t('partners.breakdown') }}</div>
                <div v-for="b in c.breakdown" :key="b.name" v-tooltip="b.detail" class="part">
                  <span class="part-name">{{ b.name }}</span>
                  <div class="part-bar"><div :style="{ width: `${(b.points / b.max) * 100}%` }" /></div>
                  <span class="part-pts">{{ b.points }}/{{ b.max }}</span>
                </div>
              </div>
            </div>

            <div class="cand-foot">
              <span class="autonomy">
                <i :class="status(c) === 'autonomous' ? 'pi pi-bolt' : status(c) === 'approval' ? 'pi pi-user' : 'pi pi-ban'" />
                {{ status(c) === 'autonomous' ? t('partners.autonomous') : status(c) === 'approval' ? t('partners.needsApproval', { threshold }) : t('partners.gateFailed') }}
              </span>
              <Button :label="t('partners.negotiate')" icon="pi pi-comments" :disabled="!c.gatesPassed" :loading="starting === c.partner.orgId"
                :severity="status(c) === 'autonomous' ? undefined : 'secondary'" :data-ap="`negotiate-${c.partner.orgId}`" @click="negotiate(c)" />
            </div>
          </div>
          <Message severity="secondary" icon="pi pi-lock">{{ t('partners.privacy') }}</Message>
        </template>

        <template v-else>
          <Message severity="info" icon="pi pi-info-circle">{{ t('partners.noGap') }}</Message>
          <h2>{{ t('partners.network') }}</h2>
          <div class="grid">
            <div v-for="l in network" :key="l.orgId" class="card">
              <div class="cand-name">{{ flag(l.country) }} {{ l.name }}</div>
              <div class="muted">{{ l.city }} · {{ l.description }}</div>
              <div class="chips">
                <Tag v-for="cert in l.certificates" :key="cert" :value="cert" severity="secondary" icon="pi pi-verified" />
              </div>
              <ul class="products"><li v-for="p in l.products" :key="p">{{ p }}</li></ul>
            </div>
          </div>
        </template>
      </div>

      <div v-if="result" class="card side">
        <h2>{{ t('qual.activity') }}</h2>
        <ActivityFeed :subject="result.tender.id" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 16px; align-items: start; }
.side { width: 340px; position: sticky; top: 16px; max-height: calc(100vh - 32px); overflow: auto; }
@media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } .side { width: auto; } }
.main { display: flex; flex-direction: column; gap: 14px; }
.gap { display: flex; gap: 14px; align-items: center; background: var(--accent-soft); }
.gap > i { font-size: 26px; color: var(--accent); }
.gap-title { font-weight: 600; font-size: 17px; }
.candidate { display: flex; flex-direction: column; gap: 16px; border-left: 6px solid var(--border); }
.candidate.autonomous { border-left-color: var(--good); }
.candidate.approval { border-left-color: var(--warn); }
.candidate.failed { border-left-color: var(--bad); opacity: 0.8; }
.cand-head { display: flex; gap: 18px; align-items: center; }
.ring { --p: 0; width: 76px; height: 76px; border-radius: 50%; flex: none; display: grid; place-items: center;
  background: conic-gradient(var(--accent) calc(var(--p) * 1%), var(--border) 0); }
.ring span { width: 60px; height: 60px; border-radius: 50%; background: var(--surface); display: grid; place-items: center; font-weight: 700; font-size: 17px; }
.autonomous .ring { background: conic-gradient(var(--good) calc(var(--p) * 1%), var(--border) 0); }
.approval .ring { background: conic-gradient(var(--warn) calc(var(--p) * 1%), var(--border) 0); }
.cand-main { display: flex; flex-direction: column; gap: 6px; }
.cand-name { font-weight: 600; font-size: 17px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
@media (max-width: 800px) { .cols { grid-template-columns: 1fr; } }
.col-title { font-weight: 600; margin-bottom: 8px; }
.gate { display: flex; gap: 8px; font-size: 14px; margin-bottom: 6px; }
.gate i { margin-top: 2px; }
.good { color: var(--good); }
.bad { color: var(--bad); }
.part { display: grid; grid-template-columns: 110px 1fr 50px; gap: 10px; align-items: center; font-size: 14px; margin-bottom: 8px; }
.part-bar { height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; }
.part-bar div { height: 100%; background: var(--accent); }
.part-pts { text-align: right; color: var(--muted); }
.cand-foot { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; border-top: 1px solid var(--border); padding-top: 12px; }
.autonomy { display: inline-flex; gap: 8px; align-items: center; font-weight: 500; }
.autonomous .autonomy { color: var(--good); }
.approval .autonomy { color: var(--warn); }
.failed .autonomy { color: var(--bad); }
.products { margin: 8px 0 0; padding-left: 18px; font-size: 14px; }
</style>
