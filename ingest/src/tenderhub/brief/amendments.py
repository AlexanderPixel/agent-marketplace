"""Brief section: amendments — what changed after publication, one item per amendment round (oldest first).

e-Vergabe publishes each amendment as a new folder "Vergabeunterlagen/Version N" holding only the files that were
added or re-issued. For every version N >= 2 this module reports:

* date   — the version's Versanddatum on the e-Vergabe documents list, else the "(Stand dd.mm.yyyy)" of its
           Hinweisblatt / Bieterinformation, else the "Erstellungsdatum" stamped on its files;
* title  — the heading of its Hinweisblatt ("1. Hinweisblatt"), else "Version N der Vergabeunterlagen";
* changes — the new bidder Q&A of its Hinweisblatt in full (cumulative sheets repeat earlier rounds; only the
           new entries are reported). An answer that only points to the "Preis- und Produktblätter" for call-off
           ("N. Abrufmenge") or minimum quantities gets those per-lot values appended, and an answer whose
           call-off month disagrees with a lot's Preis- und Produktblatt gets an "Abweichung" line. Then an
           Änderungsbekanntmachung sent the same day, and a note on files re-issued without content change;
* files_changed — files new in this version or whose content differs from the previous version of the same name
           (date/version stamps alone do not count as a change).

See docs/brief-format.md for the item fields.
"""

from __future__ import annotations

import difflib
import re
from datetime import date

from . import Doc, TenderContext, cite

# Amendment sheets of the German federal templates: "1. Hinweisblatt", "2. Bieterinformation", ...
_SHEET_WORDS = r"(?:Hinweisblatt|Bieterinformation|Bieterrundschreiben|Informationsblatt|Änderungsmitteilung|Berichtigung)"
# file names of amendment sheets ("Informationsblatt" alone is too generic: "Informationsblatt DSGVO")
_SHEET_FILE = re.compile(r"Hinweisblatt|Bieterinformation|Bieterrundschreiben|Änderungsmitteilung|Berichtigung"
                         r"|Bieterfragen|Fragen.{0,5}Antworten", re.I)
_SHEET_HEADING = re.compile(r"(\d+)\.\s*(" + _SHEET_WORDS + r")\s*\(\s*Stand:?\s*(\d{1,2}\.\d{1,2}\.\d{2,4})\s*\)", re.I)
_SHEET_TITLE = re.compile(r"(\d+)\.\s*(" + _SHEET_WORDS + r")", re.I)

# One Q&A row: "<Lfd. Nr.> <Bezug> „<Frage>“ <Antwort>" (the table is flattened column by column into lines).
_ENTRY = re.compile(r"^[ \t]*(\d{1,3})[ \t]+([^„“\"\n]{0,80}(?:\n[^„“\"\n]{0,80})?)[„\"](.+?)[“\"]", re.M | re.S)
_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\b")
_STAMP_DATE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}(?:,?\s+\d{1,2}:\d{2}(?::\d{2})?)?\b")
_PAGE_FOOTER = re.compile(r"^\s*Seite\s+\d+\s+von\s+\d+\s*$", re.M)
_GENERIC_REF = {"", "-", "allgemein", "allgemeines", "sonstiges"}
_CHANGE_WORDS = re.compile(r"verlänger|verschoben|geändert|ändert sich|ersetzt|korrigiert|berichtigt|entfällt|"
                           r"neu(?:e|er|es)? Frist|Angebotsfrist|Menge", re.I)
_MAX_ANSWER = 1500          # answers are kept whole; this only guards against a runaway table parse


def _iso(m: re.Match) -> str | None:
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y += 2000 if y < 100 else 0
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def _squash(s: str) -> str:
    return " ".join(s.split())


def _shorten(s: str, limit: int = _MAX_ANSWER) -> str:
    s = _squash(s)
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + " …"


# ---------- version dates from the e-Vergabe documents list ----------

_LIST_ROW = re.compile(r"Version\s+(\d+)/([^\]\n]+)\]\([^)]*\)\s*\|[^|\n]*\|\s*(\d{1,2}\.\d{1,2}\.\d{2,4}),?\s*(\d{1,2}:\d{2})?")


