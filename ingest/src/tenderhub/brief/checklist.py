"""Brief section: checklist — the documents the bidder must hand in (the platform's "SWZ checklist").

Sources, in the order a bid manager works through them:

1. The "Aufforderung zur Angebotsabgabe" (EinkaufBw / BAAINBw template), chapter "Mit dem Angebot einzureichende
   Unterlagen": a numbered table of Anlagen where a ticked box (☒) means "vom Bieter ausgefüllt mit dem Angebot zu
   überreichen", followed by a free list of further Unterlagen/Eignungsnachweise ("Bezeichnung der einzureichenden
   Unterlagen"). Each table row is matched to the template file shipped with the tender; a row that covers several
   per-lot files ("Preis- und Produktblatt Los/e XX") becomes one item per lot.
2. The Bewertungsmatrix: every "Nachweisart: Eigenerklärung des Bieters; hier: Bieterangabe zu …" is a declaration
   that earns performance points, so it belongs on the checklist (optional, with the offer).
3. Conditional and later documents: translations of foreign-language documents, the Verpflichtungserklärung of
   Dritte/Nachunternehmer, the register extracts and certificates the exclusion declaration (B-V 034) promises
   "auf Aufforderung", and catalogue data due after the framework agreement is concluded.

Item fields: see docs/brief-format.md ("checklist").
"""

from __future__ import annotations

import re
from calendar import monthrange
from collections import Counter
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import Doc, TenderContext, cite

_CHECKED, _UNCHECKED = "☒", "☐"
_WHEN_RANK = {"with_offer": 0, None: 1, "on_request": 2, "after_award": 3}
_OBLIGATION_RANK = {"mandatory": 0, "if_applicable": 1, "on_request": 2}
_STOP = {
    "und", "oder", "der", "die", "das", "den", "dem", "des", "zum", "zur", "von", "mit", "fuer", "gem", "gemaess",
    "anlage", "anlagen", "vertrag", "vertrages", "formular", "sofern", "zutreffend", "erklaerung", "betreffend",
    "einer", "eines", "los", "lose", "baainbw", "gwb", "einkaufbw", "iud", "stand", "version", "pdf", "docx", "xlsx",
}
_CONDITIONAL = re.compile(r"^\s*(?:ggf\.|gegebenenfalls|sofern zutreffend:?|soweit zutreffend:?|falls zutreffend:?)\s*",
                          re.I)
_HEADING = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+[A-ZÄÖÜ]")


# --------------------------------------------------------------------------------------------------- helpers

def _norm(s: str) -> str:
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return s


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", _norm(s)) if len(t) >= 3 and t not in _STOP and not t.isdigit()}


def _dehyphenate(s: str) -> str:
    """Undo word-wrap hyphens the PDF extraction leaves inside words ("Aus-schlussgründe", "Qualitäts-\\nsystem")."""
    s = re.sub(r"(?<=[a-zäöüß])-\s*\n\s*(?=[a-zäöüß])", "", s)
    s = re.sub(r"(?<=[a-zäöüß])-(?=[a-zäöüß])", "", s)
    return " ".join(s.split())


def _lines(doc: Doc) -> list[tuple[int | None, str]]:
    """(page, line) for the whole document, without running headers/footers repeated on several pages."""
    out = [(page, line) for page, chunk in doc.page_texts() for line in chunk.splitlines()]
    seen: dict[str, set] = {}
    for page, line in out:
        key = re.sub(r"\d+", "#", " ".join(line.split()))
        if key:
            seen.setdefault(key, set()).add(page)
    boiler = {k for k, pages in seen.items() if len(pages) >= 2 and len(k) < 80}
    return [(p, ln) for p, ln in out if re.sub(r"\d+", "#", " ".join(ln.split())) not in boiler]


def _item(document: str, obligation: str, when: str | None, template: str | None, lot: str | None,
          source: Doc | str, page: int | None, quote: str) -> dict:
    return {"document": document, "obligation": obligation, "when": when, "template": template, "lot": lot,
            "cite": cite(source, page, quote)}


def _with_offer_phrase(text: str) -> bool:
    return bool(re.search(r"mit\s+dem\s+Angebot\s+(?:e\s?inzureichen|zu\s+überreichen|beizufügen|vorzulegen)", text))


