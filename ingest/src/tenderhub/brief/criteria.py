"""Brief section: criteria — award criteria and weights. See docs/brief-format.md for the item fields.

Sources, best first:
- "Aufforderung zur Angebotsabgabe", section "Zuschlagskriterien": the main criteria with their weights
  (table "Zuschlagskriterium  Gewichtung"), the tie-break order ("Priorität 1: …") and the Losentscheid.
- "Bewertungsmatrix": scoring method/formula, the fixed values (w, Lmax), the price basis and every
  Leistungskriterium with its scale (a) … = N Leistungspunkte) and the kind of proof.
- The notice pages (e-Vergabe details / TED, section 5.1.10): fallback for the weights, per lot, and the
  § 56 Abs. 3 VgV rule that award-relevant documents cannot be requested later.

Item order: main criteria by weight, then sub-criteria by points, then how the score is computed and ties broken.
"""

from __future__ import annotations

import re
from typing import Any

from . import Doc, TenderContext, cite

_NUM = r"\d+(?:[.,]\d+)?"
_PAGE_MARK = re.compile(r"^\[page (\d+)\]$", re.M)
_LOT_IN_NAME = re.compile(r"(?<![^\W_])Los[ _-]*(\d+)(?!\d)", re.I)  # "_LOS 1_", "Los-2", not "Losentscheid"


def _num(s: str) -> int | float:
    v = float(s.replace(".", "").replace(",", ".")) if "," in s else float(s)
    return int(v) if v.is_integer() else v


def _fmt(v: int | float) -> str:
    return str(v).replace(".", ",")


def _clean(s: str) -> str:
    """One line: hyphenated line breaks joined ("Qualitätsmanagement-\\nsystem"), whitespace collapsed."""
    s = re.sub(r"(\w)-\s*\n\s*([a-zäöüß])", r"\1\2", s)
    return " ".join(s.split()).strip()


def _page_at(doc: Doc, pos: int) -> int | None:
    """Page number of a character offset in doc.text (from the "[page N]" markers)."""
    page = None
    for m in _PAGE_MARK.finditer(doc.text):
        if m.start() > pos:
            break
        page = int(m.group(1))
    return page


def _unit(s: str) -> str:
    return "%" if re.match(r"%|Prozent", s, re.I) else "points"


def _item(name: str, weight: Any, unit: str | None, lot: str | None, description: str | None, c: dict) -> dict:
    return {"name": name, "weight": weight, "weight_unit": unit if weight is not None else None, "lot": lot,
            "description": description or None, "cite": c}


# --- Aufforderung zur Angebotsabgabe -------------------------------------------------------------------------------

_WEIGHT_TABLE = re.compile(r"Zuschlagskriteri(?:um|en)[ \t]+Gewichtung[^\n]*\n", re.I)
_WEIGHT_ROW = re.compile(rf"^[ \t]*([A-ZÄÖÜ][^\n%]*?)[ \t]+({_NUM})[ \t]*(%|Prozent|Punkte|Pkt\.?)[ \t]*$", re.M)
_PRICE_ONLY = re.compile(r"(?<!wenn )(?<!sofern )(?:einziges|alleiniges) Zuschlagskriterium ist (?:der )?Preis"
                         r"|Zuschlag[^.\n]{0,60}ausschlie(?:ß|ss)lich[^.\n]{0,30}(?:Preis|niedrigsten Angebotspreis)",
                         re.I)
_PRIORITY = re.compile(r"Priorit(?:ä|ae)t\s*(\d+)\s*:\s*([^\n]+)", re.I)


def _main_weights(doc: Doc) -> list[dict]:
    out: list[dict] = []
    for m in _WEIGHT_TABLE.finditer(doc.text):
        rest = doc.text[m.end(): m.end() + 600]
        stop = re.search(r"\n\s*\n\s*\S|Die Einzelheiten|^\s*\d+\.\s", rest, re.M)
        block = rest[: stop.start()] if stop else rest
        for r in _WEIGHT_ROW.finditer(block):
            out.append({"name": _clean(r.group(1)), "weight": _num(r.group(2)), "unit": _unit(r.group(3)),
                        "page": _page_at(doc, m.end() + r.start()), "quote": r.group(0)})
        if out:
            return out
    for page, m in doc.find(_PRICE_ONLY):
        return [{"name": "Preis", "weight": 100, "unit": "%", "page": page, "quote": m.group(0)}]
    return out


