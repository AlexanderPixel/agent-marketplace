<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { marked } from 'marked'
import Button from 'primevue/button'
import SelectButton from 'primevue/selectbutton'
import Checkbox from 'primevue/checkbox'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import { api, post } from '@/lib/api'
import { flag } from '@/lib/flags'
import { session } from '@/lib/session'
import type { AgreementDocument, Deal, Party } from '@/lib/tenders'

const { t } = useI18n()
const route = useRoute()
const data = ref<{ deal: Deal; bidder: Party; partner: Party }>()
const selected = ref<string>()
const view = ref<'pl' | 'de' | 'both'>('both')
const generating = ref(false)
const signed = ref<Record<string, boolean>>({})

async function load() {
  data.value = await api(`/deals/${route.params.id}`)
  selected.value ??= data.value?.deal.documents[0]?.id
}
watch(() => [route.params.id, session.orgId], load, { immediate: true })

async function generate() {
  generating.value = true
  try {
    await post(`/deals/${route.params.id}/documents`)
    selected.value = undefined
    await load()
  } finally {
    generating.value = false
  }
}

const docs = computed(() => data.value?.deal.documents ?? [])
const doc = computed(() => docs.value.find((d) => d.id === selected.value))
const title = (d: AgreementDocument) => d.title[session.language] ?? d.title.pl ?? Object.values(d.title)[0]
const html = (md: string) => marked.parse(md, { async: false }) as string
const party = (p: string) => (p === 'bidder' ? data.value?.bidder : data.value?.partner)
const views = computed(() => [
  { label: '🇵🇱 PL', value: 'pl' },
  { label: '🇩🇪 DE', value: 'de' },
  { label: t('docs.sideBySide'), value: 'both' },
])

