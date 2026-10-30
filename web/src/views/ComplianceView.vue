<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { marked } from 'marked'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import InputNumber from 'primevue/inputnumber'
import Select from 'primevue/select'
import { api, post } from '@/lib/api'
import { flag } from '@/lib/flags'
import { countryCodes, countryName } from '@/lib/countries'
import { session } from '@/lib/session'
import { eur } from '@/lib/tenders'
import SupplierFix from '@/components/SupplierFix.vue'

interface Alt { supplier: string; country: string; costEur: number; note: string }
interface ChangeDoc { kind: 'order' | 'termination' | 'memo'; title: string; recipient: string; body: string }
interface Change {
  id: string; fromSupplier: string; fromCountry: string; fromCostEur: number; to: Alt; euBefore: number; euAfter: number
  documents: ChangeDoc[]; stage: 'awaiting' | 'signed'; createdAt: string; decidedBy?: string; decidedAt?: string; telegramMessageId?: number
}
interface Source { document: string; location: string; quote: string }
interface Part { id: string; name: string; supplier: string; country: string; costEur: number; alternatives?: Alt[]; change?: Change | null; source?: Source | null }
interface Imported {
  name: string; supplier: string | null; country: string | null; costEur: number | null; location: string; quote: string
  alternatives: { supplier: string; country: string | null; costEur: number | null; note: string }[]; verified: boolean; complete: boolean; sanctioned: boolean
}
interface SpecImport { document: string; product: string | null; method: 'ai' | 'table'; components: Imported[]; warnings: string[] }
interface Swap { componentId: string; component: string; from: string; fromCountry: string; to: Alt; costDelta: number; newEuContent: number; reachesThreshold: boolean }
interface Report {
  threshold: number; eligibleCountries: string[]; product: string | null; euContent: number; safeEligible: boolean; contentOk: boolean; controlOk: boolean
  components: { component: Part; eligible: boolean; share: number }[]
  swaps: Swap[]
  control: { company: string; country: string; role: string; chain: { entity: string; country: string; share: number; role: string }[]; euControlled: boolean; detail: string }[]
  sanctions: { name: string; country: string; hit: boolean; detail: string }[]
  telegram: boolean; sanctionsOk: boolean
}

const { t } = useI18n()
const report = ref<Report>()
const busy = ref<string>()
const flash = ref(false)

const dialog = ref(false)
const editingId = ref<string>()
const form = reactive({ name: '', supplier: '', country: 'PL', costEur: null as number | null })
const saving = ref(false)
const error = ref<string>()
const productDraft = ref('')
const editingProduct = ref(false)

const eligible = computed(() => new Set(report.value?.eligibleCountries ?? []))
const countryGroups = computed(() => {
  const items = countryCodes
    .map((c) => ({ value: c, name: countryName(c), label: `${flag(c)} ${countryName(c)}` }))
    .sort((a, b) => a.name.localeCompare(b.name))
  return [
    { label: t('compliance.groupEu'), items: items.filter((i) => eligible.value.has(i.value)) },
    { label: t('compliance.groupOther'), items: items.filter((i) => !eligible.value.has(i.value)) },
  ]
})
const formValid = computed(() => form.name.trim() && form.supplier.trim() && form.country && (form.costEur ?? 0) > 0)

const round1 = (n: number) => Math.round(n * 10) / 10
const totalCost = computed(() => report.value?.components.reduce((a, c) => a + c.component.costEur, 0) ?? 0)
const euCost = computed(() => report.value?.components.filter((c) => c.eligible).reduce((a, c) => a + c.component.costEur, 0) ?? 0)
const gap = computed(() => (report.value ? round1(Math.max(0, report.value.threshold - report.value.euContent)) : 0))

// The cheapest single change that clears the threshold.
const recommended = computed(() => [...(report.value?.swaps ?? [])]
  .filter((s) => s.reachesThreshold).sort((a, b) => a.costDelta - b.costDelta)[0])

/** The parts that do not count towards EU content, biggest first, each with its best replacement. */
const listed = computed(() => new Set((report.value?.sanctions ?? []).filter((s) => s.hit).map((s) => s.name)))
const offenders = computed(() => (report.value?.components ?? [])
  .filter((c) => !c.eligible)
  .sort((a, b) => b.component.costEur - a.component.costEur)
  .map((c) => {
    const options = (report.value?.swaps ?? []).filter((s) => s.componentId === c.component.id)
      .sort((a, b) => Number(b.reachesThreshold) - Number(a.reachesThreshold) || a.costDelta - b.costDelta)
    return { ...c, option: options[0], sanctioned: listed.value.has(c.component.supplier) }
  }))
