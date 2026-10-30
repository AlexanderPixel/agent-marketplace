import { reactive, watch } from 'vue'

export interface Org {
  id: string
  name: string
  country: string
  city: string
  kind: 'Supplier' | 'Buyer'
  description: string
  language: string
}

const stored = (() => {
  try {
    return JSON.parse(localStorage.getItem('session') ?? '{}') as { orgId?: string; language?: string }
  } catch {
    return {}
  }
})()

/** Demo sign-in: which company you act for and which language you read in. */
export const session = reactive({
  orgId: stored.orgId ?? '',
  language: stored.language ?? 'pl',
  orgs: [] as Org[],
})

watch(
  () => [session.orgId, session.language],
  () => {
    try {
      localStorage.setItem('session', JSON.stringify({ orgId: session.orgId, language: session.language }))
    } catch {
      // Private mode: the choice just isn't remembered.
    }
  },
)

export const currentOrg = () => session.orgs.find((o) => o.id === session.orgId)
