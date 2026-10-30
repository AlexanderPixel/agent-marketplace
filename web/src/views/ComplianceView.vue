<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
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

interface Alt { supplier: string; country: string; costEur: number; note: string }
interface Report {
  threshold: number; eligibleCountries: string[]; product: string | null; euContent: number; safeEligible: boolean; contentOk: boolean; controlOk: boolean
  components: { component: { id: string; name: string; supplier: string; country: string; costEur: number }; eligible: boolean; share: number }[]
  swaps: { componentId: string; component: string; from: string; fromCountry: string; to: Alt; costDelta: number; newEuContent: number; reachesThreshold: boolean }[]
  control: { company: string; country: string; role: string; chain: { entity: string; country: string; share: number; role: string }[]; euControlled: boolean; detail: string }[]
  sanctions: { name: string; country: string; hit: boolean; detail: string }[]
}

const { t } = useI18n()
const report = ref<Report>()
const applying = ref<string>()
const flash = ref(false)

type Part = Report['components'][number]['component']
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

async function load() {
  if (!session.orgId) return
  report.value = await api<Report>('/compliance')
}
watch(() => session.orgId, load, { immediate: true })

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

async function apply(s: Report['swaps'][number]) {
  applying.value = s.componentId + s.to.supplier
  try {
    await post('/compliance/swap', { componentId: s.componentId, supplier: s.to.supplier })
    await load()
    flash.value = true
    setTimeout(() => (flash.value = false), 1200)
  } finally {
    applying.value = undefined
  }
}
</script>