def _listed_dates(ctx: TenderContext) -> dict[int, tuple[str, str, str]]:
    """{version: (iso date, page url, verbatim row excerpt)} — the earliest Versanddatum of the version's files."""
    out: dict[int, tuple[str, str, str]] = {}
    for page in ctx.pages:
        for m in _LIST_ROW.finditer(page.get("markdown") or ""):
            d = _DATE.search(m.group(3))
            iso = _iso(d) if d else None
            if not iso:
                continue
            v = int(m.group(1))
            quote = f"Version {v}/{m.group(2).strip()}"
            stamp = " ".join(x for x in (m.group(3), m.group(4)) if x)
            if v not in out or iso < out[v][0]:
                out[v] = (iso, page["url"], f"{quote} … {stamp}")
    return out


# ---------- change notice (Änderungsbekanntmachung) on the notice pages ----------

_CHANGE_NOTICE = re.compile(r"Hauptgrund für die Änderung:\s*(?P<reason>[^\n]+).*?Beschreibung:\s*(?P<desc>[^\n]*)"
                            r".*?Datum der Übermittlung der Bekanntmachung:\s*(?P<date>\d{1,2}/\d{1,2}/\d{4})"
                            r"(?:\s+(?P<time>\d{1,2}:\d{2}))?", re.S)


def _change_notices(ctx: TenderContext) -> dict[str, tuple[str, str, str]]:
    """{iso date sent: (summary line, page url, quote)} for change notices on the (German) notice pages."""
    out: dict[str, tuple[str, str, str]] = {}
    for page in ctx.pages:
        for m in _CHANGE_NOTICE.finditer(page.get("markdown") or ""):
            d, mo, y = m.group("date").split("/")
            try:
                iso = date(int(y), int(mo), int(d)).isoformat()
            except ValueError:
                continue
            reason, desc = _squash(m.group("reason")), _squash(m.group("desc"))
            sent = m.group("date") + (f" {m.group('time')}" if m.group("time") else "")
            line = f"Änderungsbekanntmachung übermittelt am {sent.replace('/', '.')} (Hauptgrund: {reason}"
            line += f"): {_shorten(desc, 200)}" if desc and desc != "-" else "; ohne Beschreibung der Änderung)"
            out.setdefault(iso, (line, page["url"], f"Hauptgrund für die Änderung: {reason}"))
    return out


# ---------- quantities the answers refer to ("ergeben sich aus den Preis- und Produktblättern") ----------

_PRICE_SHEET = re.compile(r"Preis-?\s*und\s*Produktbl", re.I)
_LOT_IN_NAME = re.compile(r"LOS[ _]?(\d+)(?!\d)", re.I)
# "1. Abrufmenge  01/2027: 600 Stück" — one line per call-off in the lot's row of the sheet
_CALL_OFF = re.compile(r"^\s*(\d)\.\s*Abrufmenge\s+(\d{1,2})/(\d{4})\s*:\s*(\d[\d.]*)\s*([A-Za-zÄÖÜäöü]+)?", re.M)
# the Mindestabnahmemenge is the number that ends the line before the first call-off
_MIN_BEFORE = re.compile(r"(\d[\d.]*)[ \t]*\n\s*1\.\s*Abrufmenge")
_MONTHS = ("januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september", "oktober",
           "november", "dezember")
# "die 2. Abrufmenge im Januar 2028" / "Die 1. Abrufmenge wird im 1. Quartal 2027 abgerufen"
_ANSWER_WHEN = re.compile(r"(\d)\.\s*Abrufmenge\b[^.;]{0,40}?\bim\s+(?:(\d)\.\s*Quartal|(" + "|".join(_MONTHS)
                          + r"))\s+(\d{4})", re.I)


