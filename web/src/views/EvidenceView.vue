<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import SelectButton from 'primevue/selectbutton'
import { api } from '@/lib/api'
import { flag } from '@/lib/flags'
import { currentOrg, session } from '@/lib/session'
import type { CompanyDocument, EcertisMatch } from '@/lib/tenders'

interface Obtain { name: string; where: string; url: string; online: boolean; cost: string; time: string }
interface Row { document: CompanyDocument; ecertis: EcertisMatch | null; obtain: Obtain[] | null }
interface Data { translationNote: string | null; instead: { kind: string; obtain: Obtain | null }[] | null; rows: Row[] }

const { t } = useI18n()
const data = ref<Data>()
const buyerCountry = ref('DE')
const open = ref<Record<string, boolean>>({})
const options = computed(() =>
  ['DE', 'PL'].filter((c) => c !== currentOrg()?.country).map((c) => ({ label: `${flag(c)} ${c}`, value: c })),
)

async function load() {
  if (!session.orgId) return
  if (buyerCountry.value === currentOrg()?.country) buyerCountry.value = options.value[0]?.value ?? 'DE'
  data.value = await api<Data>(`/evidence?buyerCountry=${buyerCountry.value}`)
}
watch([() => session.orgId, buyerCountry, () => session.orgs.length], load, { immediate: true })

// Documents a buyer abroad can check come first; the rest are folded away below.
const mapped = computed(() => data.value?.rows.filter((r) => r.ecertis) ?? [])
const other = computed(() => data.value?.rows.filter((r) => !r.ecertis) ?? [])
const showOther = ref(false)
const today = new Date().toISOString().slice(0, 10)
const expired = (d: CompanyDocument) => !!d.validUntil && d.validUntil < today
</script>

<template>
  <div v-if="data" class="page">
    <div class="page-head">
      <div>
        <h1>{{ t('evidence.title') }}</h1>
        <p class="muted">{{ t('evidence.subtitle') }}</p>
      </div>
      <div class="country">
        <span class="label">{{ t('evidence.country') }}</span>
        <SelectButton v-model="buyerCountry" :options="options" option-label="label" option-value="value" :allow-empty="false" />
      </div>
    </div>

    <section v-if="data.instead" class="ink-panel instead">
      <div class="instead-head">
        <span class="stamp" aria-hidden="true"><i class="pi pi-exclamation-triangle" /></span>
        <div>
          <h2>{{ t('evidence.gapTitle') }}</h2>
          <p class="muted">{{ t('evidence.gapShort') }}</p>
        </div>
      </div>
      <ol class="instead-list">
        <li v-for="(i, n) in data.instead" :key="i.kind">
          <span class="n">{{ n + 1 }}</span>
          <div v-if="i.obtain">
            <strong>{{ i.obtain.name }}</strong>
            <div class="muted">{{ i.obtain.where }} · {{ i.obtain.cost }} · {{ i.obtain.time }}</div>
          </div>
          <a v-if="i.obtain" :href="i.obtain.url" target="_blank" rel="noopener" class="get">{{ t('evidence.order') }} <i class="pi pi-external-link" /></a>
        </li>
      </ol>
      <p class="muted small">{{ t('evidence.gapThen') }}</p>
    </section>

    <p v-if="data.translationNote" class="note"><i class="pi pi-language" aria-hidden="true" /> {{ data.translationNote }}</p>

    <section class="docs">
      <h2>{{ t('evidence.yourDocs', { n: mapped.length }) }}</h2>
      <article v-for="r in mapped" :key="r.document.id" class="card doc">
        <div class="cols">
          <div class="col">
            <div class="label">{{ t('evidence.yours') }}</div>
            <div class="doc-title">{{ flag(r.document.country) }} {{ r.document.title }}</div>
            <div class="muted small">
              {{ r.document.issuer }}
              <span v-if="r.document.validUntil" :class="{ bad: expired(r.document) }"> · {{ t('evidence.validUntil', { d: r.document.validUntil }) }}</span>
            </div>
          </div>

          <div class="col">
            <div class="label">{{ t('evidence.accepted', { country: buyerCountry }) }}</div>
            <div class="asked">{{ r.ecertis!.buyerCountry[0]?.name ?? '—' }}</div>
            <button v-if="r.ecertis!.buyerCountry.length > 1" type="button" class="more" :aria-expanded="!!open[r.document.id]"
              @click="open[r.document.id] = !open[r.document.id]">
              {{ open[r.document.id] ? t('evidence.less') : t('evidence.more', { n: r.ecertis!.buyerCountry.length - 1 }) }}
            </button>
          </div>

          <div class="col verdict">
            <div class="label">{{ t('evidence.status') }}</div>
            <div class="ok"><i class="pi pi-check-circle" aria-hidden="true" /> {{ t('evidence.counts') }}</div>
            <a :href="r.ecertis!.url" target="_blank" rel="noopener" class="small">{{ r.ecertis!.criterionName }} <i class="pi pi-external-link" /></a>
          </div>
        </div>

        <ul v-if="open[r.document.id]" class="asked-list">
          <li v-for="e in r.ecertis!.buyerCountry" :key="e.name">
            <a v-if="e.url" :href="e.url" target="_blank" rel="noopener">{{ e.name }}</a><span v-else>{{ e.name }}</span>
            <span v-if="e.issuer" class="muted"> · {{ e.issuer }}</span>
          </li>
        </ul>

        <div v-for="o in r.obtain ?? []" :key="o.name" class="obtain">
          <span class="label">{{ t('evidence.howToGet') }}</span>
          <span><strong>{{ o.name }}</strong> · {{ o.where }}</span>
          <span class="facts"><span>{{ o.online ? t('evidence.online') : t('evidence.offline') }}</span><span>{{ o.cost }}</span><span>{{ o.time }}</span></span>
          <a :href="o.url" target="_blank" rel="noopener" class="get">{{ t('evidence.order') }} <i class="pi pi-external-link" /></a>
        </div>
      </article>
    </section>

    <section v-if="other.length" class="docs">
      <button type="button" class="fold" :aria-expanded="showOther" @click="showOther = !showOther">
        <i :class="showOther ? 'pi pi-chevron-down' : 'pi pi-chevron-right'" aria-hidden="true" />
        {{ t('evidence.otherDocs', { n: other.length }) }}
      </button>
      <p v-if="showOther" class="muted small">{{ t('evidence.noMapping') }}</p>
      <template v-if="showOther">
        <article v-for="r in other" :key="r.document.id" class="card doc quiet">
          <div class="doc-title">{{ flag(r.document.country) }} {{ r.document.title }}</div>
          <div class="muted small">{{ r.document.issuer }}<span v-if="r.document.validUntil"> · {{ t('evidence.validUntil', { d: r.document.validUntil }) }}</span></div>
          <div v-for="o in r.obtain ?? []" :key="o.name" class="obtain">
            <span class="label">{{ t('evidence.howToGet') }}</span>
            <span><strong>{{ o.name }}</strong> · {{ o.where }}</span>
            <span class="facts"><span>{{ o.cost }}</span><span>{{ o.time }}</span></span>
            <a :href="o.url" target="_blank" rel="noopener" class="get">{{ t('evidence.order') }} <i class="pi pi-external-link" /></a>
          </div>
        </article>
      </template>
    </section>
  </div>
