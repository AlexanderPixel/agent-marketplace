"""Brief section: lots — what is being bought, per lot. See docs/brief-format.md for the item fields.

Sources, best first:
1. the bill of quantities ("Leistungsverzeichnis" PDF/.aidf, newest version): one group header per lot,
   "LOS <n> - <quantity> <unit> <title>" (the AI-Vergabemanager / e-Vergabe template);
2. the notice pages (e-Vergabe details, TED): "Lot: LOT-000n" / "Los: LOT-000n" sections with a "Title:"/"Titel:" line;
3. the per-lot "Preis- und Produktblatt" file names, when nothing else names the lots.

The short spec in `description` comes from the lot's "Preis- und Produktblatt" row (Bundeswehr medical template),
most important first: technical "Key: value" lines plus the gist of the construction text ("Aufbau"), the reference
product ("Richtfabrikat - oder gleichwertig") with its article number and set contents, the minimum purchase quantity
with its call-offs (checked against the call-off dates the amendment sheets state), the minimum shelf life and the
maximum pack size. Tenders without lots fall back to the LV positions.
"""

from __future__ import annotations

import re

from . import Doc, TenderContext, cite

_LV = r"Leistungsverzeichnis|Material- und Leistungsliste"
_SHEET = r"Preis-?\s*und\s*Produktblatt"
# "LOS 1 - 2.400 EA Endobronchialblocker-Set, 9Fr"
_LOS_LINE = re.compile(r"^\s*LOS\s+(\d+)\s*[-–]\s*(\d[\d.]*(?:,\d+)?)\s+([A-Za-z]{1,4})\s+(\S.*?)\s*$", re.M | re.I)
# a lot section on TED / e-Vergabe / oeffentlichevergabe pages
_PAGE_LOT = re.compile(r"\b(?:Lot|Los)\s*:\s*LOT-0*(\d+)\b(.*?)(?=\b(?:Lot|Los)\s*:\s*LOT-\d+|\Z)", re.S)
_PAGE_TITLE = re.compile(r"^\s*(?:Title|Titel)\s*:\s*(\S.*?)\s*$", re.M)
_TITLE_WITH_QTY = re.compile(r"^LOS\s+(\d+)\s*[-–]\s*(\d[\d.]*(?:,\d+)?)\s+([A-Za-z]{1,4})\s+(\S.*)$", re.I)
# LV positions (tenders without lots): "A) <description>" ... "C) 00010 H - / - 2.400 EA 01.12.2026"
_POS_A = re.compile(r"^A\)\s*(\S.*?)\s*$", re.M)
_POS_C = re.compile(r"^C\)\s*(\d{3,6})\b.*?\s(\d[\d.]*(?:,\d+)?)\s+([A-Z]{1,4})\b", re.M)

# product sheet row
_SPEC_KEY = re.compile(r"^\s*([A-ZÄÖÜ][\wÄÖÜäöüß\-]*(?:[ /][\wÄÖÜäöüß\-]+){0,2})\s*:\s*(.*?)\s*$")
_LONG_KEYS = {"aufbau", "beschreibung", "besondere anforderungen"}
_VALUE_ONLY_KEYS = {"material", "gebrauch"}
_COMPANY = re.compile(r"\b(GmbH|AG|KG|SE|Inc\.?|Ltd\.?|LLC|S\.A\.|B\.V\.|S\.p\.A\.|Co\.)(\s|$)")
_ART_NR = re.compile(r"^(Art\.?-?\s*Nr|Artikel|Bestell|Referenz|REF\b|PZN)", re.I)
# the article number of the reference product: "Art.-Nr. 118-30", "Bestellnummer: G44120"
_ART_NR_VALUE = re.compile(r"^(?:Art\.?-?\s*Nr\.?|Artikel-?\s*(?:Nr\.?|nummer)|Bestell-?\s*(?:Nr\.?|nummer)|REF|PZN)"
                           r"\s*:?\s*(\S.*)$", re.I)