# ---------------------------------------------------------------------------------- 1. Aufforderung zur Angebotsabgabe

def _clean_label(raw: str) -> str:
    s = _dehyphenate(raw)
    s = _CONDITIONAL.sub("", s)
    s = re.sub(r"\(\s*(?:BAAINBw-?\s*)?B-V\s*0*(\d+)\s*/[^)]*\)", lambda m: f"(B-V {int(m.group(1)):03d})", s)
    s = re.sub(r"\s*\((?:EinkaufBw|Stand)[^)]*\)", "", s)
    return s.strip(" :–-")


def _form_code(raw: str) -> str | None:
    m = re.search(r"B-V\s*0*(\d{2,3})\b", raw)
    return m.group(1) if m else None


def _lot_of(filename: str) -> str | None:
    m = re.search(r"(?<![a-z])los[\s_]*(\d+)", filename, re.I)
    return m.group(1) if m else None


def _templates(ctx: TenderContext, label: str, number: str | None, exclude: Doc) -> list[Doc]:
    """Template file(s) for an Anlage: form code (B-V 034 -> "B-V034_…") or shared filename words; several per-lot
    files when they tie (Preis- und Produktblatt Los 1/2/3)."""
    code = _form_code(label)
    want = _tokens(_clean_label(label))
    scored: list[tuple[float, Doc]] = []
    for d in ctx.docs_matching(r".", latest_only=True):
        if d is exclude or d.filename.startswith("_"):
            continue
        score = float(len(want & _tokens(d.filename)))
        if code and re.search(rf"B-?V[\s_]*0*{int(code)}(?!\d)", d.filename, re.I):
            score += 10
        if score and number and re.search(rf"(?:^|[^0-9]){number}(?:[^0-9]|$)", d.filename):
            score += 0.5
        if score >= 1:
            scored.append((score, d))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    top = [d for s, d in scored if s == best]
    if len(top) > 1 and all(_lot_of(d.filename) for d in top):
        return sorted(top, key=lambda d: int(_lot_of(d.filename) or 0))
    return top[:1]


def _per_item_note(doc: Doc, auff: Doc) -> str:
    """Who fills in / signs the form, as the template or the Aufforderung states it."""
    if re.search(r"Für\s+jeden\s+Artikel\s+ist\s+ein\s+separate", doc.text):
        return " (je Artikel ein Blatt)"
    if re.search(r"von\s+allen\s+Bewerbern\s*/\s*Bietern\s*/\s*allen\s+Mitgliedern", doc.text):
        return " (von jedem Bieter bzw. jedem Mitglied einer Bietergemeinschaft)"
    if re.search(r"Bietergemeinschaft", doc.filename + doc.text[:300]) and re.search(
            r"Erklärung\s+ist\s+von\s+allen\s+Mitgliedern\s+der\s+Bietergemeinschaft\s+zu\s+unterschreiben",
            _dehyphenate(auff.text)):
        return " (von allen Mitgliedern zu unterschreiben)"
    return ""


