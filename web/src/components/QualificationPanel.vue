<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import ActivityFeed from '@/components/ActivityFeed.vue'
import EcertisBox from '@/components/EcertisBox.vue'
import { post } from '@/lib/api'
import { autopilotAt, beat, pointAt, showSection } from '@/lib/autopilot'
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

// Autopilot, check step: show the same check the server ran, and point at the gap. The server moves on by itself.
onMounted(async () => {
  if (!autopilotAt('check')) return
  await beat(600)
  await pointAt('[data-ap="check"]', { click: true })
  if (!autopilotAt('check')) return
  await run()
  await nextTick()
  await showSection('[data-ap="verdict"]', 'start')
  await beat(1500)
  await pointAt('[data-ap="find-partner"]')
})
</script>

<template>
  <div class="qual">
    <div class="main">
      <div v-if="!analysis" class="card start">
        <div>
          <h2>{{ t('qual.run') }}</h2>
          <p class="muted">{{ t('qual.runHint') }}</p>
        </div>
        <Button class="run" data-ap="check" :label="t('qual.run')" icon="pi pi-bolt" :loading="running" @click="run" />
      </div>

      <template v-else>
        <div class="ink-panel verdict" data-ap="verdict" :class="analysis.verdict">
          <div class="verdict-head">
            <div class="verdict-text">
              <h2><span class="idx">01</span> {{ t('qual.column') }}</h2>
              <div class="verdict-label"><i :class="verdictIcon[analysis.verdict]" /> {{ t(`qual.verdict.${analysis.verdict}`) }}</div>
              <div class="muted">{{ t(`qual.verdictHint.${analysis.verdict}`) }}</div>
            </div>
            <Button :label="t('qual.rerun')" icon="pi pi-refresh" size="small" text class="rerun" :loading="running" @click="run" />
          </div>

          <div v-for="l in analysis.lots" :key="l.lot.id" class="lot">
            <div class="figures">
              <div class="fig">
                <div class="fig-label">{{ l.lot.id }} · {{ l.lot.title }}</div>
                <div class="fig-num">{{ l.lot.quantity }} <small>{{ unitName(l.lot.unit) }}</small></div>
                <div class="fig-note">{{ l.lot.place }}</div>
              </div>
              <div class="fig">
                <div class="fig-label">{{ t('qual.capacity') }}</div>
                <div class="fig-num yours">{{ l.capacity }}</div>
                <div class="fig-note">{{ l.product?.name }}</div>
              </div>
              <div v-if="l.gap" class="fig gap-fig">
                <div class="fig-label">{{ t('qual.short', { gap: '' }).trim() }}</div>
                <div class="fig-num">{{ l.gap }}</div>
                <RouterLink :to="{ path: '/partners', query: { tender: tender.id, lot: l.lot.id, gap: l.gap } }">
                  <Button data-ap="find-partner" :label="t('qual.findPartner', { gap: l.gap, unit: unitName(l.lot.unit) })" icon="pi pi-users" />
                </RouterLink>
              </div>
            </div>
            <div class="bar" role="img" :aria-label="t('qual.capacityOf', { capacity: l.capacity, needed: `${l.lot.quantity} ${unitName(l.lot.unit)}` })">
              <div class="bar-fill" :style="{ flexGrow: Math.min(l.capacity ?? 0, l.lot.quantity ?? 0) }" />
              <div class="bar-gap" :style="{ flexGrow: l.gap ?? 0 }" />
            </div>
          </div>
          <p v-if="analysis.notOffered" class="muted">{{ t('qual.notOffered', { n: analysis.notOffered }) }}</p>
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
.start { display: flex; justify-content: space-between; align-items: center; gap: 16px; border: 1px dashed var(--dash); }
.start p { margin: 6px 0 0; }
.verdict { display: flex; flex-direction: column; gap: 24px; }
.verdict-head { display: flex; gap: 16px; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; }
.verdict-text { display: flex; flex-direction: column; gap: 8px; max-width: 60ch; }
.idx { color: var(--accent-on-dark); }
.verdict-label { font-size: 30px; font-weight: 500; letter-spacing: -0.03em; display: flex; gap: 12px; align-items: center; }
.verdict-label i { font-size: 22px; color: var(--accent-on-dark); }
.verdict.can-supply .verdict-label i { color: #6fd39b; }
.verdict.not-yet .verdict-label i, .verdict.out-of-range .verdict-label i { color: #ff8a80; }
.rerun { color: var(--ink-text) !important; }
.lot { display: flex; flex-direction: column; gap: 14px; }
.figures { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); border: 1px dashed var(--ink-line); }
.fig { padding: 20px 22px; display: flex; flex-direction: column; gap: 8px; border-right: 1px dashed var(--ink-line); }
.fig:last-child { border-right: 0; }
.fig-label { font-size: 13px; font-weight: 500; color: var(--ink-muted); }
.fig-num { font-size: 56px; line-height: 1; letter-spacing: -0.05em; font-weight: 500; }
.fig-num small { font-size: 18px; letter-spacing: 0; color: var(--ink-muted); }
.fig-num.yours { color: var(--accent-on-dark); }
.fig-note { font-size: 14px; color: var(--ink-text); }
.gap-fig { background: repeating-linear-gradient(135deg, rgba(110, 155, 255, 0.1) 0 8px, transparent 8px 16px); }
.gap-fig a { margin-top: 4px; }
.section-head { display: flex; justify-content: space-between; align-items: center; }
.headline { font-weight: 600; margin: 12px 0 6px; }
.points { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 6px; font-size: 14.5px; }
.cite-chip { display: inline-flex; gap: 4px; align-items: center; font-size: 12px; color: var(--accent); background: var(--accent-soft); padding: 1px 6px; margin-left: 4px; white-space: nowrap; }
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
.bar { display: flex; height: 14px; border: 1px solid var(--ink-line); }
.bar-fill { flex-basis: 0; background: var(--accent-on-dark); }
.bar-gap { flex-basis: 0; background: repeating-linear-gradient(135deg, var(--accent-on-dark) 0 2px, transparent 2px 9px); }
.side { position: sticky; top: 16px; max-height: calc(100vh - 32px); overflow: auto; }
.side h2 { margin-bottom: 8px; }
</style>