/** Parts bought from a sanctioned party: they must go, whatever the EU content. */
const sanctionedParts = computed(() => offenders.value.filter((o) => o.sanctioned))

const signedChanges = computed(() => (report.value?.components ?? []).map((c) => c.component).filter((c) => c.change?.stage === 'signed'))

const sanctionsSorted = computed(() => [...(report.value?.sanctions ?? [])].sort((a, b) => Number(b.hit) - Number(a.hit)))
const sanctionsOk = computed(() => report.value?.sanctionsOk ?? true)

const checks = computed(() => {
  const r = report.value
  if (!r) return []
  const hits = r.sanctions.filter((s) => s.hit).length
  return [
    { key: 'content', ok: r.contentOk, title: t('compliance.check1', { t: r.threshold }),
      detail: r.contentOk ? t('compliance.check1Ok', { n: r.euContent }) : t('compliance.check1Bad', { n: r.euContent, gap: gap.value, parts: offenders.value.length }) },
    { key: 'control', ok: r.controlOk, title: t('compliance.check2'),
      detail: r.controlOk ? t('compliance.check2Ok', { n: r.control.length }) : t('compliance.controlBad') },
    { key: 'sanctions', ok: sanctionsOk.value, title: t('compliance.check3'),
      detail: hits ? t('compliance.check3Bad', { n: hits }) : t('compliance.noHits', { n: r.sanctions.length }) },
  ]
})

// Failing checks open by themselves; a person can open or close any of them.
const open = reactive(new Set<string>())
let opened = false
function toggle(key: string) {
  if (open.has(key)) open.delete(key)
  else open.add(key)
}

function roleLabel(role: string) {
  const key = `compliance.role.${role}`
  const label = t(key)
  return label === key ? role : label
}

async function load() {
  if (!session.orgId) return
  report.value = await api<Report>('/compliance')
  if (!opened) {
    checks.value.filter((c) => !c.ok).forEach((c) => open.add(c.key))
    opened = true
  }
}
watch(() => session.orgId, () => { opened = false; open.clear(); load() }, { immediate: true })

function openPart(c?: Part) {
  editingId.value = c?.id
  Object.assign(form, c ? { name: c.name, supplier: c.supplier, country: c.country, costEur: c.costEur } : { name: '', supplier: '', country: 'PL', costEur: null })
  error.value = undefined
  dialog.value = true
}

async function savePart() {
  saving.value = true
  error.value = undefined
  try {
    const body = JSON.stringify(form)
    if (editingId.value) await api(`/compliance/components/${editingId.value}`, { method: 'PUT', body })
    else await api('/compliance/components', { method: 'POST', body })
    dialog.value = false
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    saving.value = false
  }
}

async function removePart(c: Part) {
  if (!confirm(t('compliance.confirmRemove', { name: c.name }))) return
  await api(`/compliance/components/${c.id}`, { method: 'DELETE' })
  await load()
}

function editProduct() {
  productDraft.value = report.value?.product ?? ''
  editingProduct.value = true
}

async function saveProduct() {
  if (!productDraft.value.trim()) return
  await api('/compliance/product', { method: 'PUT', body: JSON.stringify({ product: productDraft.value }) })
  editingProduct.value = false
  await load()
}

// Documents of one change, shown in a dialog.
const docs = ref<{ part: Part; change: Change; index: number }>()
const html = (md: string) => marked.parse(md, { async: false }) as string
function showDocs(part: Part, index = 0) {
  if (part.change) docs.value = { part, change: part.change, index }
}
function download(d: ChangeDoc) {
  const url = URL.createObjectURL(new Blob([d.body], { type: 'text/markdown' }))
  const a = Object.assign(document.createElement('a'), { href: url, download: `${d.title}.md` })
  a.click()
  URL.revokeObjectURL(url)
}

/** Drafts the order, termination and reason, and asks a person to sign. The BOM does not change yet. */
async function prepare(s: Swap) {
  busy.value = s.componentId
  try {
    await post('/compliance/changes', { componentId: s.componentId, supplier: s.to.supplier })
    await load()
    const part = report.value?.components.find((c) => c.component.id === s.componentId)?.component
    if (part) showDocs(part)
  } finally {
    busy.value = undefined
  }
}

async function decide(part: Part, sign: boolean) {
  if (!part.change) return
  busy.value = part.id
  try {
    await post(`/compliance/changes/${part.change.id}/decide`, { sign })
    docs.value = undefined
    await load()
    if (sign) {
      flash.value = true
      setTimeout(() => (flash.value = false), 1200)
    }
  } finally {
    busy.value = undefined
  }
}

