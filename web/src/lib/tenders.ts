import { session } from './session'

export interface TenderLot {
  id: string
  title: string
  quantity: number | null
  unit: string | null
  description: string | null
  cpv: string | null
  place: string | null
  source: string
}

export interface TenderRequirement {
  code: string | null
  text: string
  language: string
  lots: string[]
  source: string
}

export interface TenderSummary {
  id: string
  source: 'TED' | 'BZP'
  sourceId: string
  reference: string | null
  title: string
  language: string
  titles: Record<string, string>
  buyerName: string
  buyerCountry: string
  buyerCity: string | null
  cpv: string
  cpvLabels: Record<string, string>
  publishedAt: string
  deadline: string | null
  isDefence: boolean
  featured: boolean
  hasFullNotice: boolean
  lotCount: number
}

export interface Tender extends TenderSummary {
  procedureType: string | null
  contractNature: string | null
  noticeUrl: string | null
  documentsUrl: string | null
  lots: TenderLot[]
  requirements: TenderRequirement[]
  exclusionGrounds: string[]
  text: string | null
  rawJson: string | null
}

/** Title in the reader's language when the source publishes one (TED translates titles into all EU languages). */
export const localTitle = (t: TenderSummary) => t.titles[session.language] ?? t.title
export const isTranslated = (t: TenderSummary) => t.language !== session.language && !!t.titles[session.language]
export const cpvLabel = (t: TenderSummary) => t.cpvLabels[session.language] ?? t.cpvLabels[t.language] ?? ''

export const daysLeft = (deadline: string | null) =>
  deadline ? Math.ceil((new Date(deadline).getTime() - Date.now()) / 86_400_000) : null

export const deadlineSeverity = (days: number | null) =>
  days === null ? 'secondary' : days < 0 ? 'secondary' : days <= 7 ? 'danger' : days <= 14 ? 'warn' : 'success'

export const formatDate = (iso: string | null, withTime = false) =>
  iso
    ? new Date(iso).toLocaleString(session.language, {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
      })
    : '—'

export const units: Record<string, string> = { EA: 'pcs', SE: 'sets', PR: 'pairs', KT: 'kits' }

export const criteriaNames: Record<string, string> = {
  'slc-suit-reg-trade': 'Enrolment in a trade register',
  'slc-suit-reg-prof': 'Enrolment in a professional register',
  'slc-suit-auth-mbrshp': 'Authorisation or membership to perform the activity',
  'slc-abil-ref-supply': 'References: past deliveries',
  'slc-abil-ref-services': 'References: past services',
  'slc-abil-staff-qualif': 'Staff qualifications',
  'slc-abil-qual-cert': 'Quality assurance certificates',
  'slc-stand-fin-turnover': 'Minimum turnover',
  'slc-stand-fin-insur': 'Professional risk insurance',
}