def _tie_break(doc: Doc) -> dict | None:
    prio = doc.find(_PRIORITY)
    if not prio:
        return None
    order = [f"{int(m.group(1))}. {_clean(m.group(2))}" for _, m in sorted(prio, key=lambda pm: int(pm[1].group(1)))]
    per_lot = " (je Los)" if doc.find(r"Gesamtpunktzahl für mehrere\s+Ange-?\s*bote auf ein Los") else ""
    desc = f"Bei gleicher Gesamtpunktzahl{per_lot} entscheidet: " + ", ".join(order)
    if doc.find(r"Los-?\s*entscheid"):
        desc += ", danach Losentscheid"
    page, first = prio[0]
    return _item("Gleichstand (Tie-Break)", None, None, None, desc, cite(doc, page, first.group(0)))


_EVAL_BASIS = re.compile(r"Die Wertung der Angebote erfolgt ausschlie(?:ß|ss)lich über die Angaben\s+im\s+([^\n]*?Preis-?\s*und\s+"
                         r"Produktblatt)\s+sowie die mittels Eigenerklärungen", re.I)
_PROOF_WITH_OFFER = re.compile(r"Die als Nachweis dienenden Eigenerklärungen sind\s*(\(ggf\.\s*pro\s+Los\))?\s*mit dem\s+"
                               r"Angebot einzureichen\.", re.I)


def _evaluation_rules(doc: Doc) -> list[dict]:
    """What is evaluated (price sheet + self-declarations only) and when the declarations must be handed in."""
    out = []
    if found := doc.find(_EVAL_BASIS):
        page, m = found[0]
        out.append(_item("Wertungsgrundlage", None, None, None,
                         f"Gewertet werden ausschließlich die Angaben im {_clean(m.group(1))} (Preis) und die Eigenerklärungen "
                         "zu den Leistungskriterien der Bewertungsmatrix (Leistung)", cite(doc, page, m.group(0))))
    if found := doc.find(_PROOF_WITH_OFFER):
        page, m = found[0]
        out.append(_item("Eigenerklärungen zu den Leistungskriterien mit dem Angebot", None, None, None,
                         "Die Eigenerklärungen, die die Leistungspunkte belegen, sind " + ("(ggf. pro Los) " if m.group(1) else "")
                         + "zusammen mit dem Angebot einzureichen", cite(doc, page, m.group(0))))
    return out


# --- Preis- und Produktblatt: the minimum shelf life a "Verfalldatum übertrifft …" criterion is measured against --------

_SHELF_HEADER = re.compile(r"Mindesthaltbarkeit(?:\s+ab\s+dem\s+Zeitpunkt\s+des\s+Wareneingangs(?:\s+bei\s+der\s+\w+)?)?", re.I)
_MONTH_RANGE = re.compile(r"(\d+)\s*(?:-|–|bis)\s*(\d+)\s*Monate")
_MONTH_MORE = re.compile(r"mehr als\s*(\d+)\s*Monate")


