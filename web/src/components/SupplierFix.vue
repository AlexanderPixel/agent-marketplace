<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import { flag } from '@/lib/flags'
import { eur } from '@/lib/tenders'

interface Alt { supplier: string; country: string; costEur: number; note: string }
interface Change { stage: 'awaiting' | 'signed'; to: Alt; euAfter: number; documents: unknown[] }
interface Swap { to: Alt; costDelta: number; newEuContent: number; reachesThreshold: boolean }

/** What to do about one part: its best replacement, or the change already waiting for a signature. */
defineProps<{ change?: Change | null; option?: Swap; recommended?: boolean; sanctioned?: boolean; euContent: number; telegram: boolean; busy: boolean }>()
const emit = defineEmits<{ prepare: []; sign: []; decline: []; docs: [] }>()
const { t } = useI18n()
</script>

<template>
  <div v-if="change?.stage === 'awaiting'" class="fix pending">
    <Tag :value="t('compliance.awaitingSign')" severity="warn" icon="pi pi-clock" />
    <div class="small">{{ flag(change.to.country) }} {{ change.to.supplier }} · {{ euContent }}% → <strong>{{ change.euAfter }}%</strong></div>
    <div class="muted small">{{ telegram ? t('compliance.sentTelegram') : t('compliance.noTelegram') }}</div>
    <div class="row-actions">
      <Button :label="t('compliance.viewDocs', { n: change.documents.length })" icon="pi pi-file" size="small" outlined @click="emit('docs')" />
      <Button :label="t('compliance.sign')" icon="pi pi-check" size="small" :loading="busy" @click="emit('sign')" />
      <Button :label="t('compliance.decline')" size="small" text severity="secondary" @click="emit('decline')" />
    </div>
  </div>

  <div v-else-if="option" class="fix">
    <div class="tags">
      <Tag v-if="sanctioned" :value="t('compliance.mustReplace')" severity="danger" icon="pi pi-ban" />
      <Tag v-if="recommended" :value="t('compliance.recommended')" severity="success" icon="pi pi-star" />
      <Tag v-if="!sanctioned" :value="option.reachesThreshold ? t('compliance.enough') : t('compliance.notEnough')" :severity="option.reachesThreshold ? 'success' : 'secondary'" />
    </div>
    <div class="small">
      {{ t('compliance.replaceWith') }} <strong>{{ flag(option.to.country) }} {{ option.to.supplier }}</strong>
      · {{ option.costDelta >= 0 ? '+' : '' }}{{ eur(option.costDelta) }} / {{ t('compliance.perSet') }}
      · {{ euContent }}% → <strong :class="option.reachesThreshold ? 'good' : ''">{{ option.newEuContent }}%</strong>
    </div>
    <div class="muted small">{{ option.to.note }}</div>
    <div class="row-actions">
      <Button :label="t('compliance.prepare')" icon="pi pi-file-edit" size="small" :severity="recommended || sanctioned ? undefined : 'secondary'"
        :outlined="!recommended && !sanctioned" :loading="busy" @click="emit('prepare')" />
    </div>
  </div>

  <div v-else class="fix muted small">{{ t('compliance.noOption') }}</div>
</template>

<style scoped>
.small { font-size: 12.5px; }
.good { color: var(--good); }
.fix { display: flex; flex-direction: column; gap: 4px; }
.fix.pending { border-left: 3px solid var(--warn); padding-left: 10px; }
.tags { display: flex; gap: 6px; flex-wrap: wrap; }
.row-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
</style>