<template>
  <div v-if="report" class="page">
    <div class="page-head">
      <div>
        <h1>{{ t('compliance.title') }}</h1>
        <p class="muted">{{ t('compliance.subtitle') }}</p>
      </div>
    </div>

    <div v-if="!report.product" class="card">
      <h2>{{ t('compliance.createTitle') }}</h2>
      <p class="muted">{{ t('compliance.createHint') }}</p>
      <div class="inline-form">
        <InputText v-model="productDraft" :placeholder="t('compliance.productPlaceholder')" @keyup.enter="saveProduct" />
        <Button :label="t('compliance.create')" icon="pi pi-plus" :disabled="!productDraft.trim()" @click="saveProduct" />
      </div>
    </div>

    <template v-else>
      <div class="card hero" :class="{ ok: report.safeEligible, flash }">
        <div class="badge-seal" :class="{ ok: report.safeEligible }">
          <span class="stars">★ ★ ★</span>
          <strong>SAFE</strong><strong>EDIP</strong>
          <i :class="report.safeEligible ? 'pi pi-check' : 'pi pi-times'" />
        </div>
        <div class="hero-main">
          <div class="hero-title">{{ report.safeEligible ? t('compliance.eligible') : t('compliance.notEligible') }}</div>
          <div v-if="!editingProduct" class="muted product">
            {{ report.product }}
            <Button icon="pi pi-pencil" text rounded size="small" :aria-label="t('compliance.editProduct')" @click="editProduct" />
          </div>
          <div v-else class="inline-form">
            <InputText v-model="productDraft" @keyup.enter="saveProduct" />
            <Button icon="pi pi-check" size="small" :aria-label="t('compliance.save')" @click="saveProduct" />
            <Button icon="pi pi-times" size="small" text :aria-label="t('compliance.cancel')" @click="editingProduct = false" />
          </div>
          <div class="gauge">
            <div class="gauge-fill" :style="{ width: `${report.euContent}%` }" />
            <div class="gauge-mark" :style="{ left: `${report.threshold}%` }"><span>{{ t('compliance.threshold', { n: report.threshold }) }}</span></div>
          </div>
          <div class="gauge-legend">
            <span class="pct">{{ t('compliance.euContent') }}: <strong>{{ report.euContent }}%</strong></span>
            <span :class="report.contentOk ? 'good' : 'bad'">
              <i :class="report.contentOk ? 'pi pi-check-circle' : 'pi pi-times-circle'" />
              {{ report.contentOk ? t('compliance.contentOk', { n: report.euContent, t: report.threshold }) : t('compliance.contentShort', { n: report.euContent, t: report.threshold }) }}
            </span>
            <span v-if="!report.controlOk" class="bad"><i class="pi pi-times-circle" /> {{ t('compliance.controlBad') }}</span>
          </div>
        </div>
      </div>

      <div v-if="report.swaps.length" class="card">
        <h2>{{ t('compliance.swaps') }}</h2>
        <div v-for="(s, i) in report.swaps" :key="s.componentId + s.to.supplier" class="swap" :class="{ best: i === 0 && s.reachesThreshold }">
          <i class="pi pi-sparkles" />
          <div class="swap-main">
            <div>{{ t('compliance.swapText', { component: s.component, from: `${flag(s.fromCountry)} ${s.from}`, to: `${flag(s.to.country)} ${s.to.supplier}` }) }}</div>
            <div class="muted small">{{ s.to.note }} · {{ s.costDelta >= 0 ? '+' : '' }}{{ eur(s.costDelta) }} / set</div>
          </div>
          <div class="swap-pct">{{ report.euContent }}% → <strong :class="s.reachesThreshold ? 'good' : ''">{{ s.newEuContent }}%</strong>
            <Tag v-if="s.reachesThreshold" :value="t('compliance.reaches')" severity="success" />
          </div>
          <Button :label="t('compliance.apply')" icon="pi pi-check" size="small" :severity="i === 0 ? undefined : 'secondary'" :outlined="i !== 0"
            :loading="applying === s.componentId + s.to.supplier" @click="apply(s)" />
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <h2>{{ t('compliance.bom') }}</h2>
          <Button :label="t('compliance.addPart')" icon="pi pi-plus" size="small" @click="openPart()" />
        </div>
        <p class="muted small">{{ t('compliance.originNote') }}</p>
        <table class="bom">
          <thead><tr><th>{{ t('compliance.component') }}</th><th>{{ t('compliance.supplier') }}</th><th>{{ t('compliance.origin') }}</th><th class="num">{{ t('compliance.cost') }}</th><th>{{ t('compliance.share') }}</th><th /></tr></thead>
          <tbody>
            <tr v-if="!report.components.length"><td colspan="6" class="muted">{{ t('compliance.noParts') }}</td></tr>
            <tr v-for="c in report.components" :key="c.component.id">
              <td>{{ c.component.name }}</td>
              <td>{{ c.component.supplier }}</td>
              <td class="nowrap">{{ flag(c.component.country) }} {{ countryName(c.component.country) }} <Tag :value="c.eligible ? 'EU' : c.component.country" :severity="c.eligible ? 'success' : 'danger'" class="mini" /></td>
              <td class="num">{{ eur(c.component.costEur) }}</td>
              <td><div class="share"><div :class="c.eligible ? 'eu' : 'non'" :style="{ width: `${Math.max(2, c.share * 1.2)}px` }" /><span>{{ c.share }}%</span></div></td>
              <td class="actions">
                <Button icon="pi pi-pencil" text rounded size="small" :aria-label="t('compliance.edit')" @click="openPart(c.component)" />
                <Button icon="pi pi-trash" text rounded size="small" severity="danger" :aria-label="t('compliance.remove')" @click="removePart(c.component)" />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="two">
        <div class="card">
          <h2>{{ t('compliance.ownership') }}</h2>
          <div v-for="c in report.control" :key="c.company" class="owner">
            <div class="owner-head">
              <strong>{{ flag(c.country) }} {{ c.company }}</strong>
              <Tag :value="c.role === 'you' ? t('compliance.you') : t('compliance.partner')" severity="secondary" />
              <Tag :value="c.euControlled ? t('compliance.controlled') : t('compliance.foreign')" :severity="c.euControlled ? 'success' : 'danger'" />
            </div>
            <ol class="chain">
              <li v-for="l in c.chain" :key="l.entity" :class="{ bad: l.share > 50 && !c.euControlled && l.role === 'ultimate' }">
                {{ flag(l.country) }} {{ l.entity }} <span class="muted">· {{ l.share }}% · {{ l.role }}</span>
              </li>
            </ol>
            <div class="muted small">{{ c.detail }}</div>
          </div>
        </div>
        <div class="card">
          <h2>{{ t('compliance.sanctions') }}</h2>
          <p class="muted small">{{ t('compliance.sanctionsNote') }}</p>
          <Tag :value="t('compliance.noHits', { n: report.sanctions.length })" :severity="report.sanctions.some((s) => s.hit) ? 'danger' : 'success'" icon="pi pi-shield" />
          <ul class="screen">
            <li v-for="s in report.sanctions" :key="s.name"><i :class="s.hit ? 'pi pi-times bad' : 'pi pi-check good'" /> {{ flag(s.country) }} {{ s.name }}</li>
          </ul>
        </div>
      </div>
    </template>

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
.hero { display: flex; gap: 24px; align-items: center; border-left: 6px solid var(--bad); transition: background 0.4s; }
.hero.ok { border-left-color: var(--good); background: var(--good-soft); }
.hero.flash { background: #d6f5e0; }
.badge-seal { width: 110px; height: 110px; border-radius: 50%; flex: none; background: #8a8f9c; color: white; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 1px; box-shadow: inset 0 0 0 5px rgb(255 255 255 / 25%); }
.badge-seal.ok { background: var(--accent); }
.badge-seal .stars { color: var(--eu-gold); font-size: 11px; letter-spacing: 2px; }
.badge-seal strong { font-size: 15px; line-height: 1.1; }
.badge-seal i { color: var(--eu-gold); margin-top: 2px; }
.hero-main { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.hero-title { font-size: 22px; font-weight: 600; }
.gauge { position: relative; height: 16px; background: var(--bad-soft); border-radius: 8px; margin-top: 22px; }
.gauge-fill { height: 100%; background: var(--accent); border-radius: 8px; transition: width 0.8s ease; }
.ok .gauge-fill { background: var(--good); }
.gauge-mark { position: absolute; top: -6px; bottom: -6px; width: 2px; background: var(--text); }
.gauge-mark span { position: absolute; bottom: 100%; left: -40px; width: 140px; font-size: 12px; font-weight: 600; }
.gauge-legend { display: flex; gap: 20px; flex-wrap: wrap; font-size: 14px; align-items: center; }
.pct strong { font-size: 20px; }
.swap { display: flex; gap: 14px; align-items: center; padding: 12px; border: 1px solid var(--border); border-radius: 10px; margin-top: 10px; flex-wrap: wrap; }
.swap.best { border-color: var(--good); background: var(--good-soft); }
.swap > i { color: var(--accent); }
.swap-main { flex: 1 1 320px; }
.swap-pct { display: flex; gap: 8px; align-items: center; }
.bom { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }
.bom th { text-align: left; color: var(--muted); font-weight: 500; padding: 8px; border-bottom: 1px solid var(--border); }
.bom td { padding: 8px; border-bottom: 1px solid var(--border); }
.num { text-align: right; }
.mini { font-size: 11px; padding: 0 6px; margin-left: 6px; }
.share { display: flex; gap: 8px; align-items: center; }
.share div { flex: none; height: 8px; border-radius: 4px; }
.share .eu { background: var(--good); }
.share .non { background: var(--bad); }
.share span { font-size: 12.5px; color: var(--muted); }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 1000px) { .two { grid-template-columns: 1fr; } }
.owner { padding: 12px 0; border-bottom: 1px solid var(--border); }
.owner:last-child { border-bottom: 0; }
.owner-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.chain { margin: 8px 0; padding-left: 20px; font-size: 14px; display: flex; flex-direction: column; gap: 3px; }
.chain li.bad { color: var(--bad); font-weight: 600; }
.screen { list-style: none; padding: 0; margin: 12px 0 0; display: flex; flex-direction: column; gap: 4px; font-size: 14px; }
</style>