def _shelf_life_baselines(ctx: TenderContext, rows: list[dict]) -> list[dict]:
    """Per lot: the Mindesthaltbarkeit in the price sheet, and the month bands the Verfalldatum points translate to."""
    expiry = next((r for r in rows if re.search(r"Verfall|Haltbarkeit", r["title"], re.I)), None)
    if not expiry:
        return []
    out = []
    for d in ctx.docs_matching(r"Preis.*Produktblatt"):
        lot_m = _LOT_IN_NAME.search(d.filename)
        header = d.find(_SHELF_HEADER)
        if not lot_m or not header:
            continue
        lot = lot_m.group(1)
        # the lot's own row: a line starting with the lot number after the example row ("Beispiel …")
        ex = re.search(r"^\s*Beispiel\b", d.text, re.M | re.I)
        row = re.compile(rf"^\s*{lot}[ \t]+[A-ZÄÖÜ][^\n]*", re.M).search(d.text, ex.end() if ex else 0)
        if not row:
            continue
        stop = re.search(r"\nOrt,\s*Datum|\nRichtfabrikat[^\n]{0,40}= |\[page", d.text[row.end():])  # signature block / legend
        chunk = d.text[row.start(): row.end() + (stop.start() if stop else 1500)]
        months = re.search(r"(\d+)\s*Monate\b", chunk)
        page = _page_at(d, row.start())
        if months:
            n = int(months.group(1))
            bands = []
            for pts, txt in expiry["scale"]:
                pts = _fmt(pts)
                r, more = _MONTH_RANGE.search(txt), _MONTH_MORE.search(txt)
                if r:
                    bands.append(f"{pts} P bei {n + int(r.group(1))}-{n + int(r.group(2))} Monaten")
                elif more:
                    bands.append(f"{pts} P ab mehr als {n + int(more.group(1))} Monaten")
            basis = _clean(header[0][1].group(0))[len("Mindesthaltbarkeit"):].strip()
            desc = f"Mindesthaltbarkeit {n} Monate" + (f" {basis}" if basis else "") + " (Preis- und Produktblatt)"
            if bands:
                desc += "; Restlaufzeit bei Lieferung für Leistungspunkte: " + ", ".join(bands)
            out.append(_item("Bezugswert Verfalldatum-Kriterium", None, None, lot, desc, cite(d, page, months.group(0))))
        else:
            hp, hm = header[0]
            out.append(_item("Bezugswert Verfalldatum-Kriterium", None, None, lot,
                             "Im Preis- und Produktblatt ist für dieses Los keine Mindesthaltbarkeit eingetragen: Bezugswert "
                             "für das Verfalldatum-Kriterium unklar (Bieterfrage empfohlen)", cite(d, hp, hm.group(0))))
    return sorted(out, key=lambda it: int(it["lot"]))


# --- Bewertungsmatrix ----------------------------------------------------------------------------------------------

_OPTION = re.compile(rf"^([a-z])\)\s*(.*?)\s*=\s*({_NUM})\s*(?:Leistungs)?punkt", re.I | re.S)


def _fixed_values(doc: Doc) -> tuple[dict[str, int | float], int | None, str | None]:
    """The "Fixe Werte für das Verfahren" table (w, Lmax, (1-w)) — not the worked examples that follow it."""
    m = re.search(r"Fixe Werte[^\n]*\n(.{0,300}?)(?=\n\s*\n|\[page|\nBeispiel|\Z)", doc.text, re.S)
    if not m:
        return {}, None, None
    vals: dict[str, int | float] = {}
    for key, rx in (("w", r"^\s*w\s+(%s)\s*$"), ("lmax", r"^\s*Lmax\s+(%s)\s*$"), ("price", r"^\s*\(1\s*-\s*w\)\s+(%s)\s*$")):
        v = re.search(rx % _NUM, m.group(1), re.M)
        if v:
            vals[key] = _num(v.group(1))
    return vals, _page_at(doc, m.start()), _clean(m.group(1))[:120]


def _legend(doc: Doc) -> str | None:
    """What L, Pmin and P stand for, from the symbol table ("Pmin Niedrigster Angebotspreis") above the matrix."""
    end = re.search(r"Ermittlung der Leistungspunkte", doc.text)
    head = doc.text[: end.start()] if end else doc.text
    found = []
    for sym in ("L", "Pmin", "P"):
        m = re.search(rf"^\s*{sym}[ \t]+([A-ZÄÖÜ][\wäöüß]*(?: [\wäöüß]+){{0,3}})[ \t]*$", head, re.M)
        if m:
            found.append(f"{sym} = {m.group(1)}")
    return ", ".join(found) if found else None


