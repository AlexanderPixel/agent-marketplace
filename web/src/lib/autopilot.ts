import { reactive } from 'vue'
import type { Router } from 'vue-router'
import { api, post } from './api'

/**
 * Demo autopilot. The server runs every step (qualify, partner, negotiation, documents) as an auto-bid demo run;
 * this page only follows it: it opens the screen for the current stage and points at what changed. Because the work
 * happens on the server, refreshing or closing the page never breaks the demo; the page picks the run up again.
 * Nothing is signed or submitted: the run stops at a person's Sign / Don't sign.
 */
export type Stage = 'check' | 'partner' | 'negotiate' | 'documents' | 'done' | 'stopped'

interface Run { id: string; tenderId: string; lotId: string | null; gap: number | null; dealId: string | null; stage: string }

export const autopilot = reactive({
  stage: null as Stage | null,
  /** i18n key under `autopilot.msg.` describing what is happening now. */
  message: '',
  runId: null as string | null,
})

export const autopilotRunning = () => !!autopilot.stage && autopilot.stage !== 'done' && autopilot.stage !== 'stopped'

/** True when this screen should show the current step. */
export const autopilotAt = (stage: Stage) => autopilot.stage === stage

export function advance(stage: Stage, message: string) {
  if (stage === 'done' || stage === 'stopped') cursor.visible = false
  autopilot.stage = stage
  autopilot.message = message
}

const KEY = 'autopilotRun'
const remember = (id: string | null) => {
  try {
    if (id) localStorage.setItem(KEY, id)
    else localStorage.removeItem(KEY)
  } catch {
    // Private mode: following just won't survive a refresh.
  }
}

let timer: ReturnType<typeof setInterval> | undefined

/** Stops following on this page. The server run itself carries on to the sign request. */
export function stopAutopilot(message = 'stopped') {
  clearInterval(timer)
  remember(null)
  advance('stopped', message)
}

export function dismissAutopilot() {
  clearInterval(timer)
  remember(null)
  autopilot.stage = null
  autopilot.message = ''
  autopilot.runId = null
}

/** Asks the server to run the demo on a tender, then follows it. */
export async function startAutopilot(router: Router, tenderId: string) {
  const { runId } = await post<{ runId: string }>('/autobid/demo', { tenderId })
  follow(router, runId)
}

/** Picks up a run that was being followed before a refresh. */
export function resumeAutopilot(router: Router) {
  try {
    const id = localStorage.getItem(KEY)
    if (id) follow(router, id)
  } catch {
    // Storage blocked: nothing to resume.
  }
}

function follow(router: Router, runId: string) {
  autopilot.runId = runId
  remember(runId)
  clearInterval(timer)
  let lastStage: string | null = null
  const tick = async () => {
    let run: Run
    try {
      run = await api<Run>(`/autobid/runs/${runId}`, {}, true)
    } catch {
      return stopAutopilot('stopped')
    }
    // Move only when the server's stage changes, so a person can still browse elsewhere in between.
    if (run.stage === lastStage) return
    lastStage = run.stage
    const target = targetOf(run)
    if (!target) return
    advance(target.stage, target.message)
    if (target.path && router.currentRoute.value.fullPath !== target.path) await router.push(target.path)
    if (target.stage === 'stopped') {
      clearInterval(timer)
      remember(null)
      return
    }
    if (target.stage === 'documents') {
      // The run has reached the person's decision: stop following and finish once the drafts have been shown.
      clearInterval(timer)
      remember(null)
      for (let i = 0; i < 24 && !documentsShown; i++) await beat(500)
      advance('done', 'done')
    }
  }
  void tick()
  timer = setInterval(tick, 1200)
}

/** Set by the documents screen once it has shown the drafts, so the run counts as watched to the end. */
let documentsShown = false
export const markDocumentsShown = () => { documentsShown = true }

function targetOf(run: Run): { stage: Stage; message: string; path?: string } | null {
  const tender = `/tenders/${encodeURIComponent(run.tenderId)}`
  switch (run.stage) {
    case 'Qualifying':
      documentsShown = false
      return { stage: 'check', message: 'check', path: tender }
    case 'FindingPartner':
      return { stage: 'partner', message: 'partner', path: `/partners?tender=${encodeURIComponent(run.tenderId)}&lot=${run.lotId}&gap=${run.gap}` }
    case 'Negotiating':
      return run.dealId ? { stage: 'negotiate', message: 'negotiate', path: `/deals/${run.dealId}` } : null
    case 'Drafting':
      return { stage: 'negotiate', message: 'documents', path: run.dealId ? `/deals/${run.dealId}` : tender }
    case 'AwaitingSignature':
    case 'Signed':
    case 'Declined':
      return { stage: 'documents', message: 'documents', path: run.dealId ? `/deals/${run.dealId}/documents` : tender }
    default:
      return { stage: 'stopped', message: 'dealFailed' }
  }
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
