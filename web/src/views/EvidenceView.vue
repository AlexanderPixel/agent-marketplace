<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import SelectButton from 'primevue/selectbutton'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import EcertisBox from '@/components/EcertisBox.vue'
import { api } from '@/lib/api'
import { flag } from '@/lib/flags'
import { currentOrg, session } from '@/lib/session'
import type { CompanyDocument, EcertisMatch } from '@/lib/tenders'

const { t } = useI18n()
const rows = ref<{ document: CompanyDocument; ecertis: EcertisMatch | null }[]>([])
const buyerCountry = ref('DE')
const options = computed(() =>
  ['DE', 'PL'].filter((c) => c !== currentOrg()?.country).map((c) => ({ label: `${flag(c)} ${c}`, value: c })),
)

async function load() {
  if (!session.orgId) return
  if (buyerCountry.value === currentOrg()?.country) buyerCountry.value = options.value[0]?.value ?? 'DE'
  rows.value = await api(`/evidence?buyerCountry=${buyerCountry.value}`)
}
watch([() => session.orgId, buyerCountry, () => session.orgs.length], load, { immediate: true })
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ t('evidence.title') }}</h1>
        <p class="muted">{{ t('evidence.subtitle') }}</p>
      </div>
      <div class="country">
        <span class="muted">{{ t('evidence.country') }}</span>
        <SelectButton v-model="buyerCountry" :options="options" option-label="label" option-value="value" :allow-empty="false" />
      </div>
    </div>

    <Message v-if="buyerCountry === 'DE'" severity="warn" icon="pi pi-exclamation-triangle">
      <strong>{{ t('evidence.gapTitle') }}.</strong> {{ t('evidence.gapText') }}
    </Message>

    <div class="list">
      <div v-for="r in rows" :key="r.document.id" class="card doc" :class="{ mapped: r.ecertis }">
        <div class="doc-head">
          <span class="doc-flag">{{ flag(r.document.country) }}</span>
          <div class="doc-main">
            <div class="doc-title">{{ r.document.title }}</div>
            <div class="muted">{{ r.document.issuer }}<span v-if="r.document.validUntil"> · {{ r.document.validUntil }}</span></div>
          </div>
          <Tag :value="r.document.kind" severity="secondary" />
        </div>
        <EcertisBox v-if="r.ecertis" :match="r.ecertis" :buyer-country="buyerCountry" />
        <p v-else class="muted small">{{ t('evidence.noMapping') }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.country { display: flex; gap: 10px; align-items: center; }
.list { display: flex; flex-direction: column; gap: 12px; }
.doc { padding: 14px 18px; }
.doc:not(.mapped) { opacity: 0.75; }
.doc-head { display: flex; gap: 12px; align-items: center; }
.doc-flag { font-size: 26px; }
.doc-main { flex: 1; }
.doc-title { font-weight: 600; }
.small { margin: 6px 0 0; font-size: 13px; }
</style>
