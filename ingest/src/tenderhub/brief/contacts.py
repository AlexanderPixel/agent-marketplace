"""Brief section: contacts. See docs/brief-format.md for the item fields.

Who to talk to: the Vergabestelle (from the letterhead row of the "Aufforderung zur Angebotsabgabe": Ansprechperson,
Telefon, E-Mail; else the buyer organisation on the notice), the catalogue-data team named in the supplier handbook
(after award), and the review body (Vergabekammer) from the notice. One entry per e-mail address.
"""

from __future__ import annotations

import re

from . import TenderContext, cite
from .deadlines import aufforderung, clean, ordered_pages, search

# e-mail possibly split after "@" by PDF line wrapping: "Name@ \nbundeswehr.org"
_EMAIL = re.compile(r"([\w.+-]+@)[ \t]*\n?[ \t]*([\w-]+(?:\.[\w-]+)*\.[a-z]{2,})", re.I)
_PHONE = re.compile(r"\+?\d[\d \-/()]{5,}\d")
_PERSON = re.compile(r"\b(?:Frau|Herr)\s+(?:Dr\.\s+)?[A-ZÄÖÜ][\wäöüß-]+")


def _email(m: re.Match) -> str:
    return m.group(1) + m.group(2)


def _from_letterhead(ctx: TenderContext) -> dict | None:
    doc = aufforderung(ctx)
    hit = search(doc, re.compile(r"Ansprech(?:person|partner(?:/-?in)?)\s+Telefon\s+E-?Mail(?:\s+Datum)?", re.I))
    if not hit:
        return None
    page, head = hit
    row = doc.text[head.end(): head.end() + 300]
    em = _EMAIL.search(row)
    if not em:
        return None
    before = row[: em.start()]
    person = _PERSON.search(before)
    phone = _PHONE.search(before, person.end() if person else 0)
    unit = search(doc, re.compile(r"Ausschreibende\s+Stelle:\s*([^\n]+)", re.I))
    role = "Vergabestelle" + (f" ({clean(unit[1].group(1))})" if unit else "")
    quote = clean(doc.text[head.start(): head.end() + em.end()])
    return {"role": role, "name": person.group(0) if person else None, "email": _email(em),
            "phone": clean(phone.group(0)) if phone else None, "cite": cite(doc, page, quote)}


_FIELD = {
    "name": r"(?:Offizielle\s+Bezeichnung|Official\s+name)",
    "contact": r"(?:Kontaktstelle|Contact\s+point)",
    "email": r"(?:E-?Mail)",
    "phone": r"(?:Telefon|Telephone)",
}


def _from_notice(ctx: TenderContext) -> list[dict]:
    """Organisations of the eForms notice ("8 Organisationen" / "8. Organisations") with their roles."""
    out = []
    for p in ordered_pages(ctx):
        md = p["markdown"]
        for block in re.split(r"\n#{2,4}\s*8\.?1\.?\s+ORG-", md)[1:]:
            block = re.split(r"\n#{1,2}\s", block)[0]
            fields = {}
            for key, label in _FIELD.items():
                m = re.search(label + r":\s*\[?([^\n\]]+)", block, re.I)
                fields[key] = clean(m.group(1)) if m else None
            if not fields["email"] or "@" not in fields["email"]:
                continue
            if re.search(r"\b(?:Beschaffer|Buyer)\b", block):
                role = "Vergabestelle"
                name = fields["contact"] or fields["name"]
            elif re.search(r"Überprüfungsstelle|Review\s+organisation", block, re.I):
                role = "Vergabekammer (Nachprüfung)"
                name = fields["name"]
            else:
                continue        # eSender, mediation without contact, ...
            quote_m = re.search(_FIELD["email"] + r":\s*\[?" + re.escape(fields["email"]), block, re.I)
            out.append({"role": role, "name": name, "email": fields["email"], "phone": fields["phone"],
                        "cite": cite(p["url"], None, quote_m.group(0) if quote_m else fields["email"])})
    return out


def _catalogue(ctx: TenderContext) -> dict | None:
    """Catalogue-data contact (Kaufhaus des Bundes) from the supplier handbook / catalogue requirements."""
    rx = re.compile(r"(?:Tel(?:efonnummer|\.)?:?\s*(\d[\d \-/]{5,}\d)\s*(?:\n\s*)?(?:oder\s+per\s+)?)?E-?Mail:\s*"
                    r"([\w.+-]*katalog[\w.+-]*@[\w.-]+\.[a-z]{2,})", re.I)
    for doc in ctx.docs_matching(r"Katalog|Lieferantenhandbuch"):
        hit = search(doc, rx)
        if hit:
            page, m = hit
            team = re.search(r"(Katalogmanagement[^\n:]*):", doc.text[max(0, m.start() - 300): m.start()])
            return {"role": "Katalogdaten (nach Zuschlag)", "name": clean(team.group(1)) if team else None,
                    "email": m.group(2), "phone": clean(m.group(1)) if m.group(1) else None,
                    "cite": cite(doc, page, m.group(0))}
    return None


def extract(ctx: TenderContext) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()

    def add(item: dict | None) -> None:
        if item and item["email"].lower() not in seen:
            seen.add(item["email"].lower())
            items.append(item)

    notice = _from_notice(ctx)
    add(_from_letterhead(ctx))
    for it in notice:
        if it["role"] == "Vergabestelle":
            add(it)
    add(_catalogue(ctx))
    for it in notice:
        if it["role"] != "Vergabestelle":
            add(it)
    return items
