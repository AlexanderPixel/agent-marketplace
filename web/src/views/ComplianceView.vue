<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import { api, post } from '@/lib/api'
import { flag } from '@/lib/flags'
import { session } from '@/lib/session'
import { eur } from '@/lib/tenders'

interface Alt { supplier: string; country: string; costEur: number; note: string }
interface Report {
  threshold: number; product: string | null; euContent: number; safeEligible: boolean; contentOk: boolean; controlOk: boolean
  components: { component: { id: string; name: string; supplier: string; country: string; costEur: number }; eligible: boolean; share: number }[]
  swaps: { componentId: string; component: string; from: string; fromCountry: string; to: Alt; costDelta: number; newEuContent: number; reachesThreshold: boolean }[]
  control: { company: string; country: string; role: string; chain: { entity: string; country: string; share: number; role: string }[]; euControlled: boolean; detail: string }[]
  sanctions: { name: string; country: string; hit: boolean; detail: string }[]
}

const { t } = useI18n()
const report = ref<Report>()
const applying = ref<string>()
const flash = ref(false)

async function load() {
  if (!session.orgId) return
  report.value = await api<Report>('/compliance')
}
watch(() => session.orgId, load, { immediate: true })

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

    <Message v-if="!report.product" severity="info">{{ t('compliance.noBom') }}</Message>

    <template v-else>
      <div class="card hero" :class="{ ok: report.safeEligible, flash }">
        <div class="badge-seal" :class="{ ok: report.safeEligible }">
          <span class="stars">★ ★ ★</span>
          <strong>SAFE</strong><strong>EDIP</strong>
          <i :class="report.safeEligible ? 'pi pi-check' : 'pi pi-times'" />
        </div>
        <div class="hero-main">
          <div class="hero-title">{{ report.safeEligible ? t('compliance.eligible') : t('compliance.notEligible') }}</div>
          <div class="muted">{{ report.product }}</div>
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
        <h2>{{ t('compliance.bom') }}</h2>
        <table class="bom">
          <thead><tr><th>{{ t('compliance.component') }}</th><th>{{ t('compliance.supplier') }}</th><th class="num">{{ t('compliance.cost') }}</th><th>{{ t('compliance.share') }}</th></tr></thead>
          <tbody>
            <tr v-for="c in report.components" :key="c.component.id">
              <td>{{ c.component.name }}</td>
              <td>{{ flag(c.component.country) }} {{ c.component.supplier }} <Tag :value="c.eligible ? 'EU' : c.component.country" :severity="c.eligible ? 'success' : 'danger'" class="mini" /></td>
              <td class="num">{{ eur(c.component.costEur) }}</td>
              <td><div class="share"><div :class="c.eligible ? 'eu' : 'non'" :style="{ width: `${c.share * 2.5}%` }" /><span>{{ c.share }}%</span></div></td>
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
  </div>
</template>

<style scoped>
.small { font-size: 12.5px; }
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
.share div { height: 8px; border-radius: 4px; }
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
