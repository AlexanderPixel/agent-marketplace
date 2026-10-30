<script setup lang="ts">
import BidSteps from '@/components/BidSteps.vue'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import Tabs from 'primevue/tabs'
import TabList from 'primevue/tablist'
import Tab from 'primevue/tab'
import TabPanels from 'primevue/tabpanels'
import TabPanel from 'primevue/tabpanel'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Button from 'primevue/button'
import Message from 'primevue/message'
import QualificationPanel from '@/components/QualificationPanel.vue'
import { api } from '@/lib/api'
import { flag } from '@/lib/flags'
import { session } from '@/lib/session'
import {
  criteriaNames, cpvLabel, daysLeft, deadlineSeverity, formatDate, isTranslated, localTitle, units,
  type Tender, type TenderRequirement,
} from '@/lib/tenders'

const { t } = useI18n()
const route = useRoute()
const tender = ref<Tender>()
const ocds = ref<unknown>()

async function load() {
  const id = encodeURIComponent(route.params.id as string)
  tender.value = await api<Tender>(`/tenders/${id}`)
  ocds.value = await api(`/tenders/${id}/ocds`)
}
onMounted(load)
watch(() => route.params.id, load)

const days = computed(() => daysLeft(tender.value?.deadline ?? null))
const languageName = (code: string) => new Intl.DisplayNames([session.language], { type: 'language' }).of(code) ?? code
const raw = computed(() => (tender.value?.rawJson ? JSON.stringify(JSON.parse(tender.value.rawJson), null, 2) : null))
const lotsLabel = (r: TenderRequirement) =>
  !r.lots.length ? '' : r.lots.length === tender.value?.lots.length && r.lots.length > 1
    ? t('tenders.allLots', { n: r.lots.length })
    : t('tenders.lotsList', { list: r.lots.map((l) => l.replace('LOT-', '').replace(/^0+/, '')).join(', ') })
const portal = computed(() => (tender.value?.source === 'TED' ? 'TED' : 'e-Zamówienia'))
</script>

<template>
  <div v-if="tender" class="page">
    <BidSteps :current="2" />
    <RouterLink to="/tenders" class="back"><i class="pi pi-arrow-left" /> {{ t('tenders.title') }}</RouterLink>

    <div class="card head">
      <div class="head-flag">{{ flag(tender.buyerCountry) }}</div>
      <div class="head-main">
        <div class="tags">
          <Tag :value="tender.source === 'TED' ? 'TED eForms' : 'e-Zamówienia BZP'" :severity="tender.source === 'TED' ? 'info' : 'secondary'" />
          <Tag v-if="tender.isDefence" value="Defence" icon="pi pi-shield" severity="contrast" />
          <span class="muted mono">{{ tender.sourceId }}</span>
        </div>
        <h1>{{ localTitle(tender) }}</h1>
        <div v-if="isTranslated(tender)" class="original">
          <i class="pi pi-language" /> {{ languageName(tender.language) }}: <em>{{ tender.title }}</em>
        </div>
        <div class="muted">{{ tender.buyerName }}<span v-if="tender.buyerCity">, {{ tender.buyerCity }}</span></div>
      </div>
      <div class="head-actions">
        <a v-if="tender.noticeUrl" :href="tender.noticeUrl" target="_blank" rel="noopener">
          <Button :label="`${t('tenders.openNotice')} (${portal})`" icon="pi pi-external-link" size="small" outlined />
        </a>
        <a v-if="tender.documentsUrl" :href="tender.documentsUrl" target="_blank" rel="noopener">
          <Button :label="t('tenders.documents')" icon="pi pi-folder-open" size="small" severity="secondary" outlined />
        </a>
      </div>
    </div>

    <div class="facts">
      <div class="card fact">
        <div class="muted">{{ t('tenders.deadline') }}</div>
        <div class="value">{{ formatDate(tender.deadline, true) }}</div>
        <Tag v-if="days !== null" :severity="deadlineSeverity(days)"
          :value="days < 0 ? t('tenders.closed') : days === 0 ? t('tenders.today') : t('tenders.daysLeft', { n: days })" />
      </div>
      <div class="card fact">
        <div class="muted">{{ t('tenders.cpv') }}</div>
        <div class="value mono">{{ tender.cpv }}</div>
        <div class="muted small">{{ cpvLabel(tender) }}</div>
      </div>
      <div class="card fact">
        <div class="muted">{{ t('tenders.lots') }}</div>
        <div class="value">{{ tender.lots.length || '—' }}</div>
        <div class="muted small">{{ tender.contractNature }}</div>
      </div>
      <div class="card fact">
        <div class="muted">{{ t('tenders.procedure') }}</div>
        <div class="value">{{ tender.procedureType ?? '—' }}</div>
        <div class="muted small">{{ t('tenders.published') }} {{ formatDate(tender.publishedAt) }}</div>
      </div>
      <div class="card fact">
        <div class="muted">{{ t('tenders.language') }}</div>
        <div class="value">{{ languageName(tender.language) }}</div>
        <div v-if="tender.reference" class="muted small">{{ t('tenders.reference') }}: {{ tender.reference }}</div>
      </div>
    </div>

    <QualificationPanel v-if="session.orgId" :tender="tender" />

    <Message v-if="!tender.hasFullNotice" severity="info" icon="pi pi-info-circle">{{ t('tenders.shortNotice') }}</Message>

    <div class="card tabs-card">
      <Tabs :value="tender.lots.length ? 'lots' : 'requirements'">
        <TabList>
          <Tab v-if="tender.lots.length" value="lots">{{ t('tenders.tabLots') }}</Tab>
          <Tab value="requirements">{{ t('tenders.tabRequirements') }} ({{ tender.requirements.length }})</Tab>
          <Tab v-if="tender.text" value="text">{{ t('tenders.tabText') }}</Tab>
          <Tab value="source">{{ t('tenders.tabSource') }}</Tab>
        </TabList>
        <TabPanels>
          <TabPanel v-if="tender.lots.length" value="lots">
            <DataTable :value="tender.lots" data-key="id" size="small" :row-class="(l) => (l.quantity === 800 ? 'hl-row' : '')">
              <Column field="id" header="Lot" style="width: 110px" />
              <Column header="">
                <template #body="{ data }">
                  <div>{{ data.title }}</div>
                  <div v-if="data.description" class="muted small clamp">{{ data.description }}</div>
                </template>
              </Column>
              <Column :header="t('tenders.quantity')" style="width: 140px">
                <template #body="{ data }">
                  <strong v-if="data.quantity !== null">{{ data.quantity.toLocaleString(session.language) }}</strong>
                  <span v-if="data.unit" class="muted">&nbsp;{{ units[data.unit] ?? data.unit }}</span>
                  <span v-if="data.quantity === null" class="muted">—</span>
                </template>
              </Column>
              <Column field="place" :header="t('tenders.place')" style="width: 170px" />
              <Column :header="t('tenders.sourceField')" style="width: 120px">
                <template #body="{ data }"><i v-tooltip="data.source" class="pi pi-book muted" /></template>
              </Column>
            </DataTable>
          </TabPanel>

          <TabPanel value="requirements">
            <p v-if="!tender.requirements.length" class="muted">{{ t('tenders.noRequirements') }}</p>
            <div v-for="(r, i) in tender.requirements" :key="i" class="req">
              <div class="req-head">
                <Tag v-if="r.code" :value="criteriaNames[r.code] ?? r.code" severity="secondary" />
                <span class="muted small">{{ lotsLabel(r) }}</span>
              </div>
              <div class="req-text" :lang="r.language">{{ r.text }}</div>
              <div class="cite"><i class="pi pi-bookmark" /> {{ r.source }} · {{ languageName(r.language) }}</div>
            </div>
            <div v-if="tender.exclusionGrounds.length" class="exclusion">
              <div class="muted">{{ t('tenders.exclusion') }}</div>
              <div class="chips">
                <Tag v-for="g in tender.exclusionGrounds" :key="g" :value="g" severity="secondary" class="mono" />
              </div>
            </div>
          </TabPanel>

          <TabPanel v-if="tender.text" value="text">
            <pre class="notice-text">{{ tender.text }}</pre>
          </TabPanel>

          <TabPanel value="source">
            <div class="source-grid">
              <div>
                <h2>{{ t('tenders.ocds') }}</h2>
                <pre class="json">{{ JSON.stringify(ocds, null, 2) }}</pre>
              </div>
              <div v-if="raw">
                <h2>{{ t('tenders.raw') }} (eForms)</h2>
                <pre class="json">{{ raw }}</pre>
              </div>
            </div>
          </TabPanel>
        </TabPanels>
      </Tabs>
    </div>
  </div>