def _anlagen_table(ctx: TenderContext, auff: Doc) -> list[dict]:
    lines = _lines(auff)
    start = next((i for i, (_, ln) in enumerate(lines) if re.search(r"Bezeichnung der Anlage", ln)), None)
    if start is None:
        start = next((i for i, (_, ln) in enumerate(lines) if re.search(r"e\s?inzureichende\s+Unterlagen", ln, re.I)),
                     None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines))
                if re.search(r"Bezeichnung der einzureichenden Unterlagen|^\s*Neben den oben", lines[i][1])
                or re.match(r"^\s*1\.5\s", lines[i][1])), len(lines))
    rows: list[tuple[str, int | None, list[str]]] = []
    for page, ln in lines[start + 1:end]:
        m = re.match(r"^\s*(\d{2})(?:\s+|$)(.*)$", ln)
        if m:
            rows.append((m.group(1), page, [ln]))
        elif rows:
            rows[-1][2].append(ln)
    items: list[dict] = []
    for number, page, row_lines in rows:
        raw = "\n".join(row_lines)
        boxes = re.search(f"[{_CHECKED}{_UNCHECKED}]", raw)
        if not boxes or boxes.group() != _CHECKED:
            continue                                          # informational Anlage, not handed in
        before = raw[:boxes.start()]
        label_raw = re.sub(r"^\s*\d{2}\s+", "", before).strip()
        quote = raw[:boxes.end()]
        obligation = "if_applicable" if _CONDITIONAL.match(label_raw) else "mandatory"
        label = _clean_label(label_raw)
        templates = _templates(ctx, label_raw, number, auff)
        if len(templates) > 1:
            for t in templates:
                lot = _lot_of(t.filename)
                name = re.sub(r"Los/?e?\s*X+", f"Los {lot}", label) if re.search(r"Los/?e?\s*X+", label) \
                    else f"{label} – Los {lot}"
                items.append(_item(name, obligation, "with_offer", t.filename, lot, auff, page, quote))
            continue
        tpl = templates[0] if templates else None
        name = label + (_per_item_note(tpl, auff) if tpl else "")
        items.append(_item(name, obligation, "with_offer", tpl.filename if tpl else None, None, auff, page, quote))
    return items


def _further_documents(auff: Doc, deadline: date | None = None) -> list[dict]:
    """The free list "Bezeichnung der einzureichenden Unterlagen" (register extract, certificates, …)."""
    lines = _lines(auff)
    start = next((i for i, (_, ln) in enumerate(lines) if re.search(r"Bezeichnung der einzureichenden Unterlagen", ln)),
                 None)
    if start is None:
        return []
    entries: list[tuple[int | None, str]] = []
    for page, ln in lines[start + 1:]:
        text = ln.strip()
        if not text:
            continue
        if _HEADING.match(text) and not re.match(r"^\d{4}/", text):
            break
        prev = entries[-1][1] if entries else ""
        if entries and (text[0].islower() or text[0].isdigit() or prev.count("(") > prev.count(")")):
            entries[-1] = (entries[-1][0], f"{prev}\n{text}")
        else:
            entries.append((page, text))
    out = []
    with_offer = "with_offer" if re.search(r"dem\s+Angebot\s+folgende\s+weitere\s+Unterlagen",
                                                    _dehyphenate(auff.text)) else None
    for page, raw in entries:
        obligation = "if_applicable" if _CONDITIONAL.match(raw) else "mandatory"
        name = _dehyphenate(_CONDITIONAL.sub("", raw))
        name = name[:1].upper() + name[1:]
        name = _issued_after(name, deadline)
        out.append(_item(name, obligation, with_offer, None, None, auff, page, raw))
    return out


