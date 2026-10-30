# CLAUDE.md

## What this project is

An AI platform for public procurement (EU and Polish tenders, including defence). It covers the full lifecycle:

- **Suppliers** find tenders, check whether they qualify, and prepare bid documents.
- **Suppliers who can't cover a whole order** find partners. Their AI agents negotiate a deal with no human in the loop, within limits each side pre-approved.
- **Buyers (government)** create tenders and analyse offers.
- **Middlemen** spot hot tenders and broker consortia.

This is a demo build. **The P0 demo path below is the only thing that must work end to end today.** Anything else is secondary.

## Priority levels

| Level | Meaning | What you do |
|---|---|---|
| **P0** | Build and demo today | Real, working implementation on real data |
| **P1** | Mock in the UI | Visible screens with realistic static/seed data. No real backend logic. |
| **P2** | Roadmap only | Do **not** build. At most a roadmap slide or a disabled "coming soon" label. |

Before starting any task, check its priority. Don't gold-plate P1 and don't touch P2. If a request is unclear about priority, ask.

## The P0 demo path (protect this above everything else)

1. A real TED tender is ingested.
2. The SWZ checklist shows a gap: the supplier has 30 of the 50 units required.
3. Partner search runs and finds a supplier for the remaining units.
4. Two agents negotiate live, visible in the agent activity log.
5. The deal closes.
6. Agreement documents are generated, along with the signing checklist.

Every change should keep this flow working. If a change risks breaking it, say so before making it.

## Scope by area

### 1. Tender ingestion and monitoring
- **[P0] Tender aggregator.** Pull notices from TED and e-Zamówienia into one database.
- **[P0] Tender detail page.** CPV codes, value, vadium, deadline, buyer, country, attached documents (SWZ, wykaz usług, wykaz osób, optional SKZ).
- **[P0] Filters.** CPV code, vadium amount, value, deadline, country, required company size, required experience.
- **[P0] SWZ fetch and parse.** Extract requirements: experience, staff/CV requirements, references, invoices, certificates, country of origin.
- [P1] Amendment and deadline monitoring (changed docs, clarifications, deadline shifts → notify).
- [P1] Historic awards database (winners, prices, CPV codes).
- [P2] Agencja Uzbrojenia, NATO NSPA, EDA sources.

### 2. Supplier side: bid preparation
- **[P0] Company profile**, which varies by company type:
  - *Manufacturer/defence:* company-owned certificates (AQAP, ISO, export licences) and product country of origin.
  - *Software house/services:* employee-owned credentials (e.g. a CV with a Python certificate attached).
- **[P0] Requirement match and gap check.** Green/red checklist of SWZ requirements against the profile, with a "can supply" / "can't supply" verdict.
- **[P0] Document pre-fill.** Wykaz usług, wykaz osób (from CVs), lists of references and invoices.
- [P1] Confidential document handling: auto-split trade secrets into a separate file marked confidential (top-right corner), generate the cover statement (e.g. "we submitted 5 invoices, marked as tajemnica przedsiębiorstwa"), generate an NDA.
- [P1] Clarification questions to the buyer, drafted before submission.
- [P1] RFI processing: suggested responses drafted from company data.
- [P1] Signing checklist: which documents need a qualified e-signature and who signs.
- [P1] Auto-attach certificates (origin, quality, weapons) when the SWZ requires them.
- [P2] Legal guide. [P2] Real e-podpis integration.

### 3. Partnering and aggregation (the autonomous deal)
- **[P0] Partner search.** Find suppliers to cover the shortfall (e.g. 30 of 50 units).
- **[P0] Agent-to-agent negotiation.** Agents agree price, quantity and delivery within limits each side pre-approved.
- **[P0] Agreement document generation:** subcontract, consortium agreement (wspólne ubieganie się), or zobowiązanie podmiotu udostępniającego zasoby, plus the related oświadczenia wykonawcy.
- [P1] Proactive matching: draft offers for tenders similar to past ones.
- [P2] Auto-bidding in public e-auctions.

