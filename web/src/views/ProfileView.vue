<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import DatePicker from 'primevue/datepicker'
import Tabs from 'primevue/tabs'
import TabList from 'primevue/tablist'
import Tab from 'primevue/tab'
import TabPanels from 'primevue/tabpanels'
import TabPanel from 'primevue/tabpanel'
import { api, post } from '@/lib/api'
import { flag } from '@/lib/flags'
import { session } from '@/lib/session'
import { eur, units } from '@/lib/tenders'

interface Doc { id: string; kind: string; title: string; issuer: string; country: string; issuedOn: string | null; validUntil: string | null }
interface Product { id: string; name: string; cpv: string; capacity: number; unit: string; unitPriceEur: number; leadTimeDays: number }
interface Credential { title: string; issuer: string; validUntil: string | null }
interface Staff { id: string; name: string; role: string; yearsExperience: number; education: string; languages: string[]; credentials: Credential[]; projects: string[]; availability: string }
interface Full {
  organization: { name: string; country: string; city: string; description: string; kind: string }
  profile: { documents: Doc[]; products: Product[]; staff: Staff[]; ownership: { entity: string; country: string; share: number; role: string }[] }
}

const { t } = useI18n()
const data = ref<Full>()
const tab = ref(String(useRoute().query.tab ?? 'docs'))
const adding = ref(false)
const wykaz = ref(false)
const form = ref({ kind: 'reference', title: '', issuer: '', validUntil: null as Date | null })
const kinds = ['trade-register', 'criminal-record', 'tax', 'social-security', 'iso9001', 'aqap2110', 'reference', 'insurance', 'export-licence', 'other']

async function load() {
  if (!session.orgId) return
  data.value = await api<Full>('/profile/full')
}
watch(() => session.orgId, load, { immediate: true })

const today = new Date().toISOString().slice(0, 10)
const soon = new Date(Date.now() + 60 * 864e5).toISOString().slice(0, 10)
function status(validUntil: string | null) {
  if (!validUntil) return { label: t('profile.noExpiry'), sev: 'secondary' }
  if (validUntil < today) return { label: t('profile.expired'), sev: 'danger' }
  if (validUntil < soon) return { label: t('profile.expiring'), sev: 'warn' }
  return { label: t('profile.valid'), sev: 'success' }
}
const counts = computed(() => {
  const d = data.value?.profile.documents ?? []
  return { total: d.length, warn: d.filter((x) => ['danger', 'warn'].includes(status(x.validUntil).sev)).length }
})

async function addDoc() {
  const f = form.value
  await post('/profile/documents', {
    kind: f.kind, title: f.title, issuer: f.issuer, country: data.value!.organization.country,
    issuedOn: today, validUntil: f.validUntil ? f.validUntil.toISOString().slice(0, 10) : null,
  })
  adding.value = false
  form.value = { kind: 'reference', title: '', issuer: '', validUntil: null }
  await load()
}
async function removeDoc(id: string) {
  await api(`/profile/documents/${id}`, { method: 'DELETE' })
  await load()
}
</script>