def _lot_quantities(ctx: TenderContext) -> dict[int, dict]:
    """{lot: {"calls": {n: (month, year, qty, unit)}, "min": qty | None}} from the newest Preis- und Produktblätter.
    The template's "Beispiel" row comes before the lot's own row, so the last call-off block of the sheet wins."""
    out: dict[int, dict] = {}
    for doc in ctx.docs_matching(_PRICE_SHEET.pattern):
        lot = _LOT_IN_NAME.search(doc.filename)
        if not lot:
            continue
        blocks: list[dict[int, tuple]] = []
        starts: list[int] = []
        for m in _CALL_OFF.finditer(doc.text):
            n = int(m.group(1))
            if n == 1 or not blocks:
                blocks.append({})
                starts.append(m.start())
            blocks[-1][n] = (int(m.group(2)), int(m.group(3)), m.group(4), m.group(5) or "")
        if not blocks:
            continue
        mins = [m for m in _MIN_BEFORE.finditer(doc.text) if m.end() >= starts[-1] - 1 and m.start() < starts[-1]]
        out[int(lot.group(1))] = {"calls": blocks[-1], "min": mins[-1].group(1) if mins else None}
    return dict(sorted(out.items()))


def _per_lot(values: dict[int, tuple[str, str]]) -> str:
    """"Los 1 600 Stück, Los 2 1.120 Stück (01/2027)" — the shared suffix once, else per lot."""
    suffixes = {suf for _, suf in values.values()}
    if len(suffixes) == 1:
        suf = next(iter(suffixes))
        return ", ".join(f"Los {lot} {v}" for lot, (v, _) in values.items()) + (f" ({suf})" if suf else "")
    return ", ".join(f"Los {lot} {v}" + (f" ({suf})" if suf else "") for lot, (v, suf) in values.items())


def _resolve_references(answer: str, lots: dict[int, dict]) -> list[str]:
    """Values for the sentences of an answer that point to the Preis- und Produktblätter for quantities."""
    found: list[str] = []
    for sentence in re.split(r"(?<=[a-zäöüß)])\.\s+", answer):
        if not _PRICE_SHEET.search(sentence):
            continue
        for n in sorted({int(x) for x in re.findall(r"(\d)\.\s*Abrufmenge", sentence)}):
            vals = {lot: (f"{q['calls'][n][2]} {q['calls'][n][3]}".strip(), f"{q['calls'][n][0]:02d}/{q['calls'][n][1]}")
                    for lot, q in lots.items() if n in q["calls"]}
            if vals:
                found.append(f"{n}. Abrufmenge laut Preis- und Produktblättern: {_per_lot(vals)}")
        if re.search(r"Mindestabnahmemenge", sentence, re.I):
            vals = {}
            for lot, q in lots.items():
                unit = next((c[3] for c in q["calls"].values() if c[3]), "")
                if q["min"]:
                    vals[lot] = (f"{q['min']} {unit}".strip(), "")
            if vals:
                found.append(f"Mindestabnahmemenge laut Preis- und Produktblättern: {_per_lot(vals)}")
    return list(dict.fromkeys(found))


def _call_off_conflicts(answer: str, lots: dict[int, dict]) -> list[str]:
    """Call-off months stated in an answer that a lot's Preis- und Produktblatt contradicts."""
    out: list[str] = []
    for m in _ANSWER_WHEN.finditer(answer):
        n, quarter, month, year = int(m.group(1)), m.group(2), m.group(3), int(m.group(4))
        if quarter:
            allowed = set(range(3 * int(quarter) - 2, 3 * int(quarter) + 1))
            said = f"{quarter}. Quartal {year}"
        else:
            allowed = {_MONTHS.index(month.lower()) + 1}
            said = f"{month.capitalize()} {year}"
        for lot, q in lots.items():
            call = q["calls"].get(n)
            if call and (call[1] != year or call[0] not in allowed):
                out.append(f"Abweichung: das Hinweisblatt nennt für die {n}. Abrufmenge „{said}“, das Preis- und "
                           f"Produktblatt Los {lot} aber {call[0]:02d}/{call[1]} ({call[2]} {call[3]})".rstrip())
    return out


# ---------- Hinweisblatt Q&A ----------