_PART = re.compile(r"^\d+\s*x\s+\S")            # set contents: "1x Saugadapter"
_PACK = re.compile(r"\bStück\s+pro\b|\bpro\s+Packung\b", re.I)
_PACK_SIZE = re.compile(r"(\d[\d.]*)\s+(\w+)\s+pro\s+Packung", re.I)
_SHELF = re.compile(r"(\d+)\s+Monate\b")
_MIN_QTY = re.compile(r"(\d[\d.]*)\s*\n\s*1\.\s*Abrufmenge")
_CALL_OFF = re.compile(r"(\d+)\.\s*Abrufmenge\s+(\d{1,2})/(\d{4})\s*:\s*(\d[\d.]*)\s*(Stück|\w+)?")
# the maximum quantity follows the last call-off line: "2.400 Stück gemäß Leistungsbeschreibung"
_MAX_AFTER = re.compile(r"\s*(\d[\d.]*)\s+(Stück|[A-Za-zÄÖÜäöüß]+)\b")
# amendment sheets restating when a call-off happens: "die 2. Abrufmenge im Januar 2028", "im 1. Quartal 2027"
_AMEND_SHEET = r"Hinweisblatt|Bieterinformation|Bieterrundschreiben|Änderungsmitteilung|Berichtigung"
_MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
           "November", "Dezember"]
_AMEND_CALL = re.compile(r"(\d)\.\s*Abrufmenge\s+(?:wird\s+)?(?:im|in|ab)\s+(?:(\d)\.\s*Quartal|(" + "|".join(_MONTHS)
                         + r"))\s+(\d{4})", re.I)
_ROW_END = re.compile(r"^\s*(Ort,\s*Datum|Firmenname|Richtfabrikat - oder gleichwertig\s*=)", re.M)


def _num(s: str) -> int | float | None:
    """German number: "2.400" -> 2400, "12,5" -> 12.5."""
    try:
        v = float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None
    return int(v) if v.is_integer() else v


def _clean(s: str) -> str:
    s = re.sub(r"\s+([,;.])", r"\1", " ".join(s.split()))   # "Niederdruckballon , ..." -> "Niederdruckballon, ..."
    return s.strip(" ,;")


# ---------------------------------------------------------------- lot list

def _from_lv(ctx: TenderContext) -> dict[str, dict]:
    """Lots from the newest bill of quantities; PDF first (it has page numbers), then the .aidf export."""
    lots: dict[str, dict] = {}
    docs = sorted(ctx.docs_matching(_LV), key=lambda d: (-d.version, not d.filename.lower().endswith(".pdf")))
    for doc in docs:
        for page, m in doc.find(_LOS_LINE):
            n = m.group(1)
            if n in lots:
                continue
            lots[n] = {"lot": n, "title": _clean(m.group(4)), "quantity": _num(m.group(2)),
                       "unit": m.group(3).upper(), "description": None,
                       "cite": cite(doc, page, m.group(0))}
        if lots:  # one document (the newest, most precise) is the source of truth
            break
    return lots


def _from_pages(ctx: TenderContext) -> dict[str, dict]:
    lots: dict[str, dict] = {}
    for pg in ctx.pages:
        md = pg.get("markdown") or ""
        for m in _PAGE_LOT.finditer(md):
            n = str(int(m.group(1)))
            t = _PAGE_TITLE.search(m.group(2))
            if n in lots or not t:
                continue
            title, qty, unit = t.group(1), None, None
            q = _TITLE_WITH_QTY.match(title)
            if q and str(int(q.group(1))) == n:
                title, qty, unit = q.group(4), _num(q.group(2)), q.group(3).upper()
            lots[n] = {"lot": n, "title": _clean(title), "quantity": qty, "unit": unit, "description": None,
                       "cite": cite(pg["url"], None, t.group(0))}
    return lots


def _from_sheet_names(ctx: TenderContext) -> dict[str, dict]:
    lots: dict[str, dict] = {}
    for doc in ctx.docs_matching(_SHEET):
        m = re.search(r"LOS[ _]?(\d+)_" + _SHEET + r"_(.+?)\.\w+$", doc.filename, re.I)
        if m and m.group(1) not in lots:
            title = re.sub(r"_\s*", ", ", m.group(2)).strip(", ")
            lots[m.group(1)] = {"lot": m.group(1), "title": title, "quantity": None, "unit": None,
                                "description": None, "cite": cite(doc, None, doc.filename)}
    return lots