def _deadline_date(ctx: TenderContext) -> date | None:
    raw = ctx.notice.get("deadline_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return dt.astimezone(ZoneInfo("Europe/Berlin")).date() if dt.tzinfo else dt.date()


def _issued_after(name: str, deadline: date | None) -> str:
    """"nicht älter als 6 Monate, gerechnet ab Angebotsfrist" -> also say the earliest issue date that still counts."""
    m = re.search(r"nicht\s+älter\s+als\s+(\d+)\s+Monate.{0,40}?Angebotsfrist\)?", name)
    if not m or not deadline:
        return name
    months = deadline.year * 12 + deadline.month - 1 - int(m.group(1))
    year, month = divmod(months, 12)
    day = min(deadline.day, monthrange(year, month + 1)[1])
    earliest = date(year, month + 1, day)
    cut = m.end() - 1 if m.group().endswith(")") else m.end()
    return f"{name[:cut]}: ausgestellt am {earliest:%d.%m.%Y} oder später{name[cut:]}"


def _translation(auff: Doc) -> list[dict]:
    for page, m in auff.find(r"Fremdsprachen\s+ist\s+eine.{0,80}?ins\s+Deutsche\s+beizufügen", re.S):
        certified = " (auf Aufforderung beglaubigt)" if re.search(r"Aufforderung\s+auch\s+beglaubigt", m.group()) else ""
        return [_item(f"Deutsche Übersetzung fremdsprachiger Dokumente{certified}", "if_applicable", "with_offer",
                      None, None, auff, page, m.group())]
    return []


def _subcontractor_commitment(auff: Doc) -> list[dict]:
    """Eignungsleihe: the Dritte / Nachunternehmer hand in the bidder's declarations and proofs too, plus a signed
    Verpflichtungserklärung. The documents do not say when (the text speaks of a Teilnahmewettbewerb), so when=None."""
    out = []
    for page, m in auff.find(r"auf\s+Ressourcen\s+von\s+Dritten\s*/\s*Nachunternehmern\s+zurückgreifen\s+will,\s*"
                             r"sind\s+die\s+geforderten\s+Bietererklärungen\s+und\s+Eignungsnachweise\s+ebenfalls\s+"
                             r"von\s+den\s+Drit-?\s*ten\s*/\s*Nachunternehmern\s+vorzulegen", re.S):
        out.append(_item("Geforderte Bietererklärungen und Eignungsnachweise auch für Dritte / Nachunternehmer, "
                         "auf deren Ressourcen zur Eignung zurückgegriffen wird", "if_applicable", None, None, None,
                         auff, page, m.group()))
        break
    for page, m in auff.find(r"Von\s+Dritten\s*/\s*Nachunternehmern\s+ist\s+eine[^.]{0,200}?\(Verpflichtungserklärung\)",
                             re.S):
        out.append(_item("Verpflichtungserklärung der Dritten / Nachunternehmer (formlos, unterschrieben)",
                         "if_applicable", None, None, None, auff, page, m.group()))
        break
    return out


def _preference_certificates(ctx: TenderContext) -> list[dict]:
    """B-V 044: a bidder claiming preference (Werkstatt für Behinderte / Blindenwerkstatt) attaches the certificates."""
    form = next(iter(ctx.docs_matching(r"bevorzugt|B-?V[\s_]*0*44(?!\d)")), None)
    if not form:
        return []
    for page, m in form.find(r"Die\s+nach\s+(§\s*\d+\s+der\s+Richtlinien?)\s+vorgeschriebenen\s+Bescheinigungen\s+"
                             r"sind\s+in\s+beglaubigter\s+Abschrift\s+beigefügt", re.S):
        return [_item(f"Bescheinigungen nach {' '.join(m.group(1).split())} zur bevorzugten Berücksichtigung "
                      "(beglaubigte Abschrift; nur wenn bevorzugte Berücksichtigung beansprucht wird)",
                      "if_applicable", "with_offer", None, None, form, page, m.group())]
    return []


def _equivalent_product_note(ctx: TenderContext) -> list[dict]:
    """Price sheets: an offer of an equivalent product instead of the reference product describes the deviations."""
    for d in ctx.docs_matching(r"Preis.{0,5}und.{0,5}Produktblatt"):
        for page, m in d.find(r"Abweichungen\s+zum\s+Richtfabrikat\s+sollen\s+vom\s+Bieter\s+beschrieben\s+und\s+"
                              r"sein\s+Einfluss\s+auf\s+die\s+Zweckbestimmung\s+nach\s+Möglichkeit\s+bewertet\s+werden",
                              re.S):
            return [_item("Beschreibung der Abweichungen zum Richtfabrikat und ihres Einflusses auf die "
                          "Zweckbestimmung (nur bei Angebot eines gleichwertigen Produkts)",
                          "if_applicable", "with_offer", None, None, d, page, m.group())]
    return []


# ------------------------------------------------------------------------------------------------ 2. Bewertungsmatrix

_MATRIX_PROOF = re.compile(
    r"(\d+)\s*\n\s*(Eigenerklärung\s+des\s+Bieters;\s*hier:\s*(.+?))"
    r"(?=(?<!§)(?<!§ )\n\s*\d+(?:\s*\n|\s+[A-ZÄÖÜ])|\n\s*\d+\.\s|\Z)", re.S)


def _matrix_declarations(ctx: TenderContext, auff: Doc | None) -> list[dict]:
    matrix = ctx.doc(r"Bewertungsmatrix")
    if not matrix:
        return []
    rule = auff and re.search(r"Eigenerklärungen\s+sind\s+(\(ggf\.[^)]*\)\s+)?mit\s+dem\s+Angebot", auff.text)
    when = "with_offer" if rule else None
    per_lot = ", ggf. je Los" if rule and rule.group(1) and re.search(r"pro\s+Los", rule.group(1)) else ""
    out = []
    for n, (page, m) in enumerate(matrix.find(_MATRIX_PROOF), start=1):
        topic = _dehyphenate(m.group(3))
        topic = re.sub(r"^Bieterangabe\s+zu\s+dem\b", "Bieterangabe zum", topic)
        topic = re.sub(r"^Bieterangabe\s+zu\s+der\b", "Bieterangabe zur", topic)
        out.append(_item(f"Eigenerklärung – {topic} (Leistungskriterium {n}, bis {m.group(1)} Leistungspunkte{per_lot})",
                         "if_applicable", when, None, None, matrix, page, m.group(2)))
    return out


# --------------------------------------------------------------------------------- 3. on request / after award

_ON_REQUEST = (
    ("Strafregister", "Auszug aus dem Strafregister (oder gleichwertige Urkunde) für zurechenbare Personen"),
    ("Insolvenzregister", "Erklärung der Stelle, die das Insolvenzregister führt"),
)
_TAXES = "Bescheinigung der zuständigen Behörde über Zahlung von Steuern, Abgaben und Sozialversicherungsbeiträgen"


def _short_quote(text: str, limit: int = 220) -> str:
    """Quote cut at a word boundary (a quote ending in a half word reads like an extraction error)."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0]


def _exclusion_proofs(ctx: TenderContext) -> list[dict]:
    """Proofs the exclusion self-declaration (§§ 123, 124 GWB) promises to bring "auf Aufforderung"."""
    decl = next(iter(ctx.docs_matching(r"Ausschlussgr|B-?V[\s_]*0*34(?!\d)")), None)
    if not decl:
        return []
    out, done = [], set()
    for page, m in decl.find(r"auf\s+Aufforderung\s+des\s+Auftraggebers.{0,400}?beibringen", re.S):
        window = m.group()
        names = [name for key, name in _ON_REQUEST if key in window]
        if not names:   # the certificate is only named by the paragraph it proves (taxes / social security)
            pos = decl.text.find(window)
            if re.search(r"Steuern.{0,200}Sozialversicherung", decl.text[max(0, pos - 800):pos], re.S):
                names = [_TAXES]
        for name in names:
            if name not in done:
                done.add(name)
                out.append(_item(name, "on_request", "on_request", None, None, decl, page, _short_quote(window)))
    return out


def _insurance_proof(ctx: TenderContext) -> list[dict]:
    """Draft contract: liability insurance the contractor keeps and proves "auf Anforderung"."""
    for d in ctx.docs_matching(r"Entwurf|Vertrag|RV"):
        pages = d.page_texts()
        for i, (page, chunk) in enumerate(pages):
            m = re.search(r"(?:Betriebs|Berufs)?[Hh]aftpflichtversicherung\b.{0,400}?(?:abzuschließen|zu\s+unterhalten)",
                          chunk + (pages[i + 1][1] if i + 1 < len(pages) else ""), re.S)
            if not m or m.start() >= len(chunk):
                continue
            after = (chunk + (pages[i + 1][1] if i + 1 < len(pages) else ""))[m.end():m.end() + 80]
            if not re.search(r"^\s*und\s+dem\s+AG\s+auf\s+(?:Anforderung|Verlangen)\s+nachzuweisen", after):
                continue
            body = chunk[m.start():]
            sums = re.findall(r"(\d+(?:,\d+)?\s+Millionen\s+€\s+für\s+[^•\n]+?)(?:\s+und)?\s*(?:\n|$)", body)
            kind = re.match(r"\w+", m.group()).group()
            name = f"Nachweis der {kind}" + (f" ({'; '.join(' '.join(x.split()) for x in sums)})" if sums else "")
            quote = re.search(r"[^.]*?" + re.escape(kind) + r"[^.]*", chunk[max(0, m.start() - 120):], re.S)
            return [_item(name, "on_request", "on_request", None, None, d, page,
                          _short_quote(quote.group() if quote else m.group(), 260))]
    return []


def _catalogue_data(ctx: TenderContext) -> list[dict]:
    docs = sorted(ctx.docs_matching(r"."), key=lambda d: 0 if re.search(r"Entwurf|Vertrag|RV", d.filename) else 1)
    template = ctx.doc(r"Vorlage.{0,5}Katalog|Katalog.*(?:Vorlage|eClass)")
    for d in docs:
        if template and d is template:
            continue
        for page, m in d.find(r"(unverzüglich\s+)?nach\s+(Abschluss|Zuschlag|Vertragsschluss|Auftragserteilung)\b"
                              r"(\s+der\s+\w+)?[^.]{0,80}?(?:Bestellkatalog|Katalogdaten)\w*", re.S):
            name = "Katalogdaten für den elektronischen Bestellkatalog"
            if re.search(r"Kaufhaus des Bundes", d.text):
                name += " (Kaufhaus des Bundes)"
            due = " ".join(f"{m.group(1) or ''}nach {m.group(2)}{m.group(3) or ''}".split())
            return [_item(f"{name} – {due}", "mandatory", "after_award", template.filename if template else None,
                          None, d, page, m.group())]
    return []


def _contract_proofs(ctx: TenderContext, known: list[dict]) -> list[dict]:
    """Proofs the draft contract demands "mit dem Angebot" (that the Aufforderung does not list already) or
    "mit (jeder) Lieferung"."""
    out = []
    have = {_norm(w) for i in known for w in re.findall(r"[\wäöüß-]+", i["document"])}
    for d in ctx.docs_matching(r"Entwurf|Vertrag"):
        for page, m in d.find(r"legt\s+dem\s+AG\s+mit\s+(dem\s+Angebot|(?:jeder\s+)?Lieferung)[^.]{0,40}?\s+"
                              r"(?:eine?n?|die|den|das)\s+([A-ZÄÖÜ][\wäöüß\s-]{2,80}?)\s+vor\b"
                              r"(?:,\s*die\s+ergibt,\s*dass\s+([^.]{5,160}))?", re.S):
            noun = " ".join(m.group(2).split())
            if _norm(noun) in have:
                continue
            have.add(_norm(noun))
            name = noun
            if m.group(3):
                clause = re.sub(r"\s+-", "-", " ".join(m.group(3).split()))
                name = f"{noun}, dass {clause}"
            with_offer = "Angebot" in m.group(1)
            if not with_offer:
                name += " – mit jeder Lieferung"
                if re.search(r"Vermerks?\s+auf\s+allen\s+Ausfertigungen\s+des\s+Lieferscheins",
                             m.string[m.end():m.end() + 200]):
                    name += " (Vermerk auf dem Lieferschein)"
            out.append(_item(name, "mandatory", "with_offer" if with_offer else "after_award", None, None, d, page,
                             m.group()))
    return out


# --------------------------------------------------------------------------------------------------- extract

def extract(ctx: TenderContext) -> list[dict]:
    auff = ctx.doc(r"Aufforderung.{0,5}zur.{0,5}Angebotsabgabe")
    items: list[dict] = []
    if auff:
        items += _anlagen_table(ctx, auff)
        further = _further_documents(auff, _deadline_date(ctx))
    else:
        further = []
    declarations = _matrix_declarations(ctx, auff)
    if declarations:   # the generic "Ggf. Eigenerklärungen gem. Bewertungsmatrix" line is replaced by the detail
        further = [i for i in further if not re.search(r"Bewertungsmatrix", i["document"])]
    items += further + declarations
    items += _contract_proofs(ctx, items)
    items += _preference_certificates(ctx) + _equivalent_product_note(ctx)
    if auff:
        items += _translation(auff) + _subcontractor_commitment(auff)
    items += _exclusion_proofs(ctx) + _insurance_proof(ctx) + _catalogue_data(ctx)

    seen: Counter = Counter()
    unique = []
    for it in items:
        key = (_norm(it["document"]), it["lot"])
        if seen[key]:
            continue
        seen[key] += 1
        unique.append(it)
    unique.sort(key=lambda i: (_WHEN_RANK.get(i["when"], 1), _OBLIGATION_RANK.get(i["obligation"], 1)))
    return unique