def _clean_sheet(doc: Doc) -> str:
    """Sheet text without the page header/footer lines that repeat on every page."""
    chunks = [t for _, t in doc.page_texts()]
    if len(chunks) > 1:
        sets = [{ln.strip() for ln in c.splitlines() if ln.strip()} for c in chunks]
        repeated = set.intersection(*sets)
        chunks = ["\n".join(ln for ln in c.splitlines() if ln.strip() not in repeated) for c in chunks]
    return _PAGE_FOOTER.sub("", "\n".join(chunks))


def _sheet_part(doc: Doc, number: int | None) -> str:
    """The part of a (cumulative) sheet that belongs to its own round: after the "N. Hinweisblatt (Stand …):"
    section heading that matches the sheet's number, when the sheet has such section headings."""
    text = _clean_sheet(doc)
    heads = list(_SHEET_HEADING.finditer(text))
    if number is None or len(heads) < 2:
        # a single heading is the sheet's own title: everything after the first heading is this round
        return text[heads[-1].end():] if heads else text
    own = [h for h in heads if int(h.group(1)) == number]
    if not own:
        return text
    start = own[-1].end()
    later = [h.start() for h in heads if h.start() > start and int(h.group(1)) != number]
    return text[start: later[0] if later else len(text)]


def _qa_entries(part: str) -> list[tuple[str, str, str]]:
    """[(Bezug, Frage, Antwort)] from a flattened Q&A table, entries numbered consecutively."""
    cands = list(_ENTRY.finditer(part))
    entries: list[re.Match] = []
    for m in cands:
        n = int(m.group(1))
        if not entries or n == int(entries[-1].group(1)) + 1:
            entries.append(m)
    out = []
    for i, m in enumerate(entries):
        end = entries[i + 1].start() if i + 1 < len(entries) else len(part)
        tail = _SHEET_HEADING.search(part, m.end(), end)
        answer = part[m.end(): tail.start() if tail else end]
        out.append((_squash(m.group(2)), _squash(m.group(3)), _squash(answer)))
    return out


def _sheet_changes(doc: Doc, number: int | None, seen: set[str], lots: dict[int, dict] | None = None) -> list[str]:
    part = _sheet_part(doc, number)
    changes: list[str] = []
    conflicts: list[str] = []
    entries = _qa_entries(part)
    for ref, question, answer in entries:
        key = re.sub(r"\W+", "", question.lower())
        if key in seen:
            continue
        seen.add(key)
        line = f"„{question}“ → {_shorten(answer)}" if answer else f"„{question}“"
        if ref.lower().rstrip(":") not in _GENERIC_REF:
            line = f"{ref}: {line}"
        if lots and answer:
            refs = _resolve_references(answer, lots)
            if refs:
                line += f" [{'; '.join(refs)}]"
            conflicts += [c for c in _call_off_conflicts(answer, lots) if c not in conflicts]
        changes.append(line)
    changes += conflicts
    if not entries:
        # free-text sheet: keep the sentences that announce a change
        for sentence in re.split(r"(?<=[a-zäöüß)])\.\s+", _squash(part)):
            if _CHANGE_WORDS.search(sentence) and _squash(sentence) not in seen:
                seen.add(_squash(sentence))
                changes.append(_shorten(sentence.rstrip(".") + "."))
            if len(changes) >= 6:
                break
    return changes


# ---------- file comparison between versions ----------

def _stamp_only(old: str, new: str, old_v: int, new_v: int) -> bool:
    """True when the two texts differ only in dates/times and the version number."""
    def norm(line: str, v: int) -> str:
        line = _STAMP_DATE.sub("<DATE>", line).strip()
        if line == str(v):                       # a bare version-number field (e.g. in .aidf exports)
            return "<V>"
        return re.sub(rf"\b(Version|Fassung)\s+{v}\b", r"\1 <V>", line, flags=re.I)

    a, b = old.splitlines(), new.splitlines()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag != "replace" or i2 - i1 != j2 - j1:
            return False
        if any(norm(x, old_v) != norm(y, new_v) for x, y in zip(a[i1:i2], b[j1:j2])):
            return False
    return True


