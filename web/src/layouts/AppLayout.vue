<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Select from 'primevue/select'
import Button from 'primevue/button'
import Drawer from 'primevue/drawer'
import ActivityFeed from '@/components/ActivityFeed.vue'
import AutopilotCursor from '@/components/AutopilotCursor.vue'
import LoadingMark from '@/components/LoadingMark.vue'
import { autopilot, autopilotRunning, dismissAutopilot, resumeAutopilot, stopAutopilot } from '@/lib/autopilot'
import { useRouter } from 'vue-router'
import { api } from '@/lib/api'
import { session, type Org } from '@/lib/session'
import { flag } from '@/lib/flags'
import { languages } from '@/i18n'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const showActivity = ref(false)
const health = ref<{ ai: boolean; model: string | null } | null>(null)

const nav = computed(() => [
  {
    items: [
      { to: '/', icon: 'pi pi-home', label: t('nav.home') },
      { to: '/profile', icon: 'pi pi-building', label: t('nav.profile') },
    ],
  },
  {
    title: t('nav.groupBid'),
    items: [
      { to: '/tenders', icon: 'pi pi-search', label: t('nav.tenders') },
      { to: '/evidence', icon: 'pi pi-id-card', label: t('nav.evidence') },
      // Partners is hidden from the menu for now; the gap check and autopilot still open it.
      { to: '/deals', icon: 'pi pi-briefcase', label: t('nav.deals') },
    ],
  },
  {
    title: t('nav.groupMore'),
    items: [
      { to: '/compliance', icon: 'pi pi-shield', label: t('nav.compliance') },
      { to: '/intelligence', icon: 'pi pi-chart-line', label: t('nav.intelligence') },
      { to: '/reach', icon: 'pi pi-box', label: t('nav.reach') },
    ],
  },
])

const orgOptions = computed(() => session.orgs.map((o) => ({ ...o, label: `${flag(o.country)} ${o.name}` })))

onMounted(async () => {
  // Pick a demo run back up straight away after a refresh; the company id is already remembered.
  resumeAutopilot(router)
  session.orgs = await api<Org[]>('/orgs')
  if (!session.orgs.some((o) => o.id === session.orgId)) session.orgId = (session.orgs.find((o) => o.country === 'PL') ?? session.orgs[0])?.id ?? ''
  health.value = await api('/health')
})

watch(() => session.language, (lang) => (locale.value = lang), { immediate: true })
</script>

<template>
  <div class="shell">
    <aside class="nav">
      <div class="brand">
        <svg class="mark" width="30" height="22" viewBox="0 0 30 22" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2">
          <path d="M2 3 L15 10 L28 3" /><path d="M5 10 L15 15.5 L25 10" /><path d="M9 16.5 L15 20 L21 16.5" />
        </svg>
        <div>
          <div class="brand-name">{{ t('app.name') }}</div>
          <div class="brand-tag">{{ t('app.tagline') }}</div>
        </div>
      </div>
      <div v-for="(group, gi) in nav" :key="gi" class="nav-group">
        <div v-if="group.title" class="nav-group-title">{{ group.title }}</div>
        <RouterLink v-for="item in group.items" :key="item.to" :to="item.to" class="nav-item"
          :class="{ active: item.to === '/' ? route.path === '/' : route.path.startsWith(item.to) }">
          <i :class="item.icon" aria-hidden="true" /> {{ item.label }}
        </RouterLink>
      </div>
      <div class="nav-foot">
        <span v-if="health" class="ai-state">
          <span class="dot" :class="{ on: health.ai }" aria-hidden="true">●</span>
          {{ health.ai ? t('common.aiOn', { model: health.model }) : t('common.aiDisabled') }}
        </span>
      </div>
    </aside>
    <div class="main">
      <header class="top">
        <div class="top-field">
          <label>{{ t('session.actAs') }}</label>
          <Select v-model="session.orgId" :options="orgOptions" option-label="label" option-value="id" class="org-select" />
        </div>
        <div class="top-field">
          <label>{{ t('session.language') }}</label>
          <Select v-model="session.language" :options="languages" option-label="label" option-value="code" />
        </div>
        <Button :label="t('activity.open')" icon="pi pi-list" severity="secondary" outlined @click="showActivity = true" />
      </header>
      <div v-if="autopilot.stage" class="autopilot" :class="autopilot.stage" role="status" aria-live="polite">
        <span class="ap-mark" aria-hidden="true"><i :class="autopilotRunning() ? 'pi pi-spin pi-spinner' : autopilot.stage === 'done' ? 'pi pi-check' : 'pi pi-pause'" /></span>
        <div class="ap-text">
          <strong>{{ t('autopilot.title') }}</strong>
          <span>{{ t(`autopilot.msg.${autopilot.message}`) }}</span>
        </div>
        <Button v-if="autopilotRunning()" :label="t('autopilot.stop')" icon="pi pi-stop" size="small" severity="secondary" @click="stopAutopilot()" />
        <Button v-else :label="t('autopilot.dismiss')" size="small" text class="ap-dismiss" @click="dismissAutopilot" />
      </div>
      <main class="content">
        <LoadingMark />
        <RouterView :key="session.orgId" />
      </main>
    </div>
    <AutopilotCursor />
    <Drawer v-model:visible="showActivity" position="right" :header="t('activity.title')" class="activity-drawer">
      <ActivityFeed />
    </Drawer>
  </div>