def _rows(doc: Doc) -> list[dict]:
    """Leistungskriterien from section "Ermittlung der Leistungspunkte": numbered rows with a)…=N scale."""
    start = re.search(r"Ermittlung der Leistungspunkte", doc.text)
    if not start:
        return []
    end = re.search(r"\n\s*\d+\.\s*Fixe Werte|\nFixe Werte|\nBeispiele", doc.text[start.end():])
    block_start = start.end()
    block = doc.text[block_start: block_start + end.start()] if end else doc.text[block_start:]
    head = re.search(r"auszuf(?:ü|ue)llen\)\s*\n", block)  # end of the table header
    if head:
        block_start += head.end()
        block = block[head.end():]

    # split into rows: a line starting with the next running number
    lines, pos = [], 0
    for ln in block.split("\n"):
        lines.append((block_start + pos, ln.strip()))
        pos += len(ln) + 1
    rows: list[list[tuple[int, str]]] = []
    expected = 1
    for i, (off, ln) in enumerate(lines):
        m = re.match(r"^(\d{1,2})(?:\s+(\S.*))?$", ln)
        nxt = next((t for _, t in lines[i + 1:] if t), "")
        if m and int(m.group(1)) == expected and not re.match(r"Eigenerkl|Nachweis|Erkl(ä|ae)rung", nxt):
            rows.append([(off, m.group(2) or "")] if m.group(2) else [])
            expected += 1
        elif rows and ln and not _PAGE_MARK.match(ln):
            rows[-1].append((off, ln))

    out = []
    for row in rows:
        texts = [t for _, t in row]
        opt_idx = next((i for i, t in enumerate(texts) if re.match(r"^[a-z]\)", t)), None)
        if opt_idx is None or opt_idx == 0:
            continue
        title_lines = texts[:opt_idx - 1] if opt_idx >= 2 else texts[:opt_idx]
        title = _clean("\n".join(title_lines)).rstrip(".")
        # options (may wrap over several lines), then the max points line, then the kind of proof
        options, cur, k = [], None, opt_idx
        while k < len(texts):
            t = texts[k]
            if re.match(r"^[a-z]\)", t):
                cur = t
                options.append(cur)
            elif re.fullmatch(_NUM, t):
                break
            elif options:
                options[-1] += " " + t
            k += 1
        scale = [m for m in (_OPTION.match(o) for o in options) if m]
        if not scale:
            continue
        max_line = texts[k] if k < len(texts) and re.fullmatch(_NUM, texts[k]) else None
        points = _num(max_line) if max_line else max(_num(m.group(3)) for m in scale)
        proof = _clean("\n".join(texts[k + 1:])) if max_line else ""
        proof = re.split(r";|\s+hier:", proof)[0].strip() or None
        out.append({"title": title, "points": points, "proof": proof, "page": _page_at(doc, row[0][0]),
                    "scale": [(_num(m.group(3)), _clean(m.group(2)).rstrip(".")) for m in scale], "quote": _clean("\n".join(title_lines))})
    return out


