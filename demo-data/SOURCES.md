# Demo data (pulled 2026-10-30)

## e-Zamówienia BZP (reachable, real data)
API: `GET https://ezamowienia.gov.pl/mo-board/api/v1/notice` (anonymous, JSON).
Confirmed params: `NoticeType` (ContractNotice, TenderResultNotice, ...), `PublicationDateFrom`, `PublicationDateTo`
(YYYY-MM-DD), `CpvCode` (exact code incl. check digit, e.g. `35300000-7`; not a prefix match), `PageSize` (max 500).
`PageNumber` appeared to be ignored; split by date range to page. `OrganizationName` filter returned nothing in tests.
Each item has metadata plus the full notice as `htmlBody`. BZP covers below-EU-threshold national notices only;
above-threshold (incl. Agencja Uzbrojenia) goes to TED.

Files in `ezamowienia/`:
- `contract-notices-cpv-<cpv>-2026.json`: all 2026 contract notices for CPV 35000000-4, 35100000-5, 35113400-3,
  35300000-7, 35800000-2, 35811300-5 (Jan 1 to Oct 30, 2026).
- `result-notices-cpv-35300000-7.json`: award notices for weapons/ammunition (May to Oct 2026), with winners and values.
- `notices/`: 46 notices from military, police, border guard, customs and prison-service buyers (`.json` metadata + `.html` full text).
- `index.csv`: one row per notice, including platformazakupowa.pl transaction links (26 notices link there).

## TED (api.ted.europa.eu reachable; ted.europa.eu website still blocked, so no raw XML/PDF yet)
Search: `POST https://api.ted.europa.eu/v3/notices/search` with `{"query": "<expert query>", "fields": [...], "limit": 100, "page": n}`.
1,830 eForms fields can be requested by name (an invalid field name returns the full list). I saved every non-empty field per notice
as `*-eforms-fields.json`. That is effectively the parsed eForms notice, but it is not the original XML file.
- `ted/baainbw-leaf-springs/`: **German demo tender.** BAAINBw, procedure 6003110460-BAAINBw E2.5F, eForms procedure id
  969a4555-f0bf-4540-ad2c-7bc36e13c7bc. Notices: 622736-2026 (10 Sep) and 723644-2026 (20 Oct, latest version). Open procedure, German,
  CPV 35400000, 20 lots of military-vehicle parts (e.g. LOT-0004 leaf springs, 800 SE), deadline 10 Nov 2026 13:00 CET.
  Selection criterion on all lots: slc-suit-reg-trade ("Wettbewerbsregisterauskunft ohne Eintragung"). The documents are at
  evergabe-online.de/tenderdocuments.html?id=889297, which is still blocked here. `lots.csv` lists all 20 lots with quantities.
- `ted/pl-defence/`: 208 Polish CPV-35 contract notices (Sep to Oct 2026), plus full fields for 604127-2026
  (2. RBLog, military vehicle parts, 10 lots; a good Polish mirror of the German tender) and 609937-2026 (weapons and ammunition).
- `ted/prime-awards/`: 92 award notices in 2026 won by Rheinmetall or KNDS entities (top winners: Rheinmetall Landsysteme 54,
  KNDS Deutschland Maintenance 41). A search on "Polska Grupa Zbrojeniowa" found no wins: PGZ companies win under their own names (e.g. HSW).

## eCertis (reachable)
`ecertis/`: criteria list, trade-register and criminal-organisation evidence for DE and PL, plus `equivalence-summary.md`
(Handelsregister maps to odpis z KRS; eCertis has no Wettbewerbsregister entry).

## platformazakupowa.pl: reachable but NOT scraped
Its terms (Regulamin, /strona/1-regulamin) forbid automated access by bots, crawlers, scrapers and AI agents, and bulk attachment downloads.
The only exceptions are the provider's own API or CSV exports. robots.txt sets Crawl-delay 900 and disallows /file/get_new/*.
So nothing was fetched beyond robots.txt and the terms page. The transaction links found in BZP notices are listed in `ezamowienia/index.csv`.

## Still blocked from this container
- ted.europa.eu (raw eForms XML and PDFs), www.evergabe-online.de (German tender documents), www.hellotender.eu
