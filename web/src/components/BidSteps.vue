<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, type RouteLocationRaw } from 'vue-router'
import { api } from '@/lib/api'
import type { Deal } from '@/lib/tenders'

/**
 * The five-step bid path, so a user always sees where they are and what comes next. `current` is 1-based.
 * Inside a deal (`deal`, or `?deal=` carried along from a deal page) every step links to that step's page for the deal,
 * so you can go back over what happened: the tender, its check, the partner search, the negotiation, the documents.
 */
const props = defineProps<{ current: number; deal?: string }>()
const { t, tm, rt } = useI18n()
const route = useRoute()
const dealId = computed(() => props.deal ?? (typeof route.query.deal === 'string' ? route.query.deal : undefined))
const deal = ref<Deal>()
watch(dealId, async (id) => {
  deal.value = id ? (await api<{ deal: Deal }>(`/deals/${id}`, {}, true).catch(() => undefined))?.deal : undefined
}, { immediate: true })

const links = computed<(RouteLocationRaw | undefined)[]>(() => {
  const d = deal.value
  if (!d) return []
  const query = { deal: d.id }
  return [
    { path: '/tenders', query },
    { path: `/tenders/${encodeURIComponent(d.tenderId)}`, query },
    { path: '/partners', query: { ...query, tender: d.tenderId, lot: d.lotId, gap: String(d.gap) } },
    `/deals/${d.id}`,
    `/deals/${d.id}/documents`,
  ]
})

const steps = computed(() => {
  const names = tm('guide.steps') as unknown[]
  const hints = tm('guide.hints') as unknown[]
  return names.map((n, i) => ({
    n: i + 1,
    name: rt(n as Parameters<typeof rt>[0]),
    hint: rt(hints[i] as Parameters<typeof rt>[0]),
    state: i + 1 < props.current ? 'done' : i + 1 === props.current ? 'now' : 'next',
    to: links.value[i],
  }))
})
const now = computed(() => steps.value[props.current - 1])
</script>

<template>
  <nav class="steps" :aria-label="t('guide.label')">
    <ol>
      <li v-for="s in steps" :key="s.n" :class="[s.state, { link: s.to }]" :aria-current="s.state === 'now' ? 'step' : undefined">
        <component :is="s.to ? 'RouterLink' : 'span'" :to="s.to" class="step">
          <span class="num">{{ s.state === 'done' ? '✓' : s.n }}</span>
          <span class="name">{{ s.name }}</span>
        </component>
      </li>
    </ol>
    <p v-if="now" class="hint">{{ now.hint }}</p>
  </nav>
</template>

<style scoped>
.steps { display: flex; flex-direction: column; gap: 10px; }
ol { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); border: 1px solid var(--border); background: var(--surface); }
li { display: flex; font-size: 14px; color: var(--muted); border-right: 1px solid var(--border); min-width: 0; }
.step { display: flex; flex: 1; gap: 10px; align-items: center; padding: 12px 14px; min-width: 0; color: inherit; text-decoration: none; }
li.link:not(.now):hover { background: var(--ground); color: var(--text); }
li:last-child { border-right: 0; }
.num { flex: none; width: 24px; height: 24px; display: grid; place-items: center; font-family: var(--mono); font-size: 12px; border: 1px solid var(--border-strong); color: var(--muted); }
.name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
li.done .num { background: var(--ink); border-color: var(--ink); color: #fff; }
li.done { color: var(--text); }
li.now { background: var(--accent-soft); color: var(--text); font-weight: 600; box-shadow: inset 0 -3px 0 var(--accent); }
li.now .num { background: var(--accent); border-color: var(--accent); color: #fff; }
.hint { margin: 0; font-size: 14.5px; color: var(--muted); }
@media (max-width: 900px) {
  ol { grid-template-columns: 1fr; }
  li { border-right: 0; border-bottom: 1px solid var(--border); }
  li:last-child { border-bottom: 0; }
  li:not(.now) .name { white-space: normal; }
}
</style>