</template>

<style scoped>
.country { display: flex; gap: 10px; align-items: center; }
.label { font-size: 13px; font-weight: 500; color: var(--muted); }
.small { font-size: 13px; }
.bad { color: var(--bad); font-weight: 600; }

.instead { display: flex; flex-direction: column; gap: 16px; }
.instead-head { display: flex; gap: 14px; align-items: flex-start; }
.instead-head h2 { color: #fff; font-size: 20px; font-weight: 500; }
.instead-head p { margin: 4px 0 0; max-width: 70ch; }
.stamp { width: 40px; height: 40px; flex: none; display: grid; place-items: center; background: #b07a00; color: #fff; clip-path: var(--chamfer-sm); }
.instead-list { list-style: none; margin: 0; padding: 0; border: 1px dashed var(--ink-line); }
.instead-list li { display: flex; gap: 14px; align-items: center; padding: 14px 16px; border-bottom: 1px dashed var(--ink-line); }
.instead-list li:last-child { border-bottom: 0; }
.instead-list li > div { flex: 1; }
.n { width: 24px; height: 24px; flex: none; display: grid; place-items: center; font-family: var(--mono); font-size: 12px; background: var(--accent); color: #fff; }
.instead .get { color: var(--accent-on-dark); }
.instead .small { margin: 0; }

.note { margin: 0; display: flex; gap: 8px; align-items: center; font-size: 14.5px; padding: 12px 14px; border: 1px dashed var(--dash); background: var(--surface); }
.note i { color: var(--accent); }

.docs { display: flex; flex-direction: column; gap: 12px; }
.doc { display: flex; flex-direction: column; gap: 14px; padding: 18px 20px; }
.cols { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1.2fr) minmax(0, 0.8fr); gap: 20px; }
@media (max-width: 900px) { .cols { grid-template-columns: 1fr; } }
.col { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.doc-title { font-weight: 600; font-size: 15.5px; }
.asked { font-size: 15px; }
.more, .fold { align-self: flex-start; background: none; border: 0; padding: 0; font: inherit; font-size: 13.5px; color: var(--accent); cursor: pointer; }
.fold { font-size: 15px; font-weight: 600; color: var(--text); display: inline-flex; gap: 8px; align-items: center; }
.ok { color: var(--good); font-weight: 600; display: inline-flex; gap: 6px; align-items: center; }
.asked-list { margin: 0; padding: 12px 14px 12px 30px; background: var(--ground); font-size: 14px; display: flex; flex-direction: column; gap: 4px; }
.obtain { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; padding-top: 12px; border-top: 1px solid var(--border); font-size: 14.5px; }
.facts { display: inline-flex; flex-wrap: wrap; gap: 6px; }
.facts span { font-size: 12.5px; padding: 2px 8px; background: var(--ground); border: 1px solid var(--border); }
.get { margin-left: auto; font-weight: 600; font-size: 14px; white-space: nowrap; }
.quiet { gap: 6px; }
</style>