// The spec agent: upload a specification, review what it read, then replace the BOM.
const DEMO_SPEC = '/demo/ST-RES-442-04-rev-C.md'
const importOpen = ref(false)
const importing = ref(false)
const importError = ref<string>()
const spec = ref<SpecImport>()
const specText = ref('')
function openImport() {
  spec.value = undefined
  importError.value = undefined
  importOpen.value = true
}
async function readSpec(document: string, text: string) {
  importing.value = true
  importError.value = undefined
  try {
    specText.value = text
    spec.value = await post<SpecImport>('/compliance/import/preview', { document, text })
  } catch (e) {
    importError.value = (e as Error).message
  } finally {
    importing.value = false
  }
}
async function onFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (file) await readSpec(file.name, await file.text())
}
async function useDemoSpec() {
  const text = await (await fetch(DEMO_SPEC)).text()
  await readSpec(DEMO_SPEC.split('/').pop()!, text)
}
const importable = computed(() => spec.value?.components.filter((c) => c.complete) ?? [])
async function applyImport() {
  if (!spec.value || !confirm(t('compliance.importConfirm', { n: importable.value.length }))) return
  importing.value = true
  try {
    await post('/compliance/import/apply', { document: spec.value.document, product: spec.value.product, components: importable.value })
    importOpen.value = false
    opened = false
    open.clear()
    await load()
  } catch (e) {
    importError.value = (e as Error).message
  } finally {
    importing.value = false
  }
}

// After a signed change the replaced supplier is kept as a "Previous supplier" alternative; undo swaps back to it.
const PREVIOUS = 'Previous supplier'
function previousOf(c: Part) {
  const prev = c.alternatives?.find((a) => a.note === PREVIOUS)
  return prev && eligible.value.has(c.country) && !eligible.value.has(prev.country) ? prev : undefined
}

async function undo(c: Part) {
  const prev = previousOf(c)
  if (!prev || !confirm(t('compliance.confirmUndo', { component: c.name, supplier: prev.supplier }))) return
  busy.value = c.id
  try {
    await post('/compliance/swap', { componentId: c.id, supplier: prev.supplier })
    await load()
  } finally {
    busy.value = undefined
  }
}
</script>

