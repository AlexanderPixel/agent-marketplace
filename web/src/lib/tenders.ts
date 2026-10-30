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
  verdict: Verdict | null
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

export type Verdict = 'can-supply' | 'partner-needed' | 'not-yet' | 'out-of-range'
export type Outcome = 'Met' | 'Equivalent' | 'Missing' | 'Unclear'

export const verdictSeverity: Record<Verdict, string> = {
  'can-supply': 'success',
  'partner-needed': 'info',
  'not-yet': 'danger',
  'out-of-range': 'secondary',
}
export const verdictIcon: Record<Verdict, string> = {
  'can-supply': 'pi pi-check-circle',
  'partner-needed': 'pi pi-users',
  'not-yet': 'pi pi-times-circle',
  'out-of-range': 'pi pi-minus-circle',
}
export const outcomeSeverity: Record<Outcome, string> = { Met: 'success', Equivalent: 'warn', Missing: 'danger', Unclear: 'danger' }
export const outcomeIcon: Record<Outcome, string> = {
  Met: 'pi pi-check', Equivalent: 'pi pi-arrow-right-arrow-left', Missing: 'pi pi-times', Unclear: 'pi pi-question',
}

export interface CompanyDocument {
  id: string
  kind: string
  title: string
  issuer: string
  country: string
  issuedOn: string | null
  validUntil: string | null
}

export interface Analysis {
  summary: { language: string; headline: string; points: { text: string; cite: string }[]; byAi: boolean }
  requirements: {
    requirement: TenderRequirement
    outcome: Outcome
    checks: {
      need: string
      outcome: Outcome
      reason: string
      evidence: CompanyDocument | null
      ecertis: EcertisMatch | null
      clarification: { language: string; text: string; translation: string } | null
      alsoSubmit: CompanyDocument[] | null
    }[]
  }[]
  lots: { lot: TenderLot; fit: 'Full' | 'Partial'; product: { name: string; unit: string } | null; capacity: number | null; gap: number | null; reason: string }[]
  notOffered: number
  verdict: Verdict
}

export interface EcertisEvidence { name: string; issuer: string | null; country: string; url: string | null; validity: string | null }
export interface EcertisMatch {
  criterionId: string
  criterionName: string
  url: string
  buyerCountry: EcertisEvidence[]
  companyCountry: EcertisEvidence[]
  matched: EcertisEvidence | null
  note: string | null
}

export type DealStatus = 'Negotiating' | 'AwaitingApproval' | 'Closed' | 'Failed'
export interface Offer { round: number; side: 'bidder' | 'partner'; unitPrice: number; quantity: number; delivery: string; message: string; accepts: boolean; at: string }
export interface Deal {
  id: string; tenderId: string; tenderRef: string; lotId: string; lotTitle: string; unit: string; gap: number
  bidderOrgId: string; partnerOrgId: string; matchScore: number; dealType: string; status: DealStatus; autonomous: boolean
  outcome: string | null; unitPrice: number | null; quantity: number | null; delivery: string | null; offers: Offer[]
  createdAt: string; closedAt: string | null; approvedBy: string | null
  documents: AgreementDocument[]; documentsAt: string | null
}
export interface Party { name: string; country: string; city?: string }
export const dealSeverity: Record<DealStatus, string> = { Negotiating: 'info', AwaitingApproval: 'warn', Closed: 'success', Failed: 'danger' }
export const eur = (n: number | null | undefined, digits = 2) =>
  n == null ? '—' : new Intl.NumberFormat(session.language, { style: 'currency', currency: 'EUR', maximumFractionDigits: digits }).format(n)

export interface AgreementDocument {
  id: string; kind: string; title: Record<string, string>; body: Record<string, string>
  signers: { party: string; name: string; signature: 'qes' | 'text' | 'handwritten' }[]
  purpose: string; submitTo: string; submitWithBid: boolean
}

/** A copy of one of the company's own certificates the tender asks for (PL text, DE working translation). */
export interface TenderCertificate {
  id: string; kind: string; title: Record<string, string>; body: Record<string, string>; need: string; why: string
  issuer: string; validUntil: string | null; validAtDeadline: boolean; demoRequirement: boolean
}
