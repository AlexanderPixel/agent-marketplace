<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import Select from 'primevue/select'
import ToggleSwitch from 'primevue/toggleswitch'
import { api } from '@/lib/api'
import { flag } from '@/lib/flags'
import { session } from '@/lib/session'
import { eur, formatDate, units } from '@/lib/tenders'

interface Spec { key: string; label: string; number: number | null; unit: string | null; text: string | null }
interface Product {
  id: string; name: string; category: string | null; origin: string | null; summary: string | null; cpv: string
  capacity: number; unit: string; unitPriceEur: number; leadTimeDays: number; specs?: number; needs?: number
}
type Status = 'Met' | 'Missing' | 'Unclear' | 'Info'
interface Row { ser: string; section: string; description: string; required: string; key: string | null }
interface RowCheck { row: Row; status: Status; yours: string | null; reason: string }
interface Need { id: string; title: string; buyer: string; country: string; classification: string | null; source: string; note: string | null }
interface NeedMatch { need: Need; verdict: 'met' | 'open' | 'gap'; met: number; checked: number; rows: RowCheck[] }
interface TenderHit {
  id: string; sourceId: string; source: string; title: string; titles: Record<string, string>; buyerName: string; buyerCountry: string
  deadline: string | null; open: boolean; lots: { id: string; title: string; quantity: number | null; unit: string | null }[]
}
interface Detail { product: Omit<Product, 'specs'> & { specs: Spec[] | null }; needs: NeedMatch[]; tenders: TenderHit[] }

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const list = ref<Product[]>([])
const detail = ref<Detail>()
const problemsOnly = ref(true)
const id = computed(() => (route.params.id as string | undefined) ?? null)

async function load() {
  if (!session.orgId) return
  list.value = await api<Product[]>('/products')
  detail.value = id.value ? await api<Detail>(`/products/${encodeURIComponent(id.value)}`) : undefined
}
watch([() => session.orgId, id], load, { immediate: true })

const options = computed(() => list.value.map((p) => ({ label: p.name, value: p.id })))
const pick = (v: string) => router.push(`/products/${encodeURIComponent(v)}`)
const show = (s: Spec) => (s.number != null ? `${s.number.toLocaleString(session.language)} ${s.unit ?? ''}`.trim() + (s.text ? ` · ${s.text}` : '') : s.text ?? '—')
const unitName = (u: string | null) => (u ? (units[u] ?? u) : '')
const icon: Record<Status, string> = { Met: 'pi pi-check-circle', Missing: 'pi pi-times-circle', Unclear: 'pi pi-question-circle', Info: 'pi pi-info-circle' }

