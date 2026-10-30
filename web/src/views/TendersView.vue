<script setup lang="ts">
import BidSteps from '@/components/BidSteps.vue'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import InputText from 'primevue/inputtext'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import Select from 'primevue/select'
import ToggleSwitch from 'primevue/toggleswitch'
import Tag from 'primevue/tag'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { useToast } from 'primevue/usetoast'
import { api, post } from '@/lib/api'
import { flag } from '@/lib/flags'
import { session } from '@/lib/session'
import {
  cpvLabel, daysLeft, deadlineSeverity, formatDate, isTranslated, localTitle, verdictIcon, verdictSeverity, type TenderSummary,
} from '@/lib/tenders'

const { t } = useI18n()
const router = useRouter()
const toast = useToast()

const rows = ref<TenderSummary[]>([])
const total = ref(0)
const loading = ref(false)
const q = ref('')
const country = ref<string | null>(null)
const source = ref<string | null>(null)
const defence = ref(true)
const open = ref(true)
const countries = ref<string[]>([])
const importOpen = ref(false)
const importNumber = ref('')
const importing = ref(false)

let timer: ReturnType<typeof setTimeout> | undefined
async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (q.value) params.set('q', q.value)
    if (country.value) params.set('country', country.value)
    if (source.value) params.set('source', source.value)
    if (defence.value) params.set('defence', 'true')
    if (open.value) params.set('open', 'true')
    const data = await api<{ total: number; rows: TenderSummary[] }>(`/tenders?${params}`)
    rows.value = data.rows
    total.value = data.total
  } finally {
    loading.value = false
  }
}

watch([country, source, defence, open, () => session.orgId], load)
watch(q, () => {
  clearTimeout(timer)
  timer = setTimeout(load, 250)
})
onMounted(async () => {
  countries.value = (await api<{ countries: string[] }>('/tenders/stats')).countries
  await load()
})

const countryOptions = computed(() => [
  { label: t('tenders.any'), value: null },
  ...countries.value.map((c) => ({ label: `${flag(c)} ${c}`, value: c })),
])
const sourceOptions = computed(() => [
  { label: t('tenders.any'), value: null },
  { label: 'TED (EU)', value: 'TED' },
  { label: 'e-Zamówienia (BZP)', value: 'BZP' },
])

async function runImport() {
  importing.value = true
  try {
    const { id } = await post<{ id: string }>(`/tenders/import/ted/${encodeURIComponent(importNumber.value.trim())}`)
    toast.add({ severity: 'success', summary: t('tenders.imported'), life: 3000 })
    importOpen.value = false
    router.push(`/tenders/${encodeURIComponent(id)}`)
  } catch (e) {
    toast.add({ severity: 'error', summary: t('tenders.importFailed'), detail: String(e).slice(0, 200), life: 6000 })
  } finally {
    importing.value = false
  }
}

const languageName = (code: string) =>
  new Intl.DisplayNames([session.language], { type: 'language' }).of(code) ?? code
</script>

