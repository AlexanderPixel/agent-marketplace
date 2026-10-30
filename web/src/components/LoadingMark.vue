<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { loading } from '@/lib/api'

/**
 * The EU Patriot mark drawing itself while page data loads. It waits a moment before showing, so fast pages never
 * flash it, and stays a minimum time once shown, so it never blinks.
 */
const { t } = useI18n()
const visible = ref(false)
let showTimer: ReturnType<typeof setTimeout> | undefined
let shownAt = 0

watch(() => loading.pending > 0, (busy) => {
  clearTimeout(showTimer)
  if (busy) {
    showTimer = setTimeout(() => { visible.value = true; shownAt = Date.now() }, 250)
  } else if (visible.value) {
    showTimer = setTimeout(() => (visible.value = false), Math.max(0, 600 - (Date.now() - shownAt)))
  }
}, { immediate: true })
</script>

<template>
  <Transition name="fade">
    <div v-if="visible" class="loader" role="status" aria-live="polite">
      <svg class="mark" width="72" height="54" viewBox="0 0 30 22" aria-hidden="true" fill="none" stroke-width="2.2">
        <path class="c1" d="M2 3 L15 10 L28 3" />
        <path class="c2" d="M5 10 L15 15.5 L25 10" />
        <path class="c3" d="M9 16.5 L15 20 L21 16.5" />
      </svg>
      <span class="word">EU Patriot</span>
      <span class="sr">{{ t('common.loading') }}</span>
    </div>
  </Transition>
</template>

<style scoped>
.loader { position: absolute; inset: 0; z-index: 4; display: flex; flex-direction: column; gap: 14px; align-items: center; justify-content: center;
  background: color-mix(in srgb, var(--ground) 82%, transparent); backdrop-filter: blur(2px); }
.mark path { stroke: var(--ink); stroke-dasharray: 32; stroke-dashoffset: 32; animation: draw 1.4s cubic-bezier(0.6, 0, 0.3, 1) infinite; }
.mark .c1 { stroke: var(--accent); }
.mark .c2 { animation-delay: 0.14s; }
.mark .c3 { animation-delay: 0.28s; }
@keyframes draw {
  0% { stroke-dashoffset: 32; }
  45%, 70% { stroke-dashoffset: 0; }
  100% { stroke-dashoffset: -32; }
}
.word { font-weight: 700; font-size: 15px; letter-spacing: -0.02em; color: var(--ink); animation: breathe 1.4s ease-in-out infinite; }
@keyframes breathe { 50% { opacity: 0.45; } }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
.fade-enter-active, .fade-leave-active { transition: opacity 0.25s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .mark path { animation: none; stroke-dashoffset: 0; }
  .word { animation: none; }
}
</style>