/** Rows grouped by the specification's own sections, in its order. */
function sections(m: NeedMatch) {
  const rows = problemsOnly.value ? m.rows.filter((r) => r.status === 'Missing' || r.status === 'Unclear') : m.rows
  const out: { name: string; rows: RowCheck[] }[] = []
  for (const r of rows) {
    const last = out.at(-1)
    if (last?.name === r.row.section) last.rows.push(r)
    else out.push({ name: r.row.section, rows: [r] })
  }
  return out
}
const tenderTitle = (h: TenderHit) => h.titles[session.language] ?? h.title
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ t('products.title') }}</h1>
        <p class="muted">{{ t('products.subtitle') }}</p>
      </div>
      <label v-if="list.length" class="switcher">
        <span class="label">{{ t('products.pick') }}</span>
        <Select :model-value="id" :options="options" option-label="label" option-value="value" :placeholder="t('products.all')" class="pick" @update:model-value="pick" />
      </label>
    </div>

    <!-- Inventory -->
    <div v-if="!id" class="grid inventory">
      <p v-if="!list.length" class="muted">{{ t('products.none') }}</p>
      <RouterLink v-for="p in list" :key="p.id" :to="`/products/${encodeURIComponent(p.id)}`" class="card item">
        <div class="item-top">
          <span class="cat">{{ p.category ? t(`products.cat.${p.category}`, p.category) : p.cpv }}</span>
          <span class="muted small">{{ t('products.origin') }}: {{ flag(p.origin ?? '') }} {{ p.origin }}</span>
        </div>
        <div class="item-name">{{ p.name }}</div>
        <p v-if="p.summary" class="muted small">{{ p.summary }}</p>
        <dl class="mini">
          <div><dt>{{ t('products.capacity') }}</dt><dd>{{ p.capacity.toLocaleString(session.language) }} {{ unitName(p.unit) }}</dd></div>
          <div><dt>{{ t('products.price') }}</dt><dd>{{ eur(p.unitPriceEur, 0) }}</dd></div>
          <div><dt>{{ t('products.demand') }}</dt><dd>{{ p.needs ?? 0 }}</dd></div>
        </dl>
      </RouterLink>
    </div>

    <template v-else-if="detail">
      <RouterLink to="/products" class="back"><i class="pi pi-arrow-left" aria-hidden="true" /> {{ t('products.all') }}</RouterLink>

      <section class="ink-panel hero">
        <div class="hero-head">
          <span class="cat on-dark">{{ detail.product.category ? t(`products.cat.${detail.product.category}`, detail.product.category) : detail.product.cpv }}</span>
          <h2 class="hero-name">{{ detail.product.name }}</h2>
          <p v-if="detail.product.summary" class="muted">{{ detail.product.summary }}</p>
        </div>
        <dl class="figures">
          <div><dt>{{ t('products.capacity') }}</dt><dd>{{ detail.product.capacity.toLocaleString(session.language) }} <small>{{ unitName(detail.product.unit) }}</small></dd></div>
          <div><dt>{{ t('products.price') }}</dt><dd>{{ eur(detail.product.unitPriceEur, 0) }}</dd></div>
          <div><dt>{{ t('products.lead') }}</dt><dd>{{ t('products.days', { n: detail.product.leadTimeDays }) }}</dd></div>
          <div><dt>{{ t('products.origin') }}</dt><dd>{{ detail.product.origin ? `${flag(detail.product.origin)} ${detail.product.origin}` : '—' }}</dd></div>
          <div><dt>CPV</dt><dd class="mono">{{ detail.product.cpv }}</dd></div>
        </dl>
      </section>

      <div class="cols">
        <section class="card specs">
          <h2>{{ t('products.specs') }}</h2>
          <p v-if="!detail.product.specs?.length" class="muted">{{ t('products.noSpecs') }}</p>
          <table v-else class="spec-table">
            <tbody>
              <tr v-for="s in detail.product.specs" :key="s.key"><th scope="row">{{ s.label }}</th><td>{{ show(s) }}</td></tr>
            </tbody>
          </table>
        </section>

        <section class="card tenders">
          <h2>{{ t('products.tenders') }}</h2>
          <p class="muted small">{{ t('products.tendersHint') }}</p>
          <p v-if="!detail.tenders.length" class="muted">{{ t('products.noTenders') }}</p>
          <RouterLink v-for="h in detail.tenders" :key="h.id" :to="`/tenders/${encodeURIComponent(h.id)}`" class="tender">
            <span class="state" :class="{ open: h.open }">{{ h.open ? t('products.open') : t('products.closed') }}</span>
            <span class="tender-title">{{ flag(h.buyerCountry) }} {{ tenderTitle(h) }}</span>
            <span class="muted small">{{ h.buyerName }} · {{ h.source }} {{ h.sourceId }} · {{ formatDate(h.deadline) }}</span>
          </RouterLink>
        </section>
      </div>

      <section class="demand">
        <div class="demand-head">
          <h2>{{ t('products.needs') }}</h2>
          <label class="toggle"><ToggleSwitch v-model="problemsOnly" /> {{ t('products.problemsOnly') }}</label>
        </div>
        <p v-if="!detail.needs.length" class="muted">{{ t('products.noNeeds') }}</p>
        <article v-for="m in detail.needs" :key="m.need.id" class="card need">
          <div class="need-head">
            <div class="need-title">
              <div class="need-name">{{ m.need.title }}</div>
              <div class="muted small">{{ m.need.buyer }} · {{ m.need.source }}</div>
            </div>
            <span v-if="m.need.classification" class="classified">{{ m.need.classification }}</span>
            <span class="verdict" :class="m.verdict">{{ t(`products.verdict.${m.verdict}`) }}</span>
          </div>
          <div class="score">
            <div class="score-bar" role="img" :aria-label="t('products.score', { met: m.met, total: m.checked })">
              <div :style="{ width: `${(m.met / Math.max(1, m.checked)) * 100}%` }" />
            </div>
            <span class="small">{{ t('products.score', { met: m.met, total: m.checked }) }}</span>
          </div>
          <p v-if="m.need.note" class="note small"><i class="pi pi-info-circle" aria-hidden="true" /> {{ m.need.note }}</p>

          <p v-if="problemsOnly && !sections(m).length" class="muted small">{{ t('products.allMet') }}</p>
          <div v-for="sec in sections(m)" :key="sec.name" class="section">
            <div class="section-name">{{ sec.name }}</div>
            <table class="rows">
              <thead><tr><th>#</th><th>{{ t('products.required') }}</th><th>{{ t('products.yours') }}</th><th>{{ t('products.status') }}</th></tr></thead>
              <tbody>
                <tr v-for="r in sec.rows" :key="r.row.ser" :class="r.status">
                  <td class="mono">{{ r.row.ser }}</td>
                  <td><strong>{{ r.row.description }}</strong><div class="muted small">{{ r.row.required }}</div></td>
                  <td>{{ r.yours ?? '—' }}</td>
                  <td class="st"><span class="badge" :class="r.status"><i :class="icon[r.status]" aria-hidden="true" /> {{ t(`products.st.${r.status}`) }}</span><div class="muted small">{{ r.reason }}</div></td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>
      </section>
    </template>
  </div>
</template>

<style scoped>
.label { font-size: 13px; font-weight: 500; color: var(--muted); }
.small { font-size: 13px; }
.switcher { display: flex; flex-direction: column; gap: 4px; }
.pick { min-width: 320px; }
.back { display: inline-flex; gap: 6px; align-items: center; text-decoration: none; font-size: 14.5px; font-weight: 500; }

