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
  autopilot.stage = stage
  autopilot.message = message
}

export function stopAutopilot(message = 'stopped') {
  autopilot.stage = 'stopped'
  autopilot.message = message
}

export function dismissAutopilot() {
  autopilot.stage = null
  autopilot.message = ''
}

/** A beat between steps so viewers can read each screen. */
export const beat = (ms = 2200) => new Promise<void>((resolve) => setTimeout(resolve, ms))