</template>

<style scoped>
.shell { display: grid; grid-template-columns: 236px 1fr; min-height: 100vh; }
.nav { background: var(--nav); color: #f6f6f6; display: flex; flex-direction: column; gap: 2px; padding: 24px 14px; position: sticky; top: 0; height: 100vh; box-sizing: border-box; }
.brand { display: flex; gap: 10px; align-items: center; padding: 0 10px 28px; }
.mark { color: #f6f6f6; flex: none; }
.brand-name { font-weight: 700; font-size: 17px; letter-spacing: -0.02em; color: #fff; }
.brand-tag { font-size: 12px; color: var(--ink-muted); }
.nav-item { display: flex; gap: 10px; align-items: center; padding: 10px 12px; color: var(--ink-text); text-decoration: none; font-size: 15px; }
.nav-item:hover { color: #fff; background: #232a2e; }
.nav-item i { font-size: 15px; width: 18px; color: var(--ink-muted); }
.nav-item.active i { color: #fff; }
.nav-group { display: flex; flex-direction: column; gap: 2px; }
.nav-group + .nav-group { margin-top: 14px; }
.nav-group-title { font-size: 12px; font-weight: 600; color: var(--ink-muted); padding: 0 12px 6px; }
.nav-item.active { background: var(--accent); color: #fff; font-weight: 600; clip-path: polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%); }
.nav-foot { margin-top: auto; padding: 20px 12px 0; border-top: 1px dashed #3a4044; font-size: 12.5px; color: var(--ink-muted); }
.ai-state { display: flex; gap: 6px; }
.dot { color: var(--ink-muted); }
.dot.on { color: var(--accent-on-dark); }
.main { display: flex; flex-direction: column; min-width: 0; }
.autopilot { position: sticky; top: 0; z-index: 5; display: flex; gap: 14px; align-items: center; padding: 12px 36px; background: var(--ink); color: #f6f6f6; flex-wrap: wrap; }
.autopilot.stopped { background: #3a2a00; }
.ap-mark { width: 30px; height: 30px; display: grid; place-items: center; background: var(--accent); clip-path: var(--chamfer-sm); flex: none; }
.autopilot.done .ap-mark { background: var(--good); }
.autopilot.stopped .ap-mark { background: #b07a00; }
.ap-text { flex: 1 1 320px; display: flex; gap: 10px; flex-wrap: wrap; align-items: baseline; font-size: 15px; }
.ap-text span { color: var(--ink-text); }
.ap-dismiss { color: var(--ink-text) !important; }
.top { display: flex; gap: 16px; align-items: flex-end; justify-content: flex-end; padding: 14px 36px; background: var(--surface); border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.top-field { display: flex; flex-direction: column; gap: 4px; }
.top-field label { font-size: 13px; font-weight: 500; color: var(--muted); }
.org-select { min-width: 280px; }
.content { position: relative; min-height: 60vh; padding: 36px; max-width: 1280px; width: 100%; box-sizing: border-box; }
@media (max-width: 800px) {
  .shell { grid-template-columns: 1fr; }
  .nav { position: static; height: auto; flex-direction: row; flex-wrap: wrap; }
  .brand, .nav-foot, .nav-group-title { display: none; }
  .nav-group { flex-direction: row; flex-wrap: wrap; }
  .nav-group + .nav-group { margin-top: 0; }
}
</style>
<style>
.activity-drawer { width: 420px !important; }
</style>