def _positions(ctx: TenderContext) -> list[dict]:
    """No lots: list the LV positions (description + quantity) instead, lot = null."""
    doc = next((d for d in ctx.docs_matching(_LV) if d.filename.lower().endswith(".pdf")), None)
    if not doc:
        return []
    items = []
    for page, chunk in doc.page_texts():
        for a in _POS_A.finditer(chunk):
            c = _POS_C.search(chunk, a.end())
            if not c:
                continue
            items.append({"lot": None, "title": _clean(a.group(1)), "quantity": _num(c.group(2)),
                          "unit": c.group(3), "description": None, "cite": cite(doc, page, a.group(0))})
    return items


# ---------------------------------------------------------------- product sheet spec

def _sheet_row(ctx: TenderContext, lot: str) -> tuple[Doc, int | None, str] | None:
    """The lot's row in its "Preis- und Produktblatt" (after the template's "Beispiel" example row)."""
    sheets = ctx.docs_matching(_SHEET)
    own = [d for d in sheets if re.search(rf"LOS[ _]?{lot}(?!\d)", d.filename, re.I)]
    start = re.compile(rf"^{lot}\s+(?!Stück\b)[A-ZÄÖÜ]", re.M)
    for doc in own or sheets:
        for page, chunk in doc.page_texts():
            ex = re.search(r"^Beispiel\b", chunk, re.M)
            m = start.search(chunk, ex.end() if ex else 0)
            if not m:
                continue
            end = _ROW_END.search(chunk, m.end())
            nxt = re.compile(rf"^{int(lot) + 1}\s+(?!Stück\b)[A-ZÄÖÜ]", re.M).search(chunk, m.end())
            stop = min(x.start() for x in (end, nxt) if x) if (end or nxt) else len(chunk)
            return doc, page, chunk[m.start():stop]
    return None


