<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import { flag } from '@/lib/flags'
import type { EcertisMatch } from '@/lib/tenders'

defineProps<{ match: EcertisMatch; buyerCountry: string; clarification?: { language: string; text: string; translation: string } | null }>()
const { t } = useI18n()
const copied = ref(false)
async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    // Clipboard blocked: the text stays visible to select by hand.
  }
}
</script>

<template>
  <div class="ecertis">
    <div class="ecertis-head">
      <span class="badge">eCertis</span>
      <span>{{ t('evidence.criterion') }}: <strong>{{ match.criterionName }}</strong></span>
      <a :href="match.url" target="_blank" rel="noopener" class="open">{{ t('evidence.open') }} <i class="pi pi-external-link" /></a>
    </div>
    <p v-if="match.note" class="note"><i class="pi pi-info-circle" /> {{ match.note }}</p>
    <div class="cols">
      <div>
        <div class="col-title">{{ flag(buyerCountry) }} {{ t('evidence.accepted', { country: buyerCountry }) }}</div>
        <ul>
          <li v-for="e in match.buyerCountry.slice(0, 8)" :key="e.name">
            <a v-if="e.url" :href="e.url" target="_blank" rel="noopener">{{ e.name }}</a><span v-else>{{ e.name }}</span>
            <span v-if="e.issuer" class="muted"> · {{ e.issuer }}</span>
          </li>
        </ul>
      </div>
      <div>
        <div class="col-title">{{ flag(match.matched?.country ?? match.companyCountry[0]?.country ?? '') }} {{ t('evidence.yourEntry') }}</div>
        <ul>
          <li v-for="e in match.companyCountry" :key="e.name" :class="{ matched: e.name === match.matched?.name }">
            <i v-if="e.name === match.matched?.name" class="pi pi-check" />
            <a v-if="e.url" :href="e.url" target="_blank" rel="noopener">{{ e.name }}</a><span v-else>{{ e.name }}</span>
            <span v-if="e.validity" class="muted"> · {{ t('evidence.validity', { v: e.validity }) }}</span>
          </li>
        </ul>
      </div>
    </div>
    <div v-if="clarification" class="clarification">
      <div class="col-title"><i class="pi pi-envelope" /> {{ t('evidence.clarification') }}
        <span class="ai-label">· {{ t('evidence.humanSends') }}</span></div>
      <blockquote :lang="clarification.language">{{ clarification.text }}</blockquote>
      <div class="muted small"><strong>{{ t('evidence.translation') }}:</strong> {{ clarification.translation }}</div>
      <Button :label="copied ? t('evidence.copied') : t('evidence.copy')" :icon="copied ? 'pi pi-check' : 'pi pi-copy'" size="small" text @click="copy(clarification.text)" />
    </div>
  </div>
</template>

<style scoped>
.ecertis { margin-top: 8px; border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; background: #fafbfd; font-size: 13.5px; display: flex; flex-direction: column; gap: 8px; }
.ecertis-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.badge { background: var(--accent); color: white; border-radius: 6px; padding: 1px 8px; font-size: 12px; font-weight: 600; }
.open { margin-left: auto; font-size: 12.5px; text-decoration: none; }
.note { margin: 0; color: var(--warn); }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 800px) { .cols { grid-template-columns: 1fr; } }
.col-title { font-weight: 600; margin-bottom: 4px; }
ul { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 3px; }
li.matched { font-weight: 600; list-style: none; margin-left: -18px; }
li.matched i { color: var(--good); margin-right: 4px; }
a { text-decoration: none; }
.clarification { border-top: 1px dashed var(--border); padding-top: 8px; }
blockquote { margin: 6px 0; padding: 10px 12px; background: white; border-left: 3px solid var(--accent); border-radius: 4px; }
.small { font-size: 12.5px; }
</style>
