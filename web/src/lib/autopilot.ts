import { reactive } from 'vue'

/**
 * Demo autopilot: walks the P0 path on the real screens so an audience can watch it.
 * Each screen does its own real action when the stage matches, then hands over to the next stage.
 * It only presses buttons a person would press: limits stay enforced on the server, a deal below the
 * autonomy threshold stops for human approval, and nothing is signed or submitted.
 */
export type Stage = 'check' | 'partner' | 'negotiate' | 'documents' | 'done' | 'stopped'

export const autopilot = reactive({
  stage: null as Stage | null,
  /** i18n key under `autopilot.` describing what is happening now. */
  message: '',
})

export const autopilotRunning = () => !!autopilot.stage && autopilot.stage !== 'done' && autopilot.stage !== 'stopped'

/** True when this screen should act now. */
export const autopilotAt = (stage: Stage) => autopilot.stage === stage

export function advance(stage: Stage, message: string) {
  if (stage === 'done') cursor.visible = false
  autopilot.stage = stage
  autopilot.message = message
}

export function stopAutopilot(message = 'stopped') {
  cursor.visible = false
  autopilot.stage = 'stopped'
  autopilot.message = message
}

export function dismissAutopilot() {
  autopilot.stage = null
  autopilot.message = ''
}

/** A beat between steps so viewers can read each screen. */
export const beat = (ms = 2200) => new Promise<void>((resolve) => setTimeout(resolve, ms))

/** The on-screen pointer the audience follows while the autopilot drives. */
export const cursor = reactive({ x: 0, y: 0, visible: false, clicking: false })

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches
const find = (target: string | Element | null | undefined) =>
  typeof target === 'string' ? document.querySelector(target) : (target ?? null)

/** Brings a part of the page into view so viewers always see what the agents are working on. */
export async function showSection(target: string | Element | null | undefined, block: ScrollLogicalPosition = 'center') {
  const el = find(target)
  if (!el) return
  el.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block })
  await beat(reducedMotion() ? 50 : 700)
}

/**
 * Scrolls to an element and glides the pointer onto it; with `click`, shows a press.
 * The press is only shown: the caller performs the action itself, so the flow can await its result.
 */
export async function pointAt(target: string | Element | null | undefined, { click = false } = {}) {
  const el = find(target)
  if (!el) return
  await showSection(el)
  const r = el.getBoundingClientRect()
  if (!cursor.visible) {
    // Enter from the lower right so the first move is visible.
    Object.assign(cursor, { x: window.innerWidth - 80, y: window.innerHeight - 80, visible: true })
    await beat(60)
  }
  Object.assign(cursor, { x: r.left + Math.min(r.width / 2, 60), y: r.top + r.height / 2 })
  await beat(reducedMotion() ? 50 : 850)
  if (!click) return
  cursor.clicking = true
  await beat(260)
  cursor.clicking = false
  await beat(200)
}
