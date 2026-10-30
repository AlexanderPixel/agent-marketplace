<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import { api, post } from '@/lib/api'
import { flag } from '@/lib/flags'
import { session } from '@/lib/session'

type Risk = 'Clear' | 'Notify' | 'Restricted' | 'Unknown'
interface Finding { substance: string; cas: string | null; percentWw: number | null; list: string; risk: Risk; action: string }
interface Scan { component: { id: string; name: string; supplier: string; country: string }; declaration: string | null; risk: Risk; findings: Finding[]; pfasFreeAlternative: string | null }
interface PackItem { group: string; item: string; status: 'ready' | 'warning' | 'missing'; detail: string; source: string | null }
interface Pack { company: string; items: PackItem[]; ready: number; lenders: { name: string; fit: string }[] }

const { t } = useI18n()
const scan = ref<{ product: string; scan: Scan[] }>()
const pack = ref<Pack>()
const scanning = ref(false)

async function load() {
  if (!session.orgId) return
  scan.value = undefined
  pack.value = await api<Pack>('/financing')
}
watch(() => session.orgId, load, { immediate: true })

async function runScan() {
  scanning.value = true
  try {
    await new Promise((r) => setTimeout(r, 700))
    scan.value = await post('/reach/scan')
    pack.value = await api<Pack>('/financing')
  } finally {
    scanning.value = false
  }
}

const severity = (r: Risk) => ({ Clear: 'success', Notify: 'warn', Restricted: 'danger', Unknown: 'danger' })[r]
const counts = computed(() => {
  const s = scan.value?.scan ?? []
  return {
    red: s.filter((x) => x.risk === 'Restricted' || x.risk === 'Unknown').length,
    notify: s.filter((x) => x.risk === 'Notify').length,
    clear: s.filter((x) => x.risk === 'Clear').length,
    pfas: s.filter((x) => x.findings.some((f) => f.list.includes('PFAS'))).length,
  }
})
const groups = computed(() => {
  const g = new Map<string, PackItem[]>()
  for (const i of pack.value?.items ?? []) g.set(i.group, [...(g.get(i.group) ?? []), i])
  return [...g.entries()]
})
const icon = (s: PackItem['status']) => ({ ready: 'pi pi-check-circle good', warning: 'pi pi-exclamation-triangle warn', missing: 'pi pi-circle bad' })[s]
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ t('reach.title') }}</h1>
        <p class="muted">{{ t('reach.subtitle') }}</p>
      </div>
    </div>

    <div class="card">
      <div class="row">
        <h2>{{ t('reach.scanTitle') }}</h2>
        <Button :label="scan ? t('reach.rescan') : t('reach.scan')" icon="pi pi-search" :loading="scanning" @click="runScan" />
      </div>
      <p class="muted small">{{ t('reach.scanNote') }}</p>

      <template v-if="scan">
        <div class="stats">
          <div class="stat red"><strong>{{ counts.red }}</strong>{{ t('reach.red') }}</div>
          <div class="stat amber"><strong>{{ counts.notify }}</strong>{{ t('reach.notify') }}</div>
          <div class="stat green"><strong>{{ counts.clear }}</strong>{{ t('reach.clear') }}</div>
          <div class="stat pfas"><strong>{{ counts.pfas }}</strong>PFAS</div>
        </div>
        <div v-for="s in scan.scan" :key="s.component.id" class="comp" :class="s.risk.toLowerCase()">
          <div class="comp-head">
            <strong>{{ s.component.name }}</strong>
            <span class="muted">{{ flag(s.component.country) }} {{ s.component.supplier }}</span>
            <Tag :value="t(`reach.risk.${s.risk}`)" :severity="severity(s.risk)" />
          </div>
          <div class="muted small">{{ s.declaration ?? t('reach.noDeclaration') }}</div>
          <div v-for="f in s.findings" :key="f.substance" class="finding">
            <i class="pi pi-flag" />
            <div>
              <div><strong>{{ f.substance }}</strong> <span v-if="f.cas" class="muted">CAS {{ f.cas }}</span> <span v-if="f.percentWw != null" class="muted">· {{ f.percentWw }}% w/w</span> <em v-if="f.list !== '—'">· {{ f.list }}</em></div>
              <div class="small">{{ f.action }}</div>
            </div>
          </div>
          <div v-if="s.pfasFreeAlternative" class="alt"><i class="pi pi-sparkles" /> {{ t('reach.pfasFree', { s: s.pfasFreeAlternative }) }}</div>
        </div>
      </template>
    </div>

    <div v-if="pack" class="card">
      <div class="row">
        <h2>{{ t('reach.packTitle') }}</h2>
        <Tag :value="t('reach.readyCount', { n: pack.ready, total: pack.items.length })" :severity="pack.ready === pack.items.length ? 'success' : 'warn'" />
      </div>
      <p class="muted small">{{ t('reach.packNote', { c: pack.company }) }}</p>
      <div class="progress"><div :style="{ width: `${(pack.ready / pack.items.length) * 100}%` }" /></div>
      <div class="pack">
        <div v-for="[g, items] in groups" :key="g" class="group">
          <h3>{{ g }}</h3>
          <div v-for="i in items" :key="i.item" class="item">
            <i :class="icon(i.status)" />
            <div>
              <div>{{ i.item }} <Tag v-if="i.source" :value="i.source" severity="secondary" class="mini" /></div>
              <div class="muted small">{{ i.detail }}</div>
            </div>
          </div>
        </div>
      </div>
      <h3>{{ t('reach.lenders') }}</h3>
      <div class="lenders">
        <div v-for="l in pack.lenders" :key="l.name" class="lender"><strong>{{ l.name }}</strong><span class="muted small">{{ l.fit }}</span></div>
      </div>
      <p class="muted small">{{ t('reach.draftNote') }}</p>
    </div>
  </div>