def _compare(doc: Doc, prev: Doc | None) -> str:
    """"new" | "changed" | "reissued" (same content, new stamp) | "same"."""
    if prev is None:
        return "new"
    if _squash(doc.text) == _squash(prev.text):
        return "same"
    return "reissued" if _stamp_only(prev.text, doc.text, prev.version, doc.version) else "changed"


def _creation_date(docs: list[Doc]) -> tuple[str, Doc, int | None, str] | None:
    for d in docs:
        for page, m in d.find(r"Erstellungsdatum:?\s*(\d{1,2}\.\d{1,2}\.\d{2,4})"):
            dm = _DATE.search(m.group(1))
            iso = _iso(dm) if dm else None
            if iso:
                return iso, d, page, m.group(0)
    return None


# ---------- section ----------

def extract(ctx: TenderContext) -> list[dict]:
    versioned = [d for d in ctx.docs if d.version > 0]
    versions = sorted({d.version for d in versioned})
    if len(versions) < 2:
        return []
    listed = _listed_dates(ctx)
    notices = _change_notices(ctx)
    lots = _lot_quantities(ctx)
    seen_questions: set[str] = set()
    used_notices: set[str] = set()
    items: list[dict] = []
    # questions answered already in the original documents are not amendments (cumulative sheets repeat them)
    for d in versioned:
        if d.version == versions[0] and _SHEET_FILE.search(d.filename):
            _sheet_changes(d, None, seen_questions)

    for v in versions[1:]:
        docs = sorted((d for d in versioned if d.version == v), key=lambda d: d.filename)
        sheets = [d for d in docs if _SHEET_FILE.search(d.filename)]

        title = sheet_date = None
        item_cite = None
        changes: list[str] = []
        for sheet in sheets:
            hm = None
            for page, m in sheet.find(_SHEET_HEADING):
                hm = (page, m)
                break
            if hm is None:
                for page, m in sheet.find(_SHEET_TITLE):
                    hm = (page, m)
                    break
            number = int(hm[1].group(1)) if hm else None
            if hm and title is None:
                title = f"{hm[1].group(1)}. {hm[1].group(2)}"
                if hm[1].re is _SHEET_HEADING:
                    dm = _DATE.search(hm[1].group(3))
                    sheet_date = _iso(dm) if dm else None
                item_cite = cite(sheet, hm[0], hm[1].group(0))
            changes += _sheet_changes(sheet, number, seen_questions, lots)
            if item_cite is None:
                item_cite = cite(sheet, None, _squash(sheet.text)[:120])

        # which files changed against the previous version of the same file name
        files_changed: list[str] = []
        reissued: list[str] = []
        for d in docs:
            prev = max((p for p in versioned if p.filename == d.filename and p.version < v),
                       key=lambda p: p.version, default=None)
            state = _compare(d, prev)
            if state in ("new", "changed"):
                files_changed.append(d.filename)
            elif state == "reissued":
                reissued.append(d.filename)
        when = listed.get(v)
        made = _creation_date([d for d in docs if d not in sheets])
        version_date = (when[0] if when else None) or sheet_date or (made[0] if made else None)

        if version_date and version_date in notices and version_date not in used_notices:
            used_notices.add(version_date)
            changes.append(notices[version_date][0])
        if reissued:
            changes.append(f"{', '.join(reissued)} neu bereitgestellt, inhaltlich unverändert "
                           f"(nur Datum/Versionsnummer)")

        if item_cite is None:
            if when:
                item_cite = cite(when[1], None, when[2])
            elif made:
                item_cite = cite(made[1], made[2], made[3])
            elif docs:
                item_cite = cite(docs[0], None, _squash(docs[0].text)[:120] or docs[0].filename)
            else:
                continue

        items.append({
            "version": v,
            "date": version_date,
            "title": title or f"Version {v} der Vergabeunterlagen",
            "changes": changes,
            "files_changed": files_changed,
            "cite": item_cite,
        })
    return items