### 4. Buyer (government) side — all P1 except publishing
- [P1] SWZ generator with a country-of-origin rule for weapons.
- [P1] Lot splitting (big lots vs. SME lots).
- [P1] Offer analysis: flag non-compliant offers, rank against criteria, summarise for the evaluation committee.
- [P2] Publish via API to e-Zamówienia / TED.

### 5. Middleman view — all P1
Hottest tenders (value, competition, urgency), supplier suggestions, deal-maker identification from award and consortium data.

### 6. Analytics — all P1
Company page (offers, wins/losses, CPV focus); stats for suppliers (win rates, average prices) and buyers (competition, price spread).

### 7. Notifications
- **[P0] Digest** of matching tenders and amendments via Telegram and/or email.
- [P2] Mobile push.

### 8. Trust, security and EU compliance
- **[P0] Agent activity log.** A visible feed of what each agent did, when, and why. Central to the demo and to the EU AI Act story.
- **[P0] Grounded AI.** The model answers only from the company's own documents and the tender documents, and cites its sources.
- **[P0] Tenant isolation.** One organisation's data is never visible to another. During negotiation, agents share only deal terms.
- [P1] AI disclosure labels on all AI-generated content.
- [P2] EU-hosted models option (e.g. Mistral, EU-region deployment).

## Non-negotiable rules

These hold for every feature, including mocks:

1. **Every agent action writes to the activity log** with a timestamp, the agent, the action, and the reason. No silent agent actions.
2. **Grounding:** AI output that makes claims about a tender or a company must cite the source document and location. If there's no source, the model says it doesn't know rather than guessing.
3. **Tenant isolation is enforced at the data layer**, not just in the UI. Every query is scoped by organisation. Negotiating agents receive only deal terms (price, quantity, delivery), never the other side's profile, documents or limits.
4. **Negotiation limits are hard bounds.** An agent can never agree outside its side's pre-approved price/quantity/delivery range. Enforce this in code, not just in the prompt.
5. **Humans sign.** The platform drafts and prepares; final tender submissions and qualified e-signatures stay with a human. The "no human in the loop" deal is the partner agreement between suppliers, within pre-approved limits.
6. **The AI recommends; the commission decides.** Buyer-side analysis never makes the award decision.
7. **Never declare capability the company can't deliver.** Gap checks must be conservative: unclear means red, not green.

## Domain glossary (Polish procurement terms)

| Term | Meaning |
|---|---|
| **SWZ** (Specyfikacja Warunków Zamówienia) | The tender specification: requirements, criteria, conditions |
| **Wykaz usług** | List of services/deliveries performed (experience evidence) |
| **Wykaz osób** | List of staff assigned to the contract, with qualifications |
| **SKZ** | Optional additional tender document attached alongside the SWZ |
| **Wadium / vadium** | Bid security deposit required to submit an offer |
| **CPV** | Common Procurement Vocabulary codes classifying the subject of a tender |
| **TED** | Tenders Electronic Daily, the EU's official tender journal |
| **e-Zamówienia** | Poland's national e-procurement platform |
| **Tajemnica przedsiębiorstwa** | Trade secret; documents so marked must be submitted separately |
| **Wspólne ubieganie się** | Joint bid by a consortium of contractors |
| **Zobowiązanie podmiotu udostępniającego zasoby** | Pledge by another entity to make its resources available to the bidder |
| **Oświadczenia wykonawcy** | Contractor declarations submitted with the offer |
| **e-podpis** | Qualified electronic signature |
| **AQAP** | NATO quality assurance standard for defence suppliers |
| **Agencja Uzbrojenia** | Polish Armament Agency (defence buyer) |
| **NSPA / EDA** | NATO Support and Procurement Agency / European Defence Agency |

Keep Polish document names in Polish in the UI and in generated documents; they are legal terms of art and shouldn't be translated.

## Working conventions

- Prefer real data for P0 (a real TED notice). Seed data is fine for P1, but make it realistic (real CPV codes, plausible values and buyers).
- Keep the demo resilient: cache the ingested demo tender and its parsed SWZ so the demo doesn't depend on a live external call at showtime.
- Generated legal documents are drafts. Label them as AI-generated drafts requiring review.
- When adding a feature, state its priority level in the PR/commit description.