def _matrix(doc: Doc, quality_weight: int | float | None) -> tuple[list[dict], list[dict], dict]:
    """(sub-criteria items, formula/threshold items, facts used by the main criteria)."""
    lot_m = _LOT_IN_NAME.search(doc.filename)
    lot = lot_m.group(1) if lot_m else None
    vals, vals_page, vals_quote = _fixed_values(doc)
    rows = _rows(doc)
    lmax = vals.get("lmax") or (sum(r["points"] for r in rows) if rows else None)

    subs = []
    for r in rows:
        parts = [f"{_fmt(p)} P: {txt}" for p, txt in r["scale"] if p]
        if r["proof"]:
            parts.append(f"Nachweis: {r['proof']}")
        if lmax and quality_weight:
            share = round(r["points"] / lmax * quality_weight, 1)
            parts.append(f"≙ {_fmt(int(share) if float(share).is_integer() else share)} % der Gesamtwertung")
        subs.append(_item(r["title"], r["points"], "points", lot, " · ".join(parts), cite(doc, r["page"], r["quote"][:200])))
    subs.sort(key=lambda it: -(it["weight"] or 0))

    extra: list[dict] = []
    facts: dict[str, Any] = {"lot": lot, "rows": len(rows), "lmax": lmax, "w": vals.get("w"), "row_data": rows}

    method = doc.find(r"Wertung anhand der\s+(\S+methode)")
    formula = doc.find(r"Formel:\s*\n?\s*(S\s*=[^\n]+)")
    price_basis = doc.find(r"Angebotspreis[^.]{0,80}?fließt als (Brutto|Netto)wert in die Berechnung ein; der\s+"
                           r"(?:Brutto|Netto)wert enthält keine Abzüge für ([^.]+)\.")
    if price_basis:
        _, pm = price_basis[0]
        facts["price_basis"] = f"{pm.group(1)}preis, keine Abzüge für {_clean(pm.group(2))}"
    if formula or method:
        bits = []
        if formula:
            bits.append(_clean(formula[0][1].group(1)))
            legend = _legend(doc)
            if legend:
                bits.append(legend)
        w = vals.get("w")
        if w is not None:
            bits.append(f"w = {_fmt(w)}" + (f", Lmax = {_fmt(lmax)}" if lmax else ""))
            if lmax:
                q_pts, p_pts = round(w * lmax, 2), round((1 - w) * lmax, 2)
                bits.append(f"max. {_fmt(_num(str(q_pts)))} Punkte aus Leistung + {_fmt(_num(str(p_pts)))} aus Preis")
        if doc.find(r"höchsten Gesamtpunktzahl"):
            bits.append("höchste Gesamtpunktzahl (auf 2 Nachkommastellen) erhält den Zuschlag"
                        if doc.find(r"zwei Nachkommastellen") else "höchste Gesamtpunktzahl erhält den Zuschlag")
        page, m = (method or formula)[0]
        name = f"Wertungsformel ({method[0][1].group(1)})" if method else "Wertungsformel"
        extra.append(_item(name, None, None, lot, "; ".join(bits), cite(doc, page, m.group(0))))
    if vals_quote and "w" in vals and not formula:
        extra.append(_item("Wertungsformel", None, None, lot, f"w = {_fmt(vals['w'])}", cite(doc, vals_page, vals_quote)))

    for page, m in doc.find(r"[^\n.]*(?:Mindest(?:leistungs)?punkt(?:e|zahl)|Ausschlusskriterium|K\.\s?O\.-Kriterium)[^\n.]*"):
        extra.append(_item("Mindestpunktzahl / Ausschlusskriterium", None, None, lot, _clean(m.group(0)),
                           cite(doc, page, m.group(0))))
        break
    return subs, extra, facts


# --- notice pages (e-Vergabe details / TED) ------------------------------------------------------------------------

_LOT_SPLIT = re.compile(r"(?:Interne Referenz-ID Los|Lot):\s*LOT-0*(\d+)")
_PAGE_CRIT = re.compile(r"(?:Art|Type):\s*([^\n]+)\s+(?:Bezeichnung|Name):\s*([^\n]+)\s+(?:(?:Beschreibung|Description):\s*([^\n]+)\s+)?"
                        r"(?:Kategorie[^\n]*:\s*([^\n]+)\s+|Category[^\n]*:\s*([^\n]+)\s+)?"
                        rf"(?:Zuschlagskriterium\s*[—-]\s*Zahl|Award criterion number):\s*({_NUM})")


def _page_weights(ctx: TenderContext) -> list[dict]:
    """Criteria from the notice page, one entry per lot that lists them (German page preferred)."""
    pages = sorted(ctx.pages, key=lambda p: ("Zuschlagskriterien" not in (p.get("markdown") or ""), "ted.europa" in p["url"]))
    for p in pages:
        md = p.get("markdown") or ""
        cuts = list(_LOT_SPLIT.finditer(md))
        parts = [(m.group(1), md[m.end(): cuts[i + 1].start() if i + 1 < len(cuts) else len(md)]) for i, m in enumerate(cuts)] \
            or [(None, md)]
        out = []
        for lot, chunk in parts:
            for m in _PAGE_CRIT.finditer(chunk):
                category = (m.group(4) or m.group(5) or "").lower()
                unit = "%" if ("prozent" in category or "percent" in category or not category) else "points"
                desc = _clean(m.group(3) or "") or None
                out.append({"lot": lot, "name": _clean(m.group(2)), "weight": _num(m.group(6)), "unit": unit,
                            "description": desc, "url": p["url"], "quote": m.group(0)})
        if out:
            return out
    return []