<template>
  <div v-if="data" class="page">
    <div class="page-head">
      <div>
        <h1>{{ flag(data.organization.country) }} {{ data.organization.name }}</h1>
        <p class="muted">{{ data.organization.city }}, {{ data.organization.country }} · {{ data.organization.description }}</p>
      </div>
    </div>

    <div class="tiles">
      <div class="tile"><strong>{{ counts.total }}</strong>{{ t('profile.documents') }}<span v-if="counts.warn" class="bad small">{{ t('profile.needAttention', { n: counts.warn }) }}</span></div>
      <div class="tile"><strong>{{ data.profile.products.length }}</strong>{{ t('profile.products') }}</div>
      <div class="tile"><strong>{{ data.profile.staff.length }}</strong>{{ t('profile.staff') }}</div>
      <div class="tile"><strong>{{ data.profile.ownership.at(-1)?.share ?? '—' }}%</strong>{{ data.profile.ownership.at(-1)?.entity ?? t('profile.owner') }}</div>
    </div>

    <div class="card">
      <Tabs v-model:value="tab">
        <TabList>
          <Tab value="docs"><i class="pi pi-file" /> {{ t('profile.documents') }}</Tab>
          <Tab value="products"><i class="pi pi-box" /> {{ t('profile.products') }}</Tab>
          <Tab value="staff"><i class="pi pi-users" /> {{ t('profile.staff') }}</Tab>
        </TabList>
        <TabPanels>
          <TabPanel value="docs">
            <div class="row">
              <p class="muted small">{{ t('profile.docsNote') }}</p>
              <Button :label="t('profile.addDoc')" icon="pi pi-upload" size="small" @click="adding = true" />
            </div>
            <table class="tbl">
              <thead><tr><th>{{ t('profile.doc') }}</th><th>{{ t('profile.kind') }}</th><th>{{ t('profile.issuer') }}</th><th>{{ t('profile.validUntil') }}</th><th></th><th></th></tr></thead>
              <tbody>
                <tr v-for="d in data.profile.documents" :key="d.id">
                  <td><i class="pi pi-file-pdf doc-icon" /> {{ d.title }}</td>
                  <td><Tag :value="d.kind" severity="secondary" class="mini" /></td>
                  <td class="muted">{{ flag(d.country) }} {{ d.issuer }}</td>
                  <td>{{ d.validUntil ?? '—' }}</td>
                  <td><Tag :value="status(d.validUntil).label" :severity="status(d.validUntil).sev" /></td>
                  <td><Button icon="pi pi-trash" text rounded size="small" severity="secondary" @click="removeDoc(d.id)" /></td>
                </tr>
              </tbody>
            </table>
          </TabPanel>

          <TabPanel value="products">
            <table class="tbl">
              <thead><tr><th>{{ t('profile.product') }}</th><th>CPV</th><th class="num">{{ t('profile.capacity') }}</th><th class="num">{{ t('profile.price') }}</th><th class="num">{{ t('profile.leadTime') }}</th></tr></thead>
              <tbody>
                <tr v-for="p in data.profile.products" :key="p.id">
                  <td>{{ p.name }}</td>
                  <td class="muted">{{ p.cpv }}</td>
                  <td class="num"><strong>{{ p.capacity.toLocaleString() }}</strong> {{ units[p.unit] ?? p.unit }}</td>
                  <td class="num">{{ eur(p.unitPriceEur) }}</td>
                  <td class="num">{{ t('profile.days', { n: p.leadTimeDays }) }}</td>
                </tr>
              </tbody>
            </table>
            <p class="muted small">{{ t('profile.capacityNote') }}</p>
          </TabPanel>

          <TabPanel value="staff">
            <div class="row">
              <p class="muted small">{{ t('profile.staffNote') }}</p>
              <Button v-if="data.profile.staff.length" label="Wykaz osób" icon="pi pi-list" size="small" @click="wykaz = true" />
            </div>
            <p v-if="!data.profile.staff.length" class="muted">{{ t('profile.noStaff') }}</p>
            <div class="people">
              <div v-for="s in data.profile.staff" :key="s.id" class="person">
                <div class="avatar">{{ s.name.split(' ').slice(-2).map((w) => w[0]).join('') }}</div>
                <div class="person-main">
                  <strong>{{ s.name }}</strong>
                  <div class="muted small">{{ s.role }} · {{ t('profile.years', { n: s.yearsExperience }) }} · {{ s.languages.join(', ').toUpperCase() }}</div>
                  <div class="small">{{ s.education }}</div>
                  <div class="creds">
                    <Tag v-for="c in s.credentials" :key="c.title" :severity="status(c.validUntil).sev === 'danger' ? 'danger' : 'info'"
                      :value="c.title + (c.validUntil ? ` · ${c.validUntil}` : '')" class="mini" />
                  </div>
                  <ul class="projects small"><li v-for="p in s.projects" :key="p">{{ p }}</li></ul>
                  <div class="muted small"><i class="pi pi-calendar" /> {{ t('profile.available') }}: {{ s.availability }}</div>
                </div>
              </div>
            </div>
          </TabPanel>
        </TabPanels>
      </Tabs>
    </div>

    <Dialog v-model:visible="adding" modal :header="t('profile.addDoc')" :style="{ width: '480px' }">
      <div class="form">
        <label>{{ t('profile.kind') }}<Select v-model="form.kind" :options="kinds" /></label>
        <label>{{ t('profile.doc') }}<InputText v-model="form.title" placeholder="np. Koncesja MSWiA na obrót wyrobami wojskowymi" /></label>
        <label>{{ t('profile.issuer') }}<InputText v-model="form.issuer" /></label>
        <label>{{ t('profile.validUntil') }}<DatePicker v-model="form.validUntil" date-format="yy-mm-dd" show-icon /></label>
      </div>
      <template #footer>
        <Button :label="t('profile.cancel')" text @click="adding = false" />
        <Button :label="t('profile.save')" icon="pi pi-check" :disabled="!form.title || !form.issuer" @click="addDoc" />
      </template>
    </Dialog>

    <Dialog v-model:visible="wykaz" modal header="Wykaz osób skierowanych przez wykonawcę do realizacji zamówienia" :style="{ width: '960px' }">
      <p class="muted small">{{ t('profile.wykazNote') }}</p>
      <table class="tbl wykaz">
        <thead><tr><th>Lp.</th><th>Imię i nazwisko</th><th>Zakres wykonywanych czynności</th><th>Kwalifikacje zawodowe, uprawnienia</th><th>Doświadczenie</th><th>Wykształcenie</th><th>Podstawa dysponowania</th></tr></thead>
        <tbody>
          <tr v-for="(s, i) in data.profile.staff" :key="s.id">
            <td>{{ i + 1 }}</td><td>{{ s.name }}</td><td>{{ s.role }}</td>
            <td>{{ s.credentials.map((c) => c.title).join('; ') }}</td>
            <td>{{ s.yearsExperience }} lat; {{ s.projects.join('; ') }}</td>
            <td>{{ s.education }}</td><td>umowa o pracę (zasób własny)</td>
          </tr>
        </tbody>
      </table>
    </Dialog>
  </div>
</template>

<style scoped>
.small { font-size: 12.5px; }
.bad { color: var(--bad); }
.row { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
.tile { background: var(--surface, white); border: 1px solid var(--border); border-radius: 12px; padding: 14px; display: flex; flex-direction: column; gap: 2px; font-size: 13px; color: var(--muted); }
.tile strong { font-size: 24px; color: var(--text); }
.tbl { width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 6px; }
.tbl th { text-align: left; color: var(--muted); font-weight: 500; padding: 8px; border-bottom: 1px solid var(--border); }
.tbl td { padding: 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
.num { text-align: right; }
.mini { font-size: 11px; padding: 0 6px; }
.doc-icon { color: var(--bad); margin-right: 4px; }
.people { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 12px; }
.person { display: flex; gap: 12px; border: 1px solid var(--border); border-radius: 12px; padding: 14px; }
.avatar { width: 44px; height: 44px; border-radius: 50%; background: var(--accent); color: white; display: flex; align-items: center; justify-content: center; font-weight: 600; flex: none; }
.person-main { display: flex; flex-direction: column; gap: 4px; }
.creds { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 4px; }
.projects { margin: 4px 0; padding-left: 18px; }
.form { display: flex; flex-direction: column; gap: 12px; }
.form label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); }
.wykaz { font-size: 12.5px; }
</style>
