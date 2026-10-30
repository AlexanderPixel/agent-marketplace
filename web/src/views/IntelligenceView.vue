<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Tag from 'primevue/tag'
import { api } from '@/lib/api'
import { flag } from '@/lib/flags'
import { session } from '@/lib/session'
import { eur, formatDate } from '@/lib/tenders'

interface Award {
  publicationNumber: string; publishedAt: string; buyer: string; buyerCountry: string; winners: string[]; prime: string
  cpv: string; value: number | null; titles: Record<string, string>; platform: string | null; framework: boolean; url: string
}
interface Intel {
  totals: { awards: number; awardValue: number; withValue: number; safeTotal: number; notices: number }
  safe: { country: string; bn: number }[]
  monthly: { month: string; ted: number; bzp: number }[]
  primes: { prime: string; awards: number; value: number; withValue: number; entities: { name: string; awards: number }[] }[]
  platforms: { platform: string; awards: number; primes: string[] }[]
  buyers: { buyer: string; country: string; awards: number }[]
  frameworks: Award[]
  recent: Award[]
  opportunities: { prime: string; awards: number; value: number; platforms: string[]; latest: string; notices: string[] }[]
}

const { t } = useI18n()
const data = ref<Intel>()
watch(() => session.orgId, async () => (data.value = session.orgId ? await api<Intel>('/intelligence') : undefined), { immediate: true })

const maxSafe = computed(() => Math.max(...(data.value?.safe.map((s) => s.bn) ?? [1])))
const maxMonth = computed(() => Math.max(...(data.value?.monthly.map((m) => m.ted + m.bzp) ?? [1])))
const title = (a: Award) => a.titles[session.language] ?? a.titles.en ?? Object.values(a.titles)[0]
const bn = (n: number) => `${(n / 1e9).toLocaleString(session.language, { maximumFractionDigits: 2 })} bn €`
const month = (m: string) => new Date(`${m}-01`).toLocaleString(session.language, { month: 'short' })
</script>

<template>
  <div v-if="data" class="page">
    <div class="page-head">
      <div>
        <h1>{{ t('intel.title') }}</h1>
        <p class="muted">{{ t('intel.subtitle') }}</p>
      </div>
    </div>

    <div class="kpis">
      <div class="card kpi"><span class="muted">{{ t('intel.safe') }}</span><strong>{{ data.totals.safeTotal.toLocaleString(session.language) }} bn €</strong><span class="muted small">{{ data.safe.length }} {{ t('tenders.country').toLowerCase() }}</span></div>
      <div class="card kpi"><span class="muted">{{ t('intel.notices') }}</span><strong>{{ data.totals.notices }}</strong><span class="muted small">TED + BZP 2026</span></div>
    </div>

    <div v-if="data.opportunities.length" class="card leads">
      <h2><i class="pi pi-bolt" /> {{ t('intel.leads') }}</h2>
      <p class="muted small">{{ t('intel.leadsHint') }}</p>
      <div class="lead-grid">
        <div v-for="o in data.opportunities" :key="o.prime" class="lead">
          <div class="lead-name">{{ o.prime }}</div>
          <div class="muted small">{{ t('intel.leadAwards', { n: o.awards }) }}<span v-if="o.value"> · {{ eur(o.value, 0) }}</span> · {{ formatDate(o.latest) }}</div>
          <div class="chips"><Tag v-for="p in o.platforms" :key="p" :value="p" severity="secondary" /></div>
          <div class="cites">
            <a v-for="n in o.notices" :key="n" :href="`https://ted.europa.eu/en/notice/-/detail/${n}`" target="_blank" rel="noopener"><i class="pi pi-bookmark" /> TED {{ n }}</a>
          </div>
        </div>
      </div>
    </div>

    <div class="two">
      <div class="card">
        <h2>{{ t('intel.safeTitle') }}</h2>
        <div class="bars">
          <div v-for="s in data.safe" :key="s.country" class="bar-row" :class="{ me: s.country === 'PL' || s.country === 'CZ' }">
            <span class="bar-label">{{ flag(s.country) }} {{ s.country }}</span>
            <div class="bar"><div :style="{ width: `${(s.bn / maxSafe) * 100}%` }" /></div>
            <span class="bar-val">{{ s.bn.toLocaleString(session.language) }}</span>
          </div>
        </div>
        <p class="muted small">{{ t('intel.safeNote') }}</p>
      </div>

      <div class="col">
        <div class="card">
          <h2>{{ t('intel.monthly') }}</h2>
          <div class="months">
            <div v-for="m in data.monthly" :key="m.month" class="month">
              <div class="stack" :style="{ height: `${((m.ted + m.bzp) / maxMonth) * 140}px` }">
                <div class="ted" :style="{ flex: m.ted }" /><div class="bzp" :style="{ flex: m.bzp }" />
              </div>
              <span class="small">{{ month(m.month) }}</span>
            </div>
          </div>
          <div class="legend small"><span class="dot ted" /> TED <span class="dot bzp" /> e-Zamówienia</div>
        </div>
        <div class="card">
          <h2>{{ t('intel.platforms') }}</h2>
          <div class="chips">
            <Tag v-for="p in data.platforms" :key="p.platform" :value="`${p.platform} · ${p.awards}`" :severity="p.primes.includes('Rheinmetall') ? 'info' : 'secondary'" />
          </div>
        </div>
      </div>
    </div>

    <div class="two">
      <div class="card">
        <h2>{{ t('intel.primes') }}</h2>
        <div v-for="p in data.primes" :key="p.prime" class="prime">
          <div class="prime-head"><strong>{{ p.prime }}</strong><span class="muted">{{ p.awards }} · {{ bn(p.value) }}</span></div>
          <div v-for="e in p.entities" :key="e.name" class="entity"><span>{{ e.name }}</span><span class="muted">{{ e.awards }}</span></div>
        </div>
      </div>
      <div class="card">
        <h2>{{ t('intel.frameworks') }}</h2>
        <div v-for="a in data.frameworks" :key="a.publicationNumber" class="award">
          <a :href="a.url" target="_blank" rel="noopener">{{ title(a) }}</a>
          <div class="muted small">{{ flag(a.buyerCountry) }} {{ a.buyer }} → {{ a.winners[0] }} · {{ a.value ? eur(a.value, 0) : t('intel.noValue') }} · {{ formatDate(a.publishedAt) }}</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>{{ t('intel.recent') }}</h2>
      <div v-for="a in data.recent" :key="a.publicationNumber" class="award">
        <div class="award-top">
          <a :href="a.url" target="_blank" rel="noopener">{{ title(a) }}</a>
          <Tag v-if="a.platform" :value="a.platform" severity="secondary" />
        </div>
        <div class="muted small">{{ flag(a.buyerCountry) }} {{ a.buyer }} → <strong>{{ [...new Set(a.winners)].join(', ') }}</strong> · {{ a.value ? eur(a.value, 0) : t('intel.noValue') }} · {{ formatDate(a.publishedAt) }} · TED {{ a.publicationNumber }}</div>
      </div>
      <p class="muted small">{{ t('intel.source') }}</p>
    </div>
  </div>
