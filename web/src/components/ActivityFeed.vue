<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Tag from 'primevue/tag'
import { api } from '@/lib/api'
import { session } from '@/lib/session'

export interface ActivityEvent {
  id: number
  orgId: string
  sharedWithOrgId: string | null
  agent: string
  action: string
  reason: string
  subject: string | null
  createdAt: string
}

const props = defineProps<{ subject?: string }>()
const { t, d } = useI18n()
const events = ref<ActivityEvent[]>([])
const live = ref(false)
let source: EventSource | null = null

async function load() {
  source?.close()
  live.value = false
  events.value = []
  if (!session.orgId) return
  const query = props.subject ? `?subject=${encodeURIComponent(props.subject)}` : ''
  events.value = await api<ActivityEvent[]>(`/activity${query}`)
  source = new EventSource(`/api/activity/stream?org=${session.orgId}`)
  source.onopen = () => (live.value = true)
  source.onerror = () => (live.value = false)
  source.addEventListener('activity', (message) => {
    const e = JSON.parse((message as MessageEvent).data) as ActivityEvent
    if (props.subject && e.subject !== props.subject) return
    events.value = [e, ...events.value]
  })
}

watch(() => [session.orgId, props.subject], load, { immediate: true })
onBeforeUnmount(() => source?.close())
</script>

<template>
  <div class="feed">
    <div class="feed-head">
      <Tag :severity="live ? 'success' : 'secondary'" :value="t('activity.live')" :icon="live ? 'pi pi-circle-fill' : 'pi pi-circle'" />
    </div>
    <p v-if="events.length === 0" class="muted">{{ t('activity.empty') }}</p>
    <ol class="events">
      <li v-for="e in events" :key="e.id" class="event">
        <div class="event-top">
          <span class="agent"><i class="pi pi-microchip-ai" /> {{ e.agent }}</span>
          <time class="muted">{{ d(new Date(e.createdAt), { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }}</time>
        </div>
        <div class="action">{{ e.action }}</div>
        <div class="reason"><strong>{{ t('activity.why') }}:</strong> {{ e.reason }}</div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.feed { display: flex; flex-direction: column; gap: 12px; }
.feed-head { display: flex; justify-content: flex-end; }
.events { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.event { border: 1px solid var(--border); border-left: 3px solid var(--accent); border-radius: 8px; padding: 10px 12px; background: var(--surface); }
.event-top { display: flex; justify-content: space-between; gap: 8px; font-size: 13px; }
.agent { font-weight: 600; color: var(--accent); }
.action { margin-top: 4px; font-size: 14px; }
.reason { margin-top: 4px; font-size: 13px; color: var(--muted); }
</style>