<template>
  <div v-if="report" class="page">
    <div class="page-head">
      <div>
        <h1>{{ t('compliance.title') }}</h1>
        <p class="muted">{{ t('compliance.intro') }}</p>
      </div>
    </div>

    <div v-if="!report.product" class="card">
      <h2>{{ t('compliance.createTitle') }}</h2>
      <p class="muted">{{ t('compliance.createHint') }}</p>
      <div class="inline-form">
        <InputText v-model="productDraft" :placeholder="t('compliance.productPlaceholder')" @keyup.enter="saveProduct" />
        <Button :label="t('compliance.create')" icon="pi pi-plus" :disabled="!productDraft.trim()" @click="saveProduct" />
        <Button :label="t('compliance.importSpec')" icon="pi pi-sparkles" outlined @click="openImport" />
      </div>
    </div>

    <template v-else>
      <div class="card verdict" :class="{ ok: report.safeEligible, flash }">
        <div class="verdict-head">
          <i :class="report.safeEligible ? 'pi pi-check-circle good' : 'pi pi-exclamation-circle bad'" />
          <div>
            <div class="verdict-title">{{ report.safeEligible ? t('compliance.eligible') : t('compliance.notEligible') }}</div>
            <div v-if="!editingProduct" class="muted product">
              {{ t('compliance.productLabel') }}: {{ report.product }}
              <Button icon="pi pi-pencil" text rounded size="small" :aria-label="t('compliance.editProduct')" @click="editProduct" />
            </div>
            <div v-else class="inline-form">
              <InputText v-model="productDraft" @keyup.enter="saveProduct" />
              <Button icon="pi pi-check" size="small" :aria-label="t('compliance.save')" @click="saveProduct" />
              <Button icon="pi pi-times" size="small" text :aria-label="t('compliance.cancel')" @click="editingProduct = false" />
            </div>
          </div>
        </div>

        <ol class="checks">
          <li v-for="(c, i) in checks" :key="c.key" :class="[c.ok ? 'pass' : 'fail', { open: open.has(c.key) }]">
            <button type="button" class="check-row" :aria-expanded="open.has(c.key)" @click="toggle(c.key)">
              <span class="num-badge">{{ i + 1 }}</span>
              <span class="check-body">
                <span class="check-title">{{ c.title }}</span>
                <span class="muted small">{{ c.detail }}</span>
              </span>
              <span class="status"><i :class="c.ok ? 'pi pi-check' : 'pi pi-times'" /> {{ c.ok ? t('compliance.pass') : t('compliance.fail') }}</span>
              <i class="pi pi-chevron-down chev" />
            </button>

            <!-- 1. EU content: which parts do not count, and what to change -->
            <div v-if="c.key === 'content' && open.has('content')" class="check-detail">
              <div class="split">
                <div class="split-bar">
                  <div class="eu" :style="{ width: `${report.euContent}%` }" />
                  <div class="mark" :style="{ left: `${report.threshold}%` }"><span>{{ t('compliance.required', { n: report.threshold }) }}</span></div>
                </div>
                <div class="split-legend">
                  <span><i class="dot eu" /> {{ t('compliance.countsShort') }}: <strong>{{ report.euContent }}%</strong> ({{ eur(euCost) }})</span>
                  <span><i class="dot non" /> {{ t('compliance.notCountsShort') }}: <strong>{{ round1(100 - report.euContent) }}%</strong> ({{ eur(totalCost - euCost) }})</span>
                </div>
              </div>

              <template v-if="offenders.length">
                <h3>{{ report.contentOk ? t('compliance.offendersOk') : t('compliance.offenders', { n: offenders.length }) }}</h3>
                <p v-if="!report.contentOk" class="muted small">{{ t('compliance.offendersHint', { gap }) }}</p>
                <div v-for="o in offenders" :key="o.component.id" class="offender" :class="{ best: o.option && o.option === recommended && !o.component.change }">
                  <div class="off-what">
                    <div class="off-name">{{ o.component.name }} <Tag v-if="o.sanctioned" :value="t('compliance.sanctionedTag')" severity="danger" icon="pi pi-ban" class="mini" /></div>
                    <div class="muted small">{{ flag(o.component.country) }} {{ o.component.supplier }} · {{ countryName(o.component.country) }} · {{ eur(o.component.costEur) }} ({{ o.share }}%)</div>
                  </div>

                  <SupplierFix :change="o.component.change" :option="o.option" :recommended="o.option === recommended" :sanctioned="o.sanctioned"
                    :eu-content="report.euContent" :telegram="report.telegram" :busy="busy === o.component.id"
                    @prepare="prepare(o.option!)" @sign="decide(o.component, true)" @decline="decide(o.component, false)" @docs="showDocs(o.component)" />
                </div>
                <p class="what-apply small"><i class="pi pi-info-circle" /> {{ t('compliance.prepareExplain') }}</p>
              </template>

              <template v-if="signedChanges.length">
                <h3>{{ t('compliance.signedTitle') }}</h3>
                <div v-for="p in signedChanges" :key="p.id" class="offender signed">
                  <div class="off-what">
                    <div class="off-name">{{ p.name }}</div>
                    <div class="muted small">{{ flag(p.change!.fromCountry) }} {{ p.change!.fromSupplier }} → {{ flag(p.country) }} {{ p.supplier }}</div>
                  </div>
                  <div class="off-fix">
                    <div class="small">{{ t('compliance.signedBy', { who: p.change!.decidedBy }) }}</div>
                    <div class="muted small">{{ t('compliance.sendYourself') }}</div>
                    <div class="row-actions">
                      <Button :label="t('compliance.viewDocs', { n: p.change!.documents.length })" icon="pi pi-file" size="small" outlined @click="showDocs(p)" />
                      <Button :label="t('compliance.undo')" icon="pi pi-undo" size="small" text :loading="busy === p.id" @click="undo(p)" />
                    </div>
                  </div>
                </div>
              </template>

              <a href="#bom" class="small">{{ t('compliance.fullBom') }} ↓</a>
            </div>

            <!-- 2. Control: the ownership chains -->
            <div v-if="c.key === 'control' && open.has('control')" class="check-detail">
              <p class="muted small">{{ t('compliance.controlNote') }}</p>
              <div v-for="o in report.control" :key="o.company" class="owner">
                <div class="owner-head">
                  <strong>{{ flag(o.country) }} {{ o.company }}</strong>
                  <Tag :value="o.role === 'you' ? t('compliance.you') : t('compliance.partner')" severity="secondary" />
                  <span :class="o.euControlled ? 'good' : 'bad'"><i :class="o.euControlled ? 'pi pi-check' : 'pi pi-times'" /> {{ o.euControlled ? t('compliance.controlled') : t('compliance.foreign') }}</span>
                </div>
                <ol class="chain">
                  <li v-for="(l, li) in o.chain" :key="l.entity" :class="{ bad: l.share > 50 && !o.euControlled && l.role === 'ultimate' }">
                    <span class="role">{{ roleLabel(l.role) }}</span>
                    <span>
                      {{ flag(l.country) }} {{ l.entity }}
                      <span v-if="li > 0" class="muted">· {{ t('compliance.ownsOf', { n: l.share, of: o.chain[li - 1].entity }) }}
                        <template v-if="l.share > 50 && l.share < 100">({{ t('compliance.controlling') }})</template></span>
                    </span>
                  </li>
                </ol>
                <div v-if="!o.euControlled" class="bad small">{{ o.detail }}</div>
              </div>
            </div>

            <!-- 3. Sanctions: who was screened -->
            <div v-if="c.key === 'sanctions' && open.has('sanctions')" class="check-detail">
              <div v-for="o in sanctionedParts" :key="o.component.id" class="listed">
                <div class="listed-head"><i class="pi pi-ban" /> {{ t('compliance.cannotTrade', { name: o.component.supplier }) }}</div>
                <p class="small">{{ t('compliance.cannotTradeWhy') }}</p>
                <div class="offender">
                  <div class="off-what">
                    <div class="off-name">{{ o.component.name }}</div>
                    <div class="muted small">{{ flag(o.component.country) }} {{ o.component.supplier }} · {{ eur(o.component.costEur) }} / {{ t('compliance.perSet') }}</div>
                  </div>
                  <SupplierFix :change="o.component.change" :option="o.option" sanctioned
                    :eu-content="report.euContent" :telegram="report.telegram" :busy="busy === o.component.id"
                    @prepare="prepare(o.option!)" @sign="decide(o.component, true)" @decline="decide(o.component, false)" @docs="showDocs(o.component)" />
                </div>
              </div>
              <p class="muted small">{{ t('compliance.sanctionsNote') }}</p>
              <ul class="screen">
                <li v-for="s in sanctionsSorted" :key="s.name" :class="{ hit: s.hit }">
                  <i :class="s.hit ? 'pi pi-times bad' : 'pi pi-check good'" /> {{ flag(s.country) }} {{ s.name }}
                  <span v-if="s.hit" class="bad small">— {{ t('compliance.sanctionedTag') }}</span>
                </li>
              </ul>
            </div>
          </li>
        </ol>
      </div>

      <div id="bom" class="card">
        <div class="card-head">
          <h2>{{ t('compliance.bom') }}</h2>
          <div class="head-actions">
            <Button :label="t('compliance.importSpec')" icon="pi pi-sparkles" size="small" @click="openImport" />
            <Button :label="t('compliance.addPart')" icon="pi pi-plus" size="small" outlined @click="openPart()" />
          </div>
        </div>
        <p class="muted small">{{ t('compliance.originNote', { t: report.threshold }) }}</p>
        <table class="bom">
          <thead><tr><th>{{ t('compliance.component') }}</th><th>{{ t('compliance.supplier') }}</th><th>{{ t('compliance.origin') }}</th><th class="num">{{ t('compliance.cost') }}</th><th class="num">{{ t('compliance.share') }}</th><th>{{ t('compliance.counts') }}</th><th /></tr></thead>
          <tbody>
            <tr v-if="!report.components.length"><td colspan="7" class="muted">{{ t('compliance.noParts') }}</td></tr>
            <tr v-for="c in report.components" :key="c.component.id" :class="{ non: !c.eligible }">
              <td>
                {{ c.component.name }}
                <div v-if="c.component.source" class="source small" :title="c.component.source.quote">
                  <i class="pi pi-file" /> {{ c.component.source.document }} · {{ c.component.source.location }}
                </div>
              </td>
              <td>
                {{ c.component.supplier }}
                <div v-if="previousOf(c.component)" class="was small">{{ t('compliance.was', { supplier: `${flag(previousOf(c.component)!.country)} ${previousOf(c.component)!.supplier}` }) }}</div>
                <div v-if="c.component.change?.stage === 'awaiting'" class="small warn-text"><i class="pi pi-clock" /> {{ t('compliance.awaitingSign') }}</div>
              </td>
              <td class="nowrap">{{ flag(c.component.country) }} {{ countryName(c.component.country) }}</td>
              <td class="num">{{ eur(c.component.costEur) }}</td>
              <td class="num">{{ c.share }}%</td>
              <td class="nowrap"><span :class="c.eligible ? 'good' : 'bad'"><i :class="c.eligible ? 'pi pi-check' : 'pi pi-times'" /> {{ c.eligible ? t('compliance.yes') : t('compliance.no') }}</span></td>
              <td class="actions">
                <Button icon="pi pi-pencil" text rounded size="small" :aria-label="t('compliance.edit')" @click="openPart(c.component)" />
                <Button icon="pi pi-trash" text rounded size="small" severity="danger" :aria-label="t('compliance.remove')" @click="removePart(c.component)" />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <Dialog :visible="!!docs" modal :header="docs ? t('compliance.docsTitle', { component: docs.part.name }) : ''" :style="{ width: '820px', maxWidth: '96vw' }"
      @update:visible="(v: boolean) => { if (!v) docs = undefined }">
      <template v-if="docs">
        <div class="doc-tabs">
          <button v-for="(d, i) in docs.change.documents" :key="d.kind" type="button" class="doc-tab" :class="{ active: i === docs.index }" @click="docs.index = i">
            <span class="doc-kind">{{ t(`compliance.docKind.${d.kind}`) }}</span>
            <span>{{ d.title }}</span>
          </button>
        </div>
        <p class="muted small doc-to">{{ t('compliance.docTo', { who: docs.change.documents[docs.index].recipient }) }}</p>
        <!-- eslint-disable-next-line vue/no-v-html -- our own templates, filled from the BOM -->
        <article class="paper" v-html="html(docs.change.documents[docs.index].body)" />
      </template>
      <template #footer>
        <template v-if="docs">
          <Button :label="t('compliance.download')" icon="pi pi-download" text @click="download(docs.change.documents[docs.index])" />
          <template v-if="docs.change.stage === 'awaiting'">
            <Button :label="t('compliance.decline')" text severity="secondary" @click="decide(docs.part, false)" />
            <Button :label="t('compliance.signAll')" icon="pi pi-check" :loading="busy === docs.part.id" @click="decide(docs.part, true)" />
          </template>
        </template>
      </template>
    </Dialog>

    <Dialog v-model:visible="importOpen" modal :header="t('compliance.importTitle')" :style="{ width: '960px', maxWidth: '96vw' }">
      <div class="import">
        <p class="muted small">{{ t('compliance.importHint') }}</p>
        <div class="import-pick">
          <label class="p-button p-button-outlined p-button-sm file-btn">
            <i class="pi pi-upload" /> {{ t('compliance.chooseFile') }}
            <input type="file" accept=".md,.txt,.csv,text/plain,text/markdown" hidden @change="onFile" />
          </label>
          <Button :label="t('compliance.useDemoSpec')" icon="pi pi-file" size="small" text @click="useDemoSpec" />
          <a :href="DEMO_SPEC" download class="small">{{ t('compliance.downloadDemoSpec') }}</a>
        </div>
        <Message v-if="importing" severity="info" size="small"><i class="pi pi-spin pi-spinner" /> {{ t('compliance.reading') }}</Message>
        <Message v-if="importError" severity="error" size="small">{{ importError }}</Message>

        <template v-if="spec && !importing">
          <div class="import-meta">
            <strong>{{ spec.document }}</strong>
            <Tag :value="spec.method === 'ai' ? t('compliance.readByAi') : t('compliance.readByTable')" severity="secondary" :icon="spec.method === 'ai' ? 'pi pi-sparkles' : 'pi pi-table'" />
            <span v-if="spec.product" class="muted small">{{ t('compliance.productLabel') }}: {{ spec.product }}</span>
          </div>
          <table class="bom">
            <thead><tr><th>{{ t('compliance.component') }}</th><th>{{ t('compliance.supplier') }}</th><th>{{ t('compliance.origin') }}</th><th class="num">{{ t('compliance.cost') }}</th><th>{{ t('compliance.sourceCol') }}</th></tr></thead>
            <tbody>
              <tr v-for="c in spec.components" :key="c.location + c.name" :class="{ skipped: !c.complete }">
                <td>
                  {{ c.name }}
                  <div v-if="c.alternatives.length" class="muted small">{{ t('compliance.alternativesN', { n: c.alternatives.length }) }}: {{ c.alternatives.map((a) => a.supplier).join(', ') }}</div>
                </td>
                <td>{{ c.supplier ?? '—' }} <Tag v-if="c.sanctioned" :value="t('compliance.sanctionedTag')" severity="danger" icon="pi pi-ban" class="mini" /></td>
                <td class="nowrap">
                  <template v-if="c.country">{{ flag(c.country) }} {{ c.country }}
                    <span :class="eligible.has(c.country) ? 'good' : 'bad'"><i :class="eligible.has(c.country) ? 'pi pi-check' : 'pi pi-times'" /></span>
                  </template>
                  <span v-else class="bad">—</span>
                </td>
                <td class="num">{{ c.costEur != null ? eur(c.costEur) : '—' }}</td>
                <td class="small">
                  <span :class="c.verified ? 'good' : 'warn-text'"><i :class="c.verified ? 'pi pi-check-circle' : 'pi pi-exclamation-triangle'" /> {{ c.location }}</span>
                  <div class="quote">{{ c.quote }}</div>
                </td>
              </tr>
            </tbody>
          </table>
          <ul v-if="spec.warnings.length" class="warnings small">
            <li v-for="w in spec.warnings" :key="w"><i class="pi pi-exclamation-triangle" /> {{ w }}</li>
          </ul>
        </template>
      </div>
      <template #footer>
        <Button :label="t('compliance.cancel')" text @click="importOpen = false" />
        <Button v-if="spec" :label="t('compliance.importApply', { n: importable.length })" icon="pi pi-check" :disabled="!importable.length" :loading="importing" @click="applyImport" />
      </template>
    </Dialog>

    <Dialog v-model:visible="dialog" modal :header="editingId ? t('compliance.editPart') : t('compliance.addPart')" :style="{ width: '480px' }">
      <div class="form">
        <label>{{ t('compliance.component') }}<InputText v-model="form.name" :placeholder="t('compliance.partPlaceholder')" /></label>
        <label>{{ t('compliance.supplier') }}<InputText v-model="form.supplier" /></label>
        <label>{{ t('compliance.origin') }}
          <Select v-model="form.country" :options="countryGroups" option-group-label="label" option-group-children="items"
            option-label="label" option-value="value" filter :filter-fields="['name', 'value']" fluid />
        </label>
        <Tag v-if="form.country" :value="eligible.has(form.country) ? t('compliance.countsEu') : t('compliance.countsNonEu')"
          :severity="eligible.has(form.country) ? 'success' : 'danger'" class="counts" />
        <label>{{ t('compliance.cost') }} (EUR)<InputNumber v-model="form.costEur" :min="0" :max-fraction-digits="2" mode="currency" currency="EUR" fluid /></label>
        <Message v-if="error" severity="error" size="small">{{ error }}</Message>
      </div>
      <template #footer>
        <Button :label="t('compliance.cancel')" text @click="dialog = false" />
        <Button :label="t('compliance.save')" icon="pi pi-check" :disabled="!formValid" :loading="saving" @click="savePart" />
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.small { font-size: 12.5px; }
h3 { margin: 18px 0 4px; font-size: 15px; font-weight: 600; }
.card-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.inline-form { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.inline-form .p-inputtext { flex: 1 1 280px; }
.product { display: flex; align-items: center; gap: 2px; }
.form { display: flex; flex-direction: column; gap: 14px; }
.form label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 500; color: var(--muted); }
.counts { align-self: flex-start; margin-top: -6px; }
.nowrap { white-space: nowrap; }
.actions { white-space: nowrap; text-align: right; width: 1%; }
.good { color: var(--good); }
.bad { color: var(--bad); }
.warn-text { color: var(--warn); }