</template>

<style scoped>
.small { font-size: 12.5px; }
.good { color: var(--good); }
.bad { color: var(--bad); }
.warn { color: #c77700; }
.row { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 14px 0; }
.stat { border-radius: 10px; padding: 12px; display: flex; flex-direction: column; font-size: 13px; }
.stat strong { font-size: 26px; }
.stat.red { background: var(--bad-soft); color: var(--bad); }
.stat.amber { background: #fff4e0; color: #a35f00; }
.stat.green { background: var(--good-soft); color: var(--good); }
.stat.pfas { background: #eef1fb; color: var(--accent); }
.comp { border: 1px solid var(--border); border-left: 5px solid var(--good); border-radius: 10px; padding: 10px 14px; margin-top: 8px; }
.comp.notify { border-left-color: #e69500; }
.comp.restricted, .comp.unknown { border-left-color: var(--bad); }
.comp-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.comp-head .p-tag { margin-left: auto; }
.finding { display: flex; gap: 10px; margin-top: 8px; font-size: 14px; }
.finding i { color: var(--bad); margin-top: 3px; }
.alt { margin-top: 8px; padding: 6px 10px; border-radius: 8px; background: var(--good-soft); font-size: 13.5px; }
.progress { height: 10px; background: var(--border); border-radius: 5px; margin: 10px 0 16px; }
.progress div { height: 100%; background: var(--accent); border-radius: 5px; transition: width 0.6s; }
.pack { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.group h3 { margin: 0 0 6px; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); }
.item { display: flex; gap: 10px; padding: 6px 0; font-size: 14px; }
.item i { margin-top: 3px; }
.mini { font-size: 11px; padding: 0 6px; margin-left: 6px; }
.lenders { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; margin-bottom: 10px; }
.lender { border: 1px solid var(--border); border-radius: 10px; padding: 10px; display: flex; flex-direction: column; gap: 4px; }
</style>
