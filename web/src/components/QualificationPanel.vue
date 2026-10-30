<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import ActivityFeed from '@/components/ActivityFeed.vue'
import EcertisBox from '@/components/EcertisBox.vue'
import { post } from '@/lib/api'
import { session } from '@/lib/session'
import {
  criteriaNames, outcomeIcon, outcomeSeverity, units, verdictIcon, verdictSeverity, type Analysis, type Tender,
} from '@/lib/tenders'

const props = defineProps<{ tender: Tender }>()
const { t } = useI18n()
const analysis = ref<Analysis>()
const running = ref(false)

async function run() {
  running.value = true
  try {
    analysis.value = await post<Analysis>(`/tenders/${encodeURIComponent(props.tender.id)}/analysis`)
  } finally {
    running.value = false
  }
}

// A different company or language means a different answer.
watch(() => [session.orgId, session.language, props.tender.id], () => (analysis.value = undefined))

const unitName = (u: string | undefined | null) => (u ? (units[u] ?? u) : '')
</script>

<template>
  <div class="qual">
    <div class="main">
      <div v-if="!analysis" class="card start">
        <div>
          <h2>{{ t('qual.run') }}</h2>
          <p class="muted">{{ t('qual.runHint') }}</p>
        </div>
        <Button class="run" :label="t('qual.run')" icon="pi pi-bolt" :loading="running" @click="run" />
      </div>

      <template v-else>
        <div class="card verdict" :class="analysis.verdict">
          <i :class="verdictIcon[analysis.verdict]" />
          <div class="verdict-text">
            <div class="verdict-label">{{ t(`qual.verdict.${analysis.verdict}`) }}</div>
            <div class="muted">{{ t(`qual.verdictHint.${analysis.verdict}`) }}</div>
          </div>
          <Button :label="t('qual.rerun')" icon="pi pi-refresh" size="small" text :loading="running" @click="run" />
        </div>

        <div class="card">
          <div class="section-head">
            <h2>{{ t('qual.summary') }}</h2>
            <span class="ai-label"><i :class="analysis.summary.byAi ? 'pi pi-sparkles' : 'pi pi-list'" />
              {{ analysis.summary.byAi ? t('qual.byAi') : t('qual.byTemplate') }}</span>
          </div>
          <p class="headline">{{ analysis.summary.headline }}</p>
          <ul class="points">
            <li v-for="(p, i) in analysis.summary.points" :key="i">
              {{ p.text }} <span class="cite-chip" :title="p.cite"><i class="pi pi-bookmark" /> {{ p.cite }}</span>
            </li>
          </ul>
        </div>

        <div class="card">
          <h2>{{ t('qual.requirements') }}</h2>
          <div v-for="(r, i) in analysis.requirements" :key="i" class="req">
            <Tag :severity="outcomeSeverity[r.outcome]" :icon="outcomeIcon[r.outcome]" :value="t(`qual.outcome.${r.outcome}`)" class="outcome" />
            <div class="req-body">
              <div class="req-title">
                <span v-if="r.requirement.code" class="muted">{{ criteriaNames[r.requirement.code] ?? r.requirement.code }} · </span>
                <span :lang="r.requirement.language">{{ r.requirement.text.length > 260 ? r.requirement.text.slice(0, 260) + '…' : r.requirement.text }}</span>
              </div>
              <div class="cite"><i class="pi pi-bookmark" /> {{ r.requirement.source }}</div>
              <div v-for="(c, j) in r.checks" :key="j" class="check">
                <i :class="outcomeIcon[c.outcome]" :style="{ color: `var(--${c.outcome === 'Met' ? 'good' : c.outcome === 'Equivalent' ? 'warn' : 'bad'})` }" />
                <div>
                  <strong>{{ c.need }}.</strong> {{ c.reason }}
                  <div v-if="c.evidence" class="evidence">
                    <i class="pi pi-file" /> {{ c.outcome === 'Met' || c.outcome === 'Equivalent' ? t('qual.evidence') : t('qual.closest') }}:
                    <strong>{{ c.evidence.title }}</strong> · {{ c.evidence.issuer }}
                    <span v-if="c.evidence.validUntil" class="muted"> · {{ c.evidence.validUntil }}</span>
                  </div>
                  <div v-if="c.alsoSubmit?.length" class="evidence">
                    <i class="pi pi-plus" /> {{ t('evidence.alsoSubmit') }}: <strong>{{ c.alsoSubmit.map((d) => d.title).join(', ') }}</strong>
                  </div>
                  <EcertisBox v-if="c.ecertis" :match="c.ecertis" :buyer-country="tender.buyerCountry" :clarification="c.clarification" />
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="analysis.lots.length" class="card">
          <h2>{{ t('qual.capacity') }}</h2>
          <div v-for="l in analysis.lots" :key="l.lot.id" class="lot">
            <div class="lot-main">
              <div><strong>{{ l.lot.id }}</strong> · {{ l.lot.title }}</div>
              <div class="muted">{{ l.product?.name }}</div>
              <div class="bar">
                <div class="bar-fill" :style="{ width: `${Math.min(100, ((l.capacity ?? 0) / (l.lot.quantity ?? 1)) * 100)}%` }" />
              </div>
              <div class="bar-legend">
                <span>{{ t('qual.capacityOf', { capacity: l.capacity, needed: `${l.lot.quantity} ${unitName(l.lot.unit)}` }) }}</span>
                <span v-if="l.gap" class="gap">{{ t('qual.short', { gap: `${l.gap} ${unitName(l.lot.unit)}` }) }}</span>
              </div>
            </div>
            <Button v-if="l.gap" v-tooltip="t('qual.soon')" :label="t('qual.findPartner', { gap: l.gap, unit: unitName(l.lot.unit) })"
              icon="pi pi-users" disabled />
          </div>
          <p v-if="analysis.notOffered" class="muted">{{ t('qual.notOffered', { n: analysis.notOffered }) }}</p>
        </div>
      </template>
    </div>

    <div class="card side">
      <h2>{{ t('qual.activity') }}</h2>
      <ActivityFeed :subject="tender.id" />
    </div>
  </div>