</template>

<style scoped>
.back { display: inline-flex; gap: 6px; align-items: center; text-decoration: none; font-size: 14.5px; font-weight: 500; }
.head { display: flex; gap: 18px; align-items: flex-start; flex-wrap: wrap; background: none; border: 0; padding: 0; }
.head-flag { font-size: 44px; line-height: 1; }
.head-main { flex: 1 1 400px; display: flex; flex-direction: column; gap: 8px; }
.head-main h1 { font-size: 34px; max-width: 28ch; }
.head-actions { display: flex; flex-direction: column; gap: 8px; }
.tags { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.original { font-size: 14px; color: var(--muted); }
.facts { display: grid; gap: 0; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); background: var(--surface); border: 1px solid var(--border); }
.facts .card { border: 0; border-right: 1px solid var(--border); }
.facts .card:last-child { border-right: 0; }
.fact > .muted:first-child { font-size: 13px; font-weight: 500; }
.fact { padding: 16px 20px; display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.fact .value { font-size: 22px; font-weight: 400; letter-spacing: -0.02em; }
.small { font-size: 12.5px; }
.mono { font-family: var(--mono); }
.clamp { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.tabs-card { padding: 4px 8px 8px; }
.req { border-bottom: 1px solid var(--border); padding: 14px 4px; display: flex; flex-direction: column; gap: 6px; }
.req-head { display: flex; gap: 10px; align-items: center; }
.req-text { white-space: pre-line; font-size: 14.5px; }
.cite { font-size: 12px; color: var(--muted); }
.exclusion { padding: 14px 4px; display: flex; flex-direction: column; gap: 8px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.notice-text { white-space: pre-wrap; font-family: inherit; font-size: 14px; max-height: 600px; overflow: auto; margin: 0; }
.source-grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); }
.json { background: var(--ink); color: #d9e2f5; padding: 14px; border-radius: 8px; font-size: 12px; max-height: 560px; overflow: auto; margin-top: 8px; }
:deep(.hl-row) { background: var(--accent-soft) !important; font-weight: 500; }
</style>