.inventory .item { display: flex; flex-direction: column; gap: 8px; text-decoration: none; color: var(--text); }
.inventory .item:hover { border-color: var(--accent); }
.item-top { display: flex; justify-content: space-between; align-items: center; }
.item-name { font-size: 17px; font-weight: 600; letter-spacing: -0.015em; }
.item p { margin: 0; flex: 1; }
.cat { font-family: var(--mono); font-size: 11.5px; padding: 3px 8px; background: var(--ground); border: 1px solid var(--border); align-self: flex-start; }
.cat.on-dark { background: transparent; border-color: var(--ink-line); color: var(--ink-text); }
.mini { margin: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-top: 1px solid var(--border); padding-top: 10px; }
.mini dt { font-size: 12px; color: var(--muted); }
.mini dd { margin: 2px 0 0; font-weight: 600; }

.hero { display: flex; flex-direction: column; gap: 22px; }
.hero-head { display: flex; flex-direction: column; gap: 8px; }
.hero-name { color: #fff; font-size: 30px; font-weight: 500; letter-spacing: -0.03em; }
.hero-head p { margin: 0; max-width: 72ch; }
.figures { margin: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); border: 1px dashed var(--ink-line); }
.figures div { padding: 14px 18px; border-right: 1px dashed var(--ink-line); }
.figures div:last-child { border-right: 0; }
.figures dt { font-size: 13px; color: var(--ink-muted); }
.figures dd { margin: 6px 0 0; font-size: 24px; font-weight: 500; letter-spacing: -0.02em; white-space: nowrap; }
.figures small { font-size: 14px; color: var(--ink-muted); }

.cols { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr); gap: 16px; align-items: start; }
@media (max-width: 1000px) { .cols { grid-template-columns: 1fr; } }
.specs, .tenders { display: flex; flex-direction: column; gap: 10px; }
.spec-table { width: 100%; border-collapse: collapse; font-size: 14.5px; }
.spec-table th { text-align: left; font-weight: 500; color: var(--muted); padding: 9px 12px 9px 0; border-bottom: 1px solid var(--border); width: 46%; }
.spec-table td { padding: 9px 0; border-bottom: 1px solid var(--border); font-variant-numeric: tabular-nums; }
.tenders p { margin: 0; }
.tender { display: flex; flex-direction: column; gap: 3px; padding: 10px 0; border-bottom: 1px solid var(--border); text-decoration: none; color: var(--text); }
.tender:hover .tender-title { color: var(--accent); }
.tender-title { font-weight: 600; font-size: 14.5px; }
.state { align-self: flex-start; font-size: 11.5px; font-weight: 600; padding: 1px 7px; background: var(--ground); color: var(--muted); border: 1px solid var(--border); }
.state.open { background: var(--good-soft); color: var(--good); border-color: transparent; }

.demand { display: flex; flex-direction: column; gap: 12px; }
.demand-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.toggle { display: inline-flex; gap: 8px; align-items: center; font-size: 14px; cursor: pointer; }
.need { display: flex; flex-direction: column; gap: 14px; }
.need-head { display: flex; gap: 12px; align-items: flex-start; flex-wrap: wrap; }
.need-title { flex: 1 1 360px; }
.need-name { font-size: 18px; font-weight: 600; letter-spacing: -0.015em; }
.classified { font-family: var(--mono); font-size: 11.5px; padding: 3px 8px; border: 1px solid var(--bad); color: var(--bad); }
.verdict { font-size: 13px; font-weight: 600; padding: 4px 10px; }
.verdict.met { background: var(--good-soft); color: var(--good); }
.verdict.open { background: var(--warn-soft); color: var(--warn); }
.verdict.gap { background: var(--bad-soft); color: var(--bad); }
.score { display: flex; gap: 12px; align-items: center; }
.score-bar { flex: 1; height: 8px; background: var(--ground); border: 1px solid var(--border); }
.score-bar div { height: 100%; background: var(--accent); }
.note { margin: 0; display: flex; gap: 8px; color: var(--muted); }
.section-name { font-weight: 600; font-size: 14px; margin: 4px 0 6px; }
.rows { width: 100%; border-collapse: collapse; font-size: 14px; }
.rows th { text-align: left; font-size: 12.5px; font-weight: 600; color: var(--muted); padding: 8px 10px; background: var(--ground); border-bottom: 1px solid var(--border); }
.rows td { padding: 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
.rows td:first-child { width: 44px; }
.st { width: 34%; }
.badge { display: inline-flex; gap: 6px; align-items: center; font-weight: 600; font-size: 13px; }
.badge.Met { color: var(--good); }
.badge.Missing { color: var(--bad); }
.badge.Unclear { color: var(--warn); }
.badge.Info { color: var(--muted); }
@media (max-width: 800px) { .rows { display: block; overflow-x: auto; } .pick { min-width: 0; width: 100%; } }
</style>