function download(d: AgreementDocument) {
  const text = view.value === 'both' ? `${d.body.de}\n\n---\n\n${d.body.pl}` : d.body[view.value]
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }))
  const a = Object.assign(document.createElement('a'), { href: url, download: `${d.id}-${view.value}.md` })
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div v-if="data" class="page">
    <RouterLink :to="`/deals/${data.deal.id}`" class="back"><i class="pi pi-arrow-left" /> {{ t('deals.room') }}</RouterLink>
    <div class="page-head">
      <div>
        <h1>{{ t('docs.title') }}</h1>
        <p class="muted">{{ flag(data.bidder.country) }} {{ data.bidder.name }} ⇄ {{ flag(data.partner.country) }} {{ data.partner.name }} · {{ data.deal.lotId }} · TED {{ data.deal.tenderRef }}</p>
      </div>
      <Button v-if="data.deal.status === 'Closed'" :label="docs.length ? t('docs.regenerate') : t('docs.generate')"
        :icon="docs.length ? 'pi pi-refresh' : 'pi pi-file-edit'" :severity="docs.length ? 'secondary' : undefined" :outlined="!!docs.length"
        :loading="generating" @click="generate" />
    </div>

    <Message v-if="data.deal.status !== 'Closed'" severity="info">{{ t('docs.notClosed') }}</Message>

    <template v-if="docs.length">
      <div class="card">
        <h2>{{ t('docs.checklist') }}</h2>
        <table class="checklist">
          <thead>
            <tr><th>{{ t('docs.document') }}</th><th>{{ t('docs.signs') }}</th><th>{{ t('docs.signature') }}</th><th>{{ t('docs.submit') }}</th><th>{{ t('docs.done') }}</th></tr>
          </thead>
          <tbody>
            <template v-for="d in docs" :key="d.id">
              <tr v-for="(s, i) in d.signers" :key="d.id + i" :class="{ signed: signed[d.id + i] }">
                <td v-if="i === 0" :rowspan="d.signers.length"><a href="#" @click.prevent="selected = d.id"><strong>{{ title(d) }}</strong></a><div class="muted small">{{ d.purpose }}</div></td>
                <td>{{ flag(party(s.party)?.country ?? '') }} {{ s.name }}</td>
                <td><Tag :value="t(`docs.sig.${s.signature}`)" :severity="s.signature === 'qes' ? 'info' : 'secondary'" :icon="s.signature === 'qes' ? 'pi pi-verified' : 'pi pi-pencil'" /></td>
                <td v-if="i === 0" :rowspan="d.signers.length">{{ d.submitTo }} <Tag v-if="d.submitWithBid" :value="t('docs.withBid')" severity="warn" /></td>
                <td><Checkbox v-model="signed[d.id + i]" binary /></td>
              </tr>
            </template>
          </tbody>
        </table>
        <p class="human"><i class="pi pi-user-edit" /> {{ t('docs.humanSigns') }}</p>
      </div>

      <div class="viewer">
        <div class="card doc-list">
          <button v-for="d in docs" :key="d.id" class="doc-item" :class="{ active: d.id === selected }" @click="selected = d.id">
            <i class="pi pi-file" />
            <div>
              <div class="doc-title">{{ title(d) }}</div>
              <div class="muted small">{{ Object.keys(d.body).map((l) => l.toUpperCase()).join(' · ') }}</div>
            </div>
          </button>
        </div>
        <div v-if="doc" class="card doc-view">
          <div class="doc-toolbar">
            <span class="ai-label"><i class="pi pi-sparkles" /> {{ t('docs.aiDraft') }}</span>
            <div class="tools">
              <SelectButton v-model="view" :options="views" option-label="label" option-value="value" :allow-empty="false" size="small" />
              <Button :label="t('docs.download')" icon="pi pi-download" size="small" text @click="download(doc)" />
            </div>
          </div>
          <div class="pages" :class="{ both: view === 'both' }">
            <!-- eslint-disable-next-line vue/no-v-html -- our own templates, rendered from deal terms -->
            <article v-if="view !== 'pl'" class="paper" lang="de" v-html="html(doc.body.de ?? '')" />
            <!-- eslint-disable-next-line vue/no-v-html -->
            <article v-if="view !== 'de'" class="paper" lang="pl" v-html="html(doc.body.pl ?? '')" />
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.back { display: inline-flex; gap: 6px; align-items: center; text-decoration: none; font-size: 14px; }
.small { font-size: 12.5px; }
.checklist { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }
.checklist th { text-align: left; color: var(--muted); font-weight: 500; padding: 8px; border-bottom: 1px solid var(--border); }
.checklist td { padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
.checklist a { text-decoration: none; color: var(--text); }
.checklist tr.signed td:not([rowspan]) { color: var(--good); }
.human { margin: 12px 0 0; display: flex; gap: 8px; align-items: center; font-size: 14px; color: var(--accent); }
.viewer { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 900px) { .viewer { grid-template-columns: 1fr; } }
.doc-list { padding: 8px; display: flex; flex-direction: column; gap: 4px; }
.doc-item { display: flex; gap: 10px; align-items: flex-start; text-align: left; border: 0; background: none; padding: 10px; border-radius: 8px; cursor: pointer; font: inherit; color: inherit; }
.doc-item:hover { background: var(--ground); }
.doc-item.active { background: var(--accent-soft); }
.doc-item i { color: var(--accent); margin-top: 3px; }
.doc-title { font-weight: 500; }
.doc-view { display: flex; flex-direction: column; gap: 12px; }
.doc-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.tools { display: flex; gap: 8px; align-items: center; }
.pages { display: grid; gap: 16px; }
.pages.both { grid-template-columns: 1fr 1fr; }
@media (max-width: 1100px) { .pages.both { grid-template-columns: 1fr; } }
.paper { background: white; border: 1px solid var(--border); border-radius: 6px; padding: 28px 32px; font-family: Georgia, 'Times New Roman', serif; font-size: 14.5px; line-height: 1.55;
  box-shadow: 0 1px 3px rgb(0 0 0 / 6%); }
.paper :deep(h1) { font-size: 20px; text-align: center; margin: 8px 0 16px; }
.paper :deep(h2) { font-size: 15px; margin: 18px 0 6px; }
.paper :deep(blockquote) { margin: 0 0 12px; padding: 8px 12px; background: var(--warn-soft); border-left: 3px solid var(--warn); font-family: 'IBM Plex Sans', sans-serif; font-size: 13px; }
.paper :deep(blockquote p) { margin: 0; }
.paper :deep(table) { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13.5px; }
.paper :deep(th), .paper :deep(td) { border: 1px solid var(--border); padding: 6px 8px; text-align: left; }
</style>
