<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Select from 'primevue/select'
import Button from 'primevue/button'
import Drawer from 'primevue/drawer'
import ActivityFeed from '@/components/ActivityFeed.vue'
import { api } from '@/lib/api'
import { session, type Org } from '@/lib/session'
import { flag } from '@/lib/flags'
import { languages } from '@/i18n'

const { t, locale } = useI18n()
const showActivity = ref(false)
const health = ref<{ ai: boolean; model: string | null } | null>(null)

const nav = computed(() => [
  { to: '/', icon: 'pi pi-home', label: t('nav.home') },
  { to: '/tenders', icon: 'pi pi-search', label: t('nav.tenders') },
  { to: '/evidence', icon: 'pi pi-id-card', label: t('nav.evidence') },
  { to: '/partners', icon: 'pi pi-users', label: t('nav.partners') },
  { to: '/deals', icon: 'pi pi-briefcase', label: t('nav.deals') },
  { to: '/compliance', icon: 'pi pi-shield', label: t('nav.compliance') },
  { to: '/intelligence', icon: 'pi pi-chart-line', label: t('nav.intelligence') },
  { to: '/reach', icon: 'pi pi-box', label: t('nav.reach') },
])

const orgOptions = computed(() => session.orgs.map((o) => ({ ...o, label: `${flag(o.country)} ${o.name}` })))

onMounted(async () => {
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
        <span class="stars">★</span>
        <div>
          <div class="brand-name">{{ t('app.name') }}</div>
          <div class="brand-tag">{{ t('app.tagline') }}</div>
        </div>
      </div>
      <RouterLink v-for="item in nav" :key="item.to" :to="item.to" class="nav-item" active-class="" exact-active-class="active">
        <i :class="item.icon" /> {{ item.label }}
      </RouterLink>
      <div class="nav-foot">
        <span v-if="health" class="ai-state">
          <i class="pi pi-sparkles" />
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
      <main class="content">
        <RouterView :key="session.orgId" />
      </main>
    </div>
    <Drawer v-model:visible="showActivity" position="right" :header="t('activity.title')" class="activity-drawer">
      <ActivityFeed />
    </Drawer>
  </div>
</template>

<style scoped>
.shell { display: grid; grid-template-columns: 240px 1fr; min-height: 100vh; }
.nav { background: var(--nav); color: #dfe6f5; display: flex; flex-direction: column; gap: 2px; padding: 18px 12px; position: sticky; top: 0; height: 100vh; box-sizing: border-box; }
.brand { display: flex; gap: 10px; align-items: center; padding: 4px 8px 18px; }
.stars { color: var(--eu-gold); font-size: 26px; }
.brand-name { font-weight: 600; color: #fff; }
.brand-tag { font-size: 12px; color: #9fb0d3; }
.nav-item { display: flex; gap: 10px; align-items: center; padding: 9px 10px; border-radius: 8px; color: #dfe6f5; text-decoration: none; font-size: 14px; }
.nav-item:hover { background: #1b2a4d; }
.nav-item.active { background: #24386a; color: #fff; }
.nav-foot { margin-top: auto; font-size: 12px; color: #9fb0d3; padding: 8px; }
.ai-state { display: flex; gap: 6px; }
.main { display: flex; flex-direction: column; min-width: 0; }
.top { display: flex; gap: 16px; align-items: flex-end; justify-content: flex-end; padding: 14px 28px; background: var(--surface); border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.top-field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.org-select { min-width: 280px; }
.content { padding: 28px; max-width: 1280px; width: 100%; box-sizing: border-box; }
@media (max-width: 800px) {
  .shell { grid-template-columns: 1fr; }
  .nav { position: static; height: auto; flex-direction: row; flex-wrap: wrap; }
  .brand, .nav-foot { display: none; }
}
</style>
<style>
.activity-drawer { width: 420px !important; }
</style>