.verdict { border-left: 6px solid var(--bad); transition: background 0.4s; display: flex; flex-direction: column; gap: 16px; }
.verdict.ok { border-left-color: var(--good); }
.verdict.flash { background: var(--good-soft); }
.verdict-head { display: flex; gap: 14px; align-items: flex-start; }
.verdict-head > i { font-size: 28px; margin-top: 2px; }
.verdict-title { font-size: 22px; font-weight: 600; }

.checks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; border-top: 1px solid var(--border); }
.checks > li { border-bottom: 1px solid var(--border); }
.check-row { all: unset; box-sizing: border-box; width: 100%; display: flex; gap: 14px; align-items: center; padding: 12px 4px; cursor: pointer; }
.check-row:hover { background: var(--ground); }
.check-row:focus-visible { outline: 2px solid var(--accent); }
.check-body { flex: 1; display: flex; flex-direction: column; gap: 2px; }
.check-title { font-weight: 600; }
.status { font-weight: 600; font-size: 14px; white-space: nowrap; }
.pass .status { color: var(--good); }
.fail .status { color: var(--bad); }
.chev { color: var(--muted); font-size: 12px; transition: transform 0.2s; }
.open .chev { transform: rotate(180deg); }
.check-detail { padding: 4px 4px 18px 42px; display: flex; flex-direction: column; gap: 6px; }
.num-badge { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; flex: none; border-radius: 50%; background: var(--ground); border: 1px solid var(--border); font-size: 13px; font-weight: 600; }
.pass .num-badge { background: var(--good-soft); border-color: var(--good); color: var(--good); }
.fail .num-badge { background: var(--bad-soft); border-color: var(--bad); color: var(--bad); }