</template>

<style scoped>
.small { font-size: 12.5px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
.kpi { display: flex; flex-direction: column; gap: 4px; padding: 16px 18px; }
.kpi strong { font-size: 26px; }
.leads { background: linear-gradient(90deg, var(--accent-soft), var(--surface)); }
.leads h2 { display: flex; gap: 8px; align-items: center; }
.leads h2 i { color: var(--eu-gold); }
.lead-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin-top: 10px; }
.lead { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; }
.lead-name { font-weight: 600; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.cites { display: flex; gap: 10px; flex-wrap: wrap; font-size: 12px; }
.cites a { text-decoration: none; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
@media (max-width: 1000px) { .two { grid-template-columns: 1fr; } }
.col { display: flex; flex-direction: column; gap: 16px; }
.bars { display: flex; flex-direction: column; gap: 5px; margin: 12px 0; }
.bar-row { display: grid; grid-template-columns: 64px 1fr 44px; gap: 8px; align-items: center; font-size: 13px; }
.bar { height: 12px; background: var(--ground); border-radius: 4px; overflow: hidden; }
.bar div { height: 100%; background: #8ea3cf; }
.bar-row.me .bar div { background: var(--accent); }
.bar-row.me { font-weight: 600; }
.bar-val { text-align: right; }
.months { display: flex; gap: 8px; align-items: flex-end; height: 170px; margin-top: 10px; }
.month { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 4px; }
.stack { width: 100%; display: flex; flex-direction: column; border-radius: 4px 4px 0 0; overflow: hidden; }
.ted { background: var(--accent); }
.bzp { background: #c94b4b; }
.legend { display: flex; gap: 6px; align-items: center; margin-top: 8px; }
.dot { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.prime { padding: 10px 0; border-bottom: 1px solid var(--border); }
.prime:last-child { border-bottom: 0; }
.prime-head { display: flex; justify-content: space-between; margin-bottom: 6px; }
.entity { display: flex; justify-content: space-between; font-size: 13.5px; padding: 2px 0 2px 12px; }
.award { padding: 10px 0; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 3px; }
.award-top { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
.award a { text-decoration: none; font-weight: 500; }
</style>
