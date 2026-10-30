<script setup lang="ts">
import { autopilotRunning, cursor } from '@/lib/autopilot'
</script>

<template>
  <div v-if="cursor.visible && autopilotRunning()" class="ap-cursor" :class="{ clicking: cursor.clicking }"
    :style="{ transform: `translate(${cursor.x}px, ${cursor.y}px)` }" aria-hidden="true">
    <svg width="26" height="30" viewBox="0 0 26 30"><path d="M2 2 L2 24 L8 18.5 L12.5 28 L16.5 26.2 L12 17 L20 17 Z" fill="#12171a" stroke="#fff" stroke-width="2" stroke-linejoin="round" /></svg>
    <span class="ring" />
  </div>
</template>

<style scoped>
.ap-cursor { position: fixed; left: 0; top: 0; z-index: 1000; pointer-events: none; transition: transform 0.8s cubic-bezier(0.25, 0.8, 0.25, 1); filter: drop-shadow(0 2px 4px rgb(0 0 0 / 25%)); }
.ap-cursor svg { display: block; transition: transform 0.12s ease; }
.ap-cursor.clicking svg { transform: scale(0.85); }
.ring { position: absolute; left: -14px; top: -14px; width: 32px; height: 32px; border: 2px solid var(--accent); border-radius: 50%; opacity: 0; }
.ap-cursor.clicking .ring { animation: press 0.45s ease-out; }
@keyframes press { from { transform: scale(0.3); opacity: 0.9; } to { transform: scale(1.4); opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .ap-cursor { transition: none; } .ap-cursor.clicking .ring { animation: none; } }
</style>
