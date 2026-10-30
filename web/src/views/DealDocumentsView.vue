<script setup lang="ts">
import BidSteps from '@/components/BidSteps.vue'
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { marked } from 'marked'
import Button from 'primevue/button'
import SelectButton from 'primevue/selectbutton'
import Checkbox from 'primevue/checkbox'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import { api, post } from '@/lib/api'
import { autopilotAt, beat, markDocumentsShown, showSection } from '@/lib/autopilot'
import { flag } from '@/lib/flags'
import { session } from '@/lib/session'
import type { AgreementDocument, Deal, Party, TenderCertificate } from '@/lib/tenders'

const { t } = useI18n()
const route = useRoute()
const data = ref<{ deal: Deal; bidder: Party; partner: Party; certificates: TenderCertificate[] }>()
const selected = ref<string>()
const view = ref<'pl' | 'de' | 'both'>('both')
const generating = ref(false)
const signed = ref<Record<string, boolean>>({})

async function load() {
  data.value = await api(`/deals/${route.params.id}`)
  selected.value ??= data.value?.deal.documents[0]?.id
}
watch(() => [route.params.id, session.orgId], load, { immediate: true })

const docs = computed(() => data.value?.deal.documents ?? [])

// Autopilot, documents step: the server has drafted them; walk through the checklist and the drafts.
watch(() => docs.value.length, async (n) => {
  if (!autopilotAt('documents') || !n) return
  await beat(800)
  await showSection('[data-ap="checklist"]', 'start')
  await beat(3000)
  await showSection('[data-ap="viewer"]', 'start')
  markDocumentsShown()
}, { immediate: true })

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

const certs = computed(() => data.value?.certificates ?? [])
const doc = computed<AgreementDocument | TenderCertificate | undefined>(() =>
  docs.value.find((d) => d.id === selected.value) ?? certs.value.find((c) => c.id === selected.value))
const cert = computed(() => certs.value.find((c) => c.id === selected.value))
// The tender's required certificates, and which consortium member covers each.
const required = computed(() => certs.value.filter((c) => c.demoRequirement))
const split = computed(() => {
  const by = new Map<string, { label: string; n: number }>()
  for (const c of required.value) {
    const key = c.providedBy?.orgId ?? '-'
    const label = c.providedBy ? `${flag(c.providedBy.country)} ${c.providedBy.name}` : t('docs.matrixMissing')
    by.set(key, { label, n: (by.get(key)?.n ?? 0) + 1 })
  }
  return [...by.values()].map((x) => `${x.n} × ${x.label}`).join(' · ')
})
// The other company in the deal, from the viewer's side: it attaches its own certificates.
const otherParty = computed(() => (data.value && session.orgId === data.value.deal.bidderOrgId ? data.value.partner : data.value?.bidder))
const title = (d: AgreementDocument | TenderCertificate) => d.title[session.language] ?? d.title.pl ?? Object.values(d.title)[0]
const html = (md: string) => marked.parse(md, { async: false }) as string
const party = (p: string) => (p === 'bidder' ? data.value?.bidder : data.value?.partner)
const views = computed(() => [
  { label: '🇵🇱 PL', value: 'pl' },
  { label: '🇩🇪 DE', value: 'de' },
  { label: t('docs.sideBySide'), value: 'both' },
])