</template>

<style scoped>
.qual { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; align-items: start; }
@media (max-width: 1100px) { .qual { grid-template-columns: 1fr; } }
.main { display: flex; flex-direction: column; gap: 16px; }
.start { display: flex; justify-content: space-between; align-items: center; gap: 16px; background: linear-gradient(90deg, var(--accent-soft), var(--surface)); }
.start p { margin: 4px 0 0; }
.verdict { display: flex; gap: 16px; align-items: center; border-left: 6px solid var(--muted); }
.verdict > i { font-size: 30px; }
.verdict-text { flex: 1; }
.verdict-label { font-size: 20px; font-weight: 600; }
.verdict.can-supply { border-left-color: var(--good); background: var(--good-soft); }
.verdict.can-supply > i { color: var(--good); }
.verdict.partner-needed { border-left-color: var(--accent); background: var(--accent-soft); }
.verdict.partner-needed > i { color: var(--accent); }
.verdict.not-yet { border-left-color: var(--bad); background: var(--bad-soft); }
.verdict.not-yet > i { color: var(--bad); }
.section-head { display: flex; justify-content: space-between; align-items: center; }
.headline { font-weight: 600; margin: 12px 0 6px; }
.points { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 6px; font-size: 14.5px; }
.cite-chip { display: inline-flex; gap: 4px; align-items: center; font-size: 11.5px; color: var(--accent); background: var(--accent-soft); border-radius: 6px; padding: 1px 6px; margin-left: 4px; white-space: nowrap; }
.cite-chip i { font-size: 10px; }
.req { display: flex; gap: 14px; padding: 14px 0; border-bottom: 1px solid var(--border); }
.req:last-child { border-bottom: 0; }
.outcome { min-width: 112px; align-self: flex-start; }
.req-body { flex: 1; display: flex; flex-direction: column; gap: 6px; font-size: 14.5px; }
.cite { font-size: 12px; color: var(--muted); }
.check { display: flex; gap: 8px; font-size: 14px; }
.check > i { margin-top: 3px; }
.check > div { flex: 1; }
.evidence { margin-top: 4px; font-size: 13px; }
.lot { display: flex; gap: 16px; align-items: center; padding: 12px 0; flex-wrap: wrap; }
.lot-main { flex: 1 1 300px; display: flex; flex-direction: column; gap: 4px; }
.bar { height: 10px; background: var(--bad-soft); border-radius: 6px; overflow: hidden; margin-top: 6px; }
.bar-fill { height: 100%; background: var(--good); }
.bar-legend { display: flex; justify-content: space-between; font-size: 13px; }
.gap { color: var(--bad); font-weight: 600; }
.side { position: sticky; top: 16px; max-height: calc(100vh - 32px); overflow: auto; }
.side h2 { margin-bottom: 8px; }
</style>