def _gist(text: str, limit: int = 240) -> str:
    """Construction text, cut at a clause boundary when long."""
    text = _clean(text).rstrip(".")
    if len(text) <= limit:
        return text
    cut = max(text.rfind(", ", 0, limit), text.rfind(". ", 0, limit))
    return (text[:cut] if cut > limit // 2 else text[:limit].rsplit(" ", 1)[0]) + " …"


def _spec(row: str) -> str | None:
    lines = [ln.strip() for ln in row.splitlines() if ln.strip()]
    # the spec starts at the construction text; "Key: value" lines before it belong to the lot name column
    first = next((i for i, ln in enumerate(lines)
                  if (k := _SPEC_KEY.match(ln)) and k.group(1).lower() in _LONG_KEYS), 0)
    lines = lines[first:]
    specs: list[tuple[str, str]] = []
    rest: list[str] = []
    for i, ln in enumerate(lines):
        k = _SPEC_KEY.match(ln)
        if k and k.group(2):
            specs.append((k.group(1), k.group(2)))
        elif specs and specs[-1][0].lower() in _LONG_KEYS and not k:
            prev = specs[-1][1].rstrip()                         # wrapped long text ("Aufbau: ...");
            glue = "" if re.search(r"[a-zäöüß]-$", prev) else " "   # "Hochvolumen-" + "Niederdruckcuff"
            specs[-1] = (specs[-1][0], prev + glue + ln)
        elif specs:
            rest = lines[i:]                                     # spec block over: reference product follows
            break
    if not specs:
        return None
    short = [(k, v) for k, v in specs if k.lower() not in _LONG_KEYS]
    parts = [_clean(v) if k.lower() in _VALUE_ONLY_KEYS else f"{k} {_clean(v)}" for k, v in short]
    aufbau = next((v for k, v in specs if k.lower() in _LONG_KEYS), "")
    if aufbau:  # what the item is and what it includes; the measurable specs follow
        parts.insert(0, _gist(aufbau, 240 if len(short) >= 3 else 320))
    out = "; ".join(p.rstrip(".") for p in parts) + "."

    # reference product ("Richtfabrikat"): the lines after the spec block, up to an article number or key line;
    # the manufacturer is the first company line before the packaging column
    name: list[str] = []
    parts_list: list[str] = []
    maker, art, name_done = None, None, False
    for ln in rest:
        if _PACK.search(ln):
            break
        if _COMPANY.search(ln):
            maker = maker or _clean(ln)
            name_done = True
        elif _PART.match(ln):
            parts_list.append(_clean(ln))
            name_done = True
        elif _ART_NR.match(ln) or ":" in ln:
            a = _ART_NR_VALUE.match(ln)
            if a and not art:
                art = _clean(a.group(1))
            name_done = True
        elif not name_done and len(name) < 2:
            name.append(ln)
    if name:
        ref = _clean(" ".join(name))
        extra = ", ".join(x for x in (f"Art.-Nr. {art}" if art else None, maker) if x)
        out += f" Richtfabrikat o. glw.: {ref}" + (f" ({extra})" if extra else "")
        out += (f", Set aus {', '.join(parts_list)}." if parts_list else ".")
    return out


def _amended_call_offs(ctx: TenderContext) -> tuple[dict[int, tuple[set[int], str]], Doc | None]:
    """Call-off timing restated in the newest amendment sheet: {call-off no.: (allowed months, wording)}."""
    sheets = sorted(ctx.docs_matching(_AMEND_SHEET), key=lambda d: d.version, reverse=True)
    for doc in sheets:
        found: dict[int, tuple[set[int], str]] = {}
        for m in re.finditer(_AMEND_CALL, doc.text):
            n, quarter, month, year = int(m.group(1)), m.group(2), m.group(3), m.group(4)
            if quarter:
                q = int(quarter)
                months, said = set(range(3 * q - 2, 3 * q + 1)), f"{q}. Quartal {year}"
            else:
                idx = next(i for i, x in enumerate(_MONTHS, 1) if x.lower() == month.lower())
                months, said = {idx}, f"{_MONTHS[idx - 1]} {year}"
            found.setdefault(n, ({m_ + int(year) * 100 for m_ in months}, said))
        if found:
            return found, doc
    return {}, None


def _quantities(ctx: TenderContext, row: str, lv_qty: int | float | None) -> str | None:
    bits = []
    mq = _MIN_QTY.search(row)
    calls = list(_CALL_OFF.finditer(row))
    unit = next((c.group(5) for c in calls if c.group(5)), None)
    u = f" {unit}" if unit else ""
    mx = _MAX_AFTER.match(row, calls[-1].end()) if calls else None
    max_qty = _num(mx.group(1)) if mx else None
    call_txt = ", ".join(f"{c.group(2)}/{c.group(3)}: {c.group(4)}" for c in calls)
    if mq:
        bit = f"Mindestabnahme {mq.group(1)}{u}" + (f" von max. {mx.group(1)}{u}" if mx else "")
        bit += f" (Abrufe {call_txt})" if calls else ""
        bits.append(bit)
    elif calls:
        bits.append(f"Abrufe {call_txt}")
    if max_qty is not None and lv_qty is not None and max_qty != lv_qty:
        bits.append(f"Achtung: Höchstmenge im Produktblatt ({mx.group(1)}) weicht vom LV ab")
    # call-off dates contradicted by an amendment sheet (Q&A answers override the original sheet)
    amended, sheet = _amended_call_offs(ctx) if calls else ({}, None)
    for c in calls:
        said = amended.get(int(c.group(1)))
        if said and int(c.group(3)) * 100 + int(c.group(2)) not in said[0]:
            bits.append(f"Achtung: {c.group(1)}. Abruf laut Produktblatt {c.group(2)}/{c.group(3)}, laut "
                        f"Hinweisblatt ({sheet.folder or sheet.filename}) im {said[1]}")
    shelf = _SHELF.search(row)
    if shelf:
        bits.append(f"Mindesthaltbarkeit {shelf.group(1)} Monate ab Wareneingang")
    pack = _PACK_SIZE.search(row)
    if pack:
        bits.append(f"max. {pack.group(1)} {pack.group(2)} pro Packung")
    return "; ".join(bits) + "." if bits else None


def extract(ctx: TenderContext) -> list[dict]:
    lots = _from_lv(ctx)
    for n, item in _from_pages(ctx).items():
        lots.setdefault(n, item)
    if not lots:
        lots = _from_sheet_names(ctx)
    if not lots:
        return _positions(ctx)

    items = [lots[n] for n in sorted(lots, key=int)]
    for item in items:
        found = _sheet_row(ctx, item["lot"])
        if not found:
            continue
        _, _, row = found
        desc = " ".join(x for x in (_spec(row), _quantities(ctx, row, item["quantity"])) if x)
        item["description"] = desc or None
    return items