def _no_late_submission(ctx: TenderContext) -> dict | None:
    rx = re.compile(r"Die Nachforderung von leistungsbezogenen Unterlagen,[^\n]{0,160}?Zuschlagskriterien[^\n]{0,80}?ausgeschlossen\.")
    for p in sorted(ctx.pages, key=lambda p: "ted.europa" in p["url"]):
        m = rx.search(p.get("markdown") or "")
        if m:
            law = re.search(r"§\s*\d+\s*Absatz\s*\d+\s*\w+", m.group(0))
            desc = ("Leistungsbezogene Unterlagen zu den Zuschlagskriterien werden nicht nachgefordert"
                    + (f" ({law.group(0)})" if law else "") + ": fehlende Nachweise zu den Leistungskriterien können nicht nachgereicht werden")
            return _item("Keine Nachforderung wertungsrelevanter Unterlagen", None, None, None, desc, cite(p["url"], None, m.group(0)))
    return None


# --- section -------------------------------------------------------------------------------------------------------

def extract(ctx: TenderContext) -> list[dict]:
    invite = ctx.doc(r"Aufforderung.*Angebot") or ctx.doc(r"Aufforderung")
    main = _main_weights(invite) if invite else []
    page_main = _page_weights(ctx)

    quality = next((m["weight"] for m in main if not re.match(r"Preis", m["name"], re.I) and m["unit"] == "%"), None)
    if quality is None:
        quality = next((m["weight"] for m in page_main if not re.match(r"Preis", m["name"], re.I) and m["unit"] == "%"), None)

    subs: list[dict] = []
    extra: list[dict] = []
    facts: list[dict] = []
    for mdoc in ctx.docs_matching(r"Bewertungsmatrix"):
        s, e, f = _matrix(mdoc, quality)
        subs += s
        extra += e
        facts.append(f)
    general = next((f for f in facts if f["lot"] is None), facts[0] if facts else {})

    def describe(name: str) -> str | None:
        if re.match(r"Preis", name, re.I):
            return general.get("price_basis")
        if general.get("rows"):
            d = f"{general['rows']} Leistungskriterien"
            if general.get("lmax"):
                d += f", max. {_fmt(general['lmax'])} Leistungspunkte"
            return d + " (Bewertungsmatrix)"
        return None

    items: list[dict] = []
    if main:
        for m in sorted(main, key=lambda m: -m["weight"]):
            items.append(_item(m["name"], m["weight"], m["unit"], None, describe(m["name"]), cite(invite, m["page"], m["quote"])))
    elif page_main:
        lots = {m["lot"] for m in page_main}
        by_lot = {lot: sorted((m["name"], m["weight"]) for m in page_main if m["lot"] == lot) for lot in lots}
        same = len({tuple(v) for v in by_lot.values()}) == 1
        seen = set()
        for m in sorted(page_main, key=lambda m: (m["lot"] or "", -m["weight"])):
            lot = None if same else m["lot"]
            if (lot, m["name"]) in seen:
                continue
            seen.add((lot, m["name"]))
            desc = describe(m["name"]) or m["description"]
            items.append(_item(m["name"], m["weight"], m["unit"], lot, desc, cite(m["url"], None, m["quote"])))

    items += subs
    items += _shelf_life_baselines(ctx, [r for f in facts for r in f["row_data"]])
    items += extra
    if invite and (tb := _tie_break(invite)):
        items.append(tb)
    if invite and items:
        items += _evaluation_rules(invite)
    if items and (nl := _no_late_submission(ctx)):
        items.append(nl)
    return items
