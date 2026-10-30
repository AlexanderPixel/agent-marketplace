import { reactive } from 'vue'
import { session } from './session'

/** Page data being fetched. Drives the logo loader; polls and button actions pass `quiet` and are not counted. */
export const loading = reactive({ pending: 0 })

/** Every call carries the chosen company; the server scopes all private data to it. */
export async function api<T>(path: string, init: RequestInit = {}, quiet = false): Promise<T> {
  const counted = !quiet && (init.method ?? 'GET') === 'GET'
  if (counted) loading.pending++
  try {
    return await request<T>(path, init)
  } finally {
    if (counted) loading.pending--
  }
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const headers = new Headers(init.headers)
  if (session.orgId) headers.set('X-Org-Id', session.orgId)
  headers.set('Accept-Language', session.language)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(`/api${path}`, { ...init, headers })
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