<template>
  <div class="page">
    <BidSteps :current="1" />
    <div class="page-head">
      <div>
        <h1>{{ t('tenders.title') }}</h1>
        <p class="muted">{{ t('tenders.subtitle') }}</p>
      </div>
      <Button :label="t('tenders.import')" icon="pi pi-download" severity="secondary" outlined @click="importOpen = true" />
    </div>

    <div class="card filters">
      <IconField class="search">
        <InputIcon class="pi pi-search" />
        <InputText v-model="q" :placeholder="t('tenders.search')" fluid />
      </IconField>
      <Select v-model="country" :options="countryOptions" option-label="label" option-value="value" :placeholder="t('tenders.country')" />
      <Select v-model="source" :options="sourceOptions" option-label="label" option-value="value" :placeholder="t('tenders.source')" />
      <label class="toggle"><ToggleSwitch v-model="defence" /> {{ t('tenders.defenceOnly') }}</label>
      <label class="toggle"><ToggleSwitch v-model="open" /> {{ t('tenders.openOnly') }}</label>
      <span class="muted count">{{ t('tenders.count', { shown: rows.length, total }) }}</span>
    </div>

    <div class="card table-card">
      <DataTable
        :value="rows"
        :loading="loading"
        data-key="id"
        paginator
        :rows="25"
        row-hover
        :row-class="(r: TenderSummary) => (r.featured ? 'featured-row' : '')"
        @row-click="(e) => router.push(`/tenders/${encodeURIComponent(e.data.id)}`)"
      >
        <Column :header="t('tenders.title')">
          <template #body="{ data }: { data: TenderSummary }">
            <div class="title-cell">
              <span class="flag">{{ flag(data.buyerCountry) }}</span>
              <div>
                <div class="title">
                  <Tag v-if="data.featured" :value="t('tenders.featured')" icon="pi pi-star-fill" class="featured-tag" />
                  {{ localTitle(data) }}
                </div>
                <div class="muted small">
                  <span v-if="isTranslated(data)" v-tooltip="data.title" class="translated">
                    <i class="pi pi-language" /> {{ t('tenders.translated', { lang: languageName(data.language) }) }} ·
                  </span>
                  {{ data.buyerName }}<span v-if="data.buyerCity">, {{ data.buyerCity }}</span>
                </div>
              </div>
            </div>
          </template>
        </Column>
        <Column :header="t('qual.column')" style="width: 150px">
          <template #body="{ data }: { data: TenderSummary }">
            <Tag v-if="data.verdict" :severity="verdictSeverity[data.verdict]" :icon="verdictIcon[data.verdict]"
              :value="t(`qual.verdict.${data.verdict}`)" class="verdict" />
          </template>
        </Column>
        <Column :header="t('tenders.cpv')" style="width: 200px">
          <template #body="{ data }: { data: TenderSummary }">
            <div class="mono">{{ data.cpv }}</div>
            <div class="muted small clamp">{{ cpvLabel(data) }}</div>
          </template>
        </Column>
        <Column :header="t('tenders.lots')" style="width: 70px">
          <template #body="{ data }: { data: TenderSummary }">{{ data.lotCount || '—' }}</template>
        </Column>
        <Column :header="t('tenders.deadline')" style="width: 170px">
          <template #body="{ data }: { data: TenderSummary }">
            <div>{{ formatDate(data.deadline) }}</div>
            <Tag
              v-if="data.deadline"
              :severity="deadlineSeverity(daysLeft(data.deadline))"
              :value="daysLeft(data.deadline)! < 0 ? t('tenders.closed') : daysLeft(data.deadline) === 0 ? t('tenders.today') : t('tenders.daysLeft', { n: daysLeft(data.deadline) })"
            />
          </template>
        </Column>
        <Column :header="t('tenders.source')" style="width: 110px">
          <template #body="{ data }: { data: TenderSummary }">
            <Tag :value="data.source === 'TED' ? 'TED eForms' : 'BZP'" :severity="data.source === 'TED' ? 'info' : 'secondary'" />
            <div class="muted small mono">{{ data.sourceId }}</div>
          </template>
        </Column>
      </DataTable>
    </div>

    <Dialog v-model:visible="importOpen" modal :header="t('tenders.import')" style="width: 420px">
      <p class="muted">{{ t('tenders.importHint') }}</p>
      <InputText v-model="importNumber" placeholder="723644-2026" fluid @keyup.enter="runImport" />
      <template #footer>
        <Button :label="t('tenders.import')" icon="pi pi-download" :loading="importing" :disabled="!importNumber.trim()" @click="runImport" />
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.filters { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; padding: 14px 16px; }
.search { flex: 1 1 320px; }
.toggle { display: inline-flex; gap: 8px; align-items: center; font-size: 14px; }
.count { margin-left: auto; }
.table-card { padding: 0; overflow: hidden; }
.title-cell { display: flex; gap: 12px; align-items: flex-start; cursor: pointer; }
.flag { font-size: 22px; line-height: 1.2; }
.title { font-weight: 500; }
.small { font-size: 12.5px; margin-top: 2px; }
.mono { font-family: ui-monospace, monospace; font-size: 13px; }
.clamp { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.translated { color: var(--accent); }
.verdict { white-space: normal; text-align: left; }
.featured-tag { margin-right: 6px; background: var(--accent); color: #ffffff; }
:deep(.featured-row) { background: var(--accent-soft) !important; box-shadow: inset 3px 0 0 var(--accent); }
:deep(.p-datatable-tbody > tr) { cursor: pointer; }
</style>