function download(d: AgreementDocument | TenderCertificate) {
  const text = view.value === 'both' ? `${d.body.pl}\n\n---\n\n${d.body.de}` : d.body[view.value]
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }))
  const a = Object.assign(document.createElement('a'), { href: url, download: `${d.id}-${view.value}.md` })
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div v-if="data" class="page">
    <BidSteps :current="5" :deal="data.deal.id" />
    <RouterLink :to="`/deals/${data.deal.id}`" class="back"><i class="pi pi-arrow-left" /> {{ t('deals.room') }}</RouterLink>
    <div class="page-head">
      <div>
        <h1>{{ t('docs.title') }}</h1>
        <p class="muted">{{ flag(data.bidder.country) }} {{ data.bidder.name }} ⇄ {{ flag(data.partner.country) }} {{ data.partner.name }} · {{ data.deal.lotId }} · TED {{ data.deal.tenderRef }}</p>
      </div>
      <Button v-if="data.deal.status === 'Closed'" data-ap="generate" :label="docs.length ? t('docs.regenerate') : t('docs.generate')"
        :icon="docs.length ? 'pi pi-refresh' : 'pi pi-file-edit'" :severity="docs.length ? 'secondary' : undefined" :outlined="!!docs.length"
        :loading="generating" @click="generate" />
    </div>

    <Message v-if="data.deal.status !== 'Closed'" severity="info">{{ t('docs.notClosed') }}</Message>

    <template v-if="docs.length">
      <div v-if="required.length" class="card" data-ap="certificates">
        <h2>{{ t('docs.matrixTitle') }}</h2>
        <p class="muted small">{{ t('docs.matrixHint', { n: required.length, split }) }}</p>
        <table class="checklist matrix">
          <thead>
            <tr><th>{{ t('docs.matrixNeed') }}</th><th>{{ t('docs.matrixBy') }}</th><th>{{ t('docs.matrixCert') }}</th><th>{{ t('docs.matrixWhy') }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in required" :key="c.id" :class="{ missing: !c.providedBy }">
              <td lang="de"><strong>{{ c.need }}</strong></td>
              <td>
                <template v-if="c.providedBy">
                  {{ flag(c.providedBy.country) }} {{ c.providedBy.name }}
                  <Tag v-if="c.providedBy.orgId === session.orgId" :value="t('docs.you')" severity="info" class="mini" />
                </template>
                <span v-else class="expired">{{ t('docs.matrixMissing') }}</span>
              </td>
              <td>
                <a v-if="c.providedBy" href="#" @click.prevent="selected = c.id">{{ title(c) }}</a>
                <div class="muted small">{{ c.issuer }}<template v-if="c.validUntil"> · {{ t('docs.certValid', { date: c.validUntil }) }}</template></div>
              </td>
              <td class="small">
                <Tag v-if="c.country && c.country !== 'DE'" :value="`${flag(c.country)} → 🇩🇪`" severity="success" class="mini" />
                {{ c.equivalence }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card" data-ap="checklist">
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

      <div class="viewer" data-ap="viewer">
        <div class="card doc-list">
          <button v-for="d in docs" :key="d.id" class="doc-item" :class="{ active: d.id === selected }" @click="selected = d.id">
            <i class="pi pi-file" />
            <div>
              <div class="doc-title">{{ title(d) }}</div>
              <div class="muted small">{{ Object.keys(d.body).map((l) => l.toUpperCase()).join(' · ') }}</div>
            </div>
          </button>
          <template v-if="certs.length">
            <div class="group-head">
              <strong>{{ t('docs.certificates') }}</strong>
              <span v-if="otherParty" class="muted small">{{ t('docs.certificatesHint', { partner: otherParty.name }) }}</span>
            </div>
            <button v-for="c in certs.filter((x) => !x.id.startsWith('missing-'))" :key="c.id" class="doc-item" :class="{ active: c.id === selected }" @click="selected = c.id">
              <i class="pi pi-verified" />
              <div>
                <div class="doc-title">{{ title(c) }}</div>
                <div v-if="c.providedBy" class="muted small">{{ flag(c.providedBy.country) }} {{ c.providedBy.name }}</div>
                <div class="muted small" :class="{ expired: !c.validAtDeadline }">
                  {{ !c.validAtDeadline ? t('docs.certExpired') : c.validUntil ? t('docs.certValid', { date: c.validUntil }) : t('docs.certNoExpiry') }}
                </div>
              </div>
            </button>
          </template>
        </div>
        <div v-if="doc" class="card doc-view">
          <div class="doc-toolbar">
            <span v-if="cert" class="ai-label"><i class="pi pi-info-circle" /> {{ t('docs.certDemo') }}</span>
            <span v-else class="ai-label"><i class="pi pi-sparkles" /> {{ t('docs.aiDraft') }}</span>
            <div class="tools">
              <SelectButton v-model="view" :options="views" option-label="label" option-value="value" :allow-empty="false" size="small" />
              <Button :label="t('docs.download')" icon="pi pi-download" size="small" text @click="download(doc)" />
            </div>
          </div>
          <div v-if="cert" class="cert-need">
            <strong>{{ t('docs.certRequired', { need: cert.need }) }}</strong>
            <Tag v-if="cert.demoRequirement" :value="t('docs.certDemoRequirement')" severity="secondary" />
            <div class="muted small">{{ cert.why }}</div>
            <div v-if="cert.equivalence" class="small">{{ cert.equivalence }}</div>
          </div>
          <div class="pages" :class="{ both: view === 'both' }">
            <!-- PL first, in the same order as the language switch. -->
            <!-- eslint-disable-next-line vue/no-v-html -- our own templates, rendered from deal terms -->
            <article v-if="view !== 'de'" class="paper" lang="pl" v-html="html(doc.body.pl ?? '')" />
            <!-- eslint-disable-next-line vue/no-v-html -->
            <article v-if="view !== 'pl'" class="paper" lang="de" v-html="html(doc.body.de ?? '')" />
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.back { display: inline-flex; gap: 6px; align-items: center; text-decoration: none; font-size: 14.5px; font-weight: 500; }
.small { font-size: 13px; }
.checklist { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14.5px; }
.checklist th { text-align: left; color: var(--muted); font-size: 13px; font-weight: 600; padding: 10px 12px; background: var(--ground); border-bottom: 1px solid var(--border); }
.checklist td { padding: 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
.checklist a { text-decoration: none; color: var(--text); }
.checklist a:hover { color: var(--accent); }
.checklist tr.signed td:not([rowspan]) { color: var(--good); }
.human { margin: 14px 0 0; display: flex; gap: 8px; align-items: center; font-size: 14.5px; color: var(--text); padding: 12px 14px; border: 1px dashed var(--dash); }
.human i { color: var(--accent); }
.viewer { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 900px) { .viewer { grid-template-columns: 1fr; } }
.doc-list { padding: 6px; display: flex; flex-direction: column; gap: 2px; }
.doc-item { display: flex; gap: 10px; align-items: flex-start; text-align: left; border: 0; background: none; padding: 12px; cursor: pointer; font: inherit; color: inherit; }
.doc-item:hover { background: var(--ground); }
.doc-item.active { background: var(--accent-soft); box-shadow: inset 3px 0 0 var(--accent); }
.doc-item i { color: var(--accent); margin-top: 3px; }
.doc-title { font-weight: 600; }
.group-head { display: flex; flex-direction: column; gap: 2px; padding: 16px 12px 6px; margin-top: 6px; border-top: 1px solid var(--border); }
.expired { color: var(--bad); }
.matrix td { font-size: 14px; }
.matrix a { color: var(--accent); }
.matrix tr.missing td { background: var(--warn-soft); }
.mini { font-size: 11px; padding: 1px 6px; margin-left: 4px; }
.cert-need { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; padding: 10px 12px; background: var(--ground); border: 1px solid var(--border); }
.cert-need .muted, .cert-need .small { flex-basis: 100%; }
.doc-view { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.doc-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.tools { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.pages { display: grid; gap: 16px; min-width: 0; }
/* minmax(0, 1fr): a wide table inside a page must not push the second page out of the card. */
.pages.both { grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (max-width: 1100px) { .pages.both { grid-template-columns: minmax(0, 1fr); } }
.paper { min-width: 0; overflow-x: auto; background: #fff; border: 1px solid var(--border); padding: 28px 32px; font-family: Georgia, 'Times New Roman', serif; font-size: 14.5px; line-height: 1.55; }
.paper :deep(h1) { font-size: 20px; text-align: center; margin: 8px 0 16px; letter-spacing: 0; }
.paper :deep(h2) { font-size: 15px; margin: 18px 0 6px; font-family: inherit; color: inherit; letter-spacing: 0; }
.paper :deep(blockquote) { margin: 0 0 12px; padding: 10px 12px; background: var(--warn-soft); border: 1px solid #f0d49a; font-family: 'Hanken Grotesk', sans-serif; font-size: 13px; }
.paper :deep(blockquote p) { margin: 0; }
.paper :deep(table) { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13.5px; }
.paper :deep(th), .paper :deep(td) { border: 1px solid var(--border); padding: 6px 8px; text-align: left; hyphens: auto; }
.pages.both .paper { padding: 24px 22px; font-size: 14px; }
</style>
