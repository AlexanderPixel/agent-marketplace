# eCertis: DE vs PL evidence (pulled 2026-10-30)

Source: `GET https://ec.europa.eu/growth/tools-databases/ecertisrest/criteria/{id}?lang=<xx>&countryFilter=<CC>` (public, no auth).
Criteria list: `criteria?lang=en` (56 criteria, saved as criteria-en.json).

## Enrolment in a trade register (87b3fa26-3549-4f92-b8e0-3fd8f04bf5c7)
This is `slc-suit-reg-trade` in eForms, the selection criterion used on all 20 lots of the BAAINBw tender.

| Germany (DE) | Poland (PL) |
|---|---|
| Amtlicher chronologischer Ausdruck aus dem Handelsregister A / B (Amtsgericht) | aktualny odpis z Krajowego Rejestru Sądowego (KRS) |
| Partnerschafts- / Genossenschaftsregister | odpis z CEIDG (sole traders) |
| Auskunft aus dem Gewerbezentralregister (Bundesamt für Justiz) | |
| IHK-Mitgliedsbescheinigung; AVPQ / PQ-VOB lists | |

So a Polish sp. z o.o. answers a German "Handelsregisterauszug" requirement with an odpis z KRS.

## Exclusion: participation in a criminal organisation (005eb9ed-1347-4ca3-bb29-9bc0db64e1ab)
- DE: Extract from judicial records; self-declaration; AVPQ lists.
- PL: Extract from the National Criminal Register (Krajowy Rejestr Karny, KRK).

Note: the BAAINBw notice's criterion text says "Wettbewerbsregisterauskunft ohne Eintragung". eCertis has no entry for the
Wettbewerbsregister (the German competition register, run since 2022). The closest PL equivalent is an extract from the KRK
(Krajowy Rejestr Karny, the national criminal register), plus the KRS. The app should flag this as a gap with a reasoned
mapping, not as a confirmed eCertis match.
