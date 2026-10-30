# Tender brief format

`GET /api/brief?id=<notice id>` and `tenderhub brief <source> <source_id> --json` return one JSON object built by
`tenderhub.brief.build()` from what `tenderhub enrich` captured. It powers the viewer's **Brief** mode (clean,
decision-ready) next to the **Verbose** mode (every captured page and attachment).

Every item has a `cite`: `{"file": <attachment filename or page url>, "folder": <e.g. "Vergabeunterlagen/Version 2" or null>,
"page": <int or null>, "quote": <short verbatim text from the source, ≤300 chars>}`. No item without a source.
Values are taken from the documents, never invented; when a document is ambiguous, leave the field `null`.
Dates are ISO 8601 (`2026-11-20T10:00:00+01:00`, or `2026-11-20` when the source gives no time).

```jsonc
{
  "built_at": "2026-10-30T16:30:00+00:00",
  "errors": {},                                   // section -> error message if its extractor failed
  "summary": { "title", "buyer", "country", "procedure", "cpv": [..], "deadline", "value", "currency",
               "platform_url", "lots": 3, "documents": 30, "latest_version": 3 },

  "lots": [                                        // what is being bought
    { "lot": "1", "title": "Endobronchialblocker-Set, 9Fr", "quantity": 2400, "unit": "EA",
      "description": "short spec / variant notes or null", "cite": {..} } ],

  "deadlines": [                                   // every date a bidder must act on, soonest first
    { "label": "Angebotsfrist (offer deadline)", "at": "2026-11-20T08:00:00+01:00",
      "kind": "offer | questions | binding_period | contract_start | contract_end | other", "cite": {..} } ],

  "submission": [                                  // how to bid: platform, form, signature, language, variants, ...
    { "label": "Form der Angebotsabgabe", "value": "elektronisch in Textform über e-Vergabe", "cite": {..} } ],

  "criteria": [                                    // award criteria and weights (Zuschlagskriterien / Bewertungsmatrix)
    { "name": "Preis", "weight": 100, "weight_unit": "% | points", "lot": null, "description": "…", "cite": {..} } ],

  "checklist": [                                   // documents the bidder must hand in (maps to the SWZ checklist)
    { "document": "Eigenerklärung zum Nichtvorliegen von Ausschlussgründen (B-V034)",
      "obligation": "mandatory | if_applicable | on_request",
      "when": "with_offer | on_request | after_award | null",
      "template": "VM191_12_B-V034_Eigenerklaerung Nichtvorliegen Ausschlussgruende.pdf or null",
      "lot": null, "cite": {..} } ],

  "requirements": [                                // conditions the supplier/product must meet
    { "category": "quality | product | eligibility | legal | delivery | catalogue | invoicing | other",
      "requirement": "Medizinprodukte müssen CE-gekennzeichnet sein (MDR)", "cite": {..} } ],

  "amendments": [                                  // what changed after publication, oldest first
    { "version": 2, "date": "2026-10-27", "title": "1. Hinweisblatt",
      "changes": ["Frist für Bieterfragen verlängert auf …", "…"],
      "files_changed": ["Leistungsverzeichnis.pdf"], "cite": {..} } ],

  "contacts": [
    { "role": "Vergabestelle", "name": null, "email": "…@bundeswehr.org", "phone": null, "cite": {..} } ]
}
```

Extractors live in `src/tenderhub/brief/<section>.py` and receive a `TenderContext`: `notice` (the stored row),
`pages` (captured page markdown) and `docs` (attachments: `filename`, `folder`, `version`, `text`, `page_texts()`,
`find()`); `ctx.doc(pattern)` / `ctx.docs_matching(pattern)` pick attachments by filename, newest version first.
They must work from the text — no values hard-coded for one tender — so the same code handles other tenders that
use the same document templates (Bundeswehr / BAIUDBw / BAAINBw on e-Vergabe, and similar German tenders).