.split { margin: 22px 0 4px; }
.split-bar { position: relative; height: 14px; background: var(--bad-soft); }
.split-bar .eu { height: 100%; background: var(--good); transition: width 0.8s ease; }
.split-bar .mark { position: absolute; top: -6px; bottom: -6px; width: 2px; background: var(--text); }
.split-bar .mark span { position: absolute; bottom: calc(100% + 2px); left: 50%; transform: translateX(-50%); white-space: nowrap; font-size: 12px; font-weight: 600; }
.split-legend { display: flex; gap: 20px; flex-wrap: wrap; font-size: 14px; margin-top: 8px; }
.dot { display: inline-block; width: 10px; height: 10px; margin-right: 4px; }
.dot.eu { background: var(--good); }
.dot.non { background: var(--bad-soft); border: 1px solid var(--bad); }

.offender { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(260px, 1.6fr); gap: 16px; padding: 12px; border: 1px solid var(--border); background: var(--surface); }
.offender.best { border-color: var(--good); background: var(--good-soft); }
.offender.signed { background: var(--ground); }
@media (max-width: 760px) { .offender { grid-template-columns: 1fr; } }
.off-name { font-weight: 600; }
.off-fix { display: flex; flex-direction: column; gap: 4px; }
.mini { font-size: 11px; padding: 0 6px; margin-left: 6px; vertical-align: 1px; }
.listed { border: 1px solid var(--bad); background: var(--bad-soft); padding: 12px; display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
.listed-head { font-weight: 600; color: var(--bad); }
.listed p { margin: 0; }
.row-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
.what-apply { display: flex; gap: 8px; align-items: flex-start; background: var(--ground); padding: 8px 12px; margin: 6px 0 0; color: var(--muted); }
.what-apply i { margin-top: 2px; }

.owner { padding: 10px 0; border-bottom: 1px solid var(--border); }
.owner:last-child { border-bottom: 0; }
.owner-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.chain { margin: 8px 0 0; padding: 0; list-style: none; font-size: 14px; display: flex; flex-direction: column; gap: 4px; }
.chain li { display: flex; gap: 10px; }
.chain .role { width: 140px; flex: none; color: var(--muted); font-size: 13px; }
.chain li.bad { color: var(--bad); font-weight: 600; }
.screen { list-style: none; padding: 0; margin: 4px 0 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 4px 16px; font-size: 14px; }

.bom { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }
.bom th { text-align: left; color: var(--muted); font-weight: 500; padding: 8px; border-bottom: 1px solid var(--border); }
.bom td { padding: 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
.bom tr.non td { background: #fffafa; }
.num { text-align: right; }
.was { color: var(--muted); }
.source { color: var(--muted); margin-top: 2px; cursor: help; }
.head-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.import { display: flex; flex-direction: column; gap: 12px; }
.import-pick { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.file-btn { display: inline-flex; gap: 6px; align-items: center; cursor: pointer; }
.import-meta { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.bom tr.skipped td { opacity: 0.55; }
.quote { font-family: var(--mono); font-size: 11px; color: var(--muted); margin-top: 2px; max-width: 320px; overflow-wrap: anywhere; }
.warnings { list-style: none; margin: 0; padding: 8px 12px; background: var(--warn-soft); color: var(--warn); display: flex; flex-direction: column; gap: 4px; }

.doc-tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.doc-tab { all: unset; cursor: pointer; display: flex; flex-direction: column; gap: 2px; padding: 8px 12px; border: 1px solid var(--border); font-size: 13.5px; flex: 1 1 200px; }
.doc-tab.active { border-color: var(--accent); background: var(--accent-soft); }
.doc-kind { font-size: 12px; color: var(--muted); }
.doc-to { margin: 10px 0 0; }
.paper { border: 1px solid var(--border); padding: 20px 28px; margin-top: 8px; font-size: 14px; line-height: 1.55; background: var(--surface); }
.paper :deep(h1) { font-size: 20px; margin: 8px 0 12px; }
.paper :deep(h2) { font-size: 15px; margin: 16px 0 6px; }
.paper :deep(blockquote) { margin: 0 0 12px; padding: 8px 12px; background: var(--warn-soft); border-left: 3px solid var(--warn); }
.paper :deep(blockquote p) { margin: 0; }
.paper :deep(ul) { padding-left: 18px; }
</style>
