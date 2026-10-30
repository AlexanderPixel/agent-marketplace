"""Brief section: deadlines. See docs/brief-format.md for the item fields.

Every date a bidder must act on, soonest first. Sources, from most to least specific:

- the "Aufforderung zur Angebotsabgabe" (EinkaufBw template, chapter "geplanter Ablauf" / "Voraussichtlicher
  Zeitplan"): questions deadline, offer deadline, planned award, Zuschlags- und Bindefrist;
- the platform pages (e-Vergabe details, TED, eForms wording in German or English): offer deadline, contract
  start/end; the "_Hinweise von der e-Vergabe.txt" file the platform writes at download time (current deadline);
- Hinweisblätter / Bieterinformationen published later: they may move a deadline. The brief shows the CURRENT date
  and says in the label what it was before.
- the newest statement of the planned call-offs of a framework agreement ("1. Abrufmenge wird im 1. Quartal 2027
  abgerufen"): no day is given, so `at` stays null and the period goes in the label (sorted by its first day).

A platform deadline that falls into the platform's weekly maintenance window (stated in the Aufforderung) gets a
warning in its label.

This module also holds the small text helpers the submission and contacts sections share.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import Doc, TenderContext, cite

BERLIN = ZoneInfo("Europe/Berlin")
_MARK = re.compile(r"^\[page (\d+)\]$", re.M)

# 20.11.2026 08:00 | 20/11/2026 08:00:00 (UTC+01:00) | 13.11.2026 | 20.11.2026, 10.00 Uhr
DATE = (r"(?P<d>\d{1,2})[./](?P<m>\d{1,2})[./](?P<y>\d{4})"
        r"(?:[ \t]*,?[ \t]*(?:um[ \t]+)?(?:(?P<H>\d{1,2}):(?P<M>\d{2})(?::\d{2})?|(?P<H2>\d{1,2})\.(?P<M2>\d{2})(?=\s*Uhr))"
        r"(?:[ \t]*Uhr)?(?:[ \t]*\(UTC(?P<off>[+-]\d{2}:\d{2})\))?)?")
DATE_RX = re.compile(DATE)


# ---------------------------------------------------------------- shared helpers (also used by submission/contacts)

def clean(s: str) -> str:
    return " ".join(s.split())


def page_at(doc: Doc, pos: int) -> int | None:
    """Page number of a position in doc.text (from the "[page N]" markers before it)."""
    page = None
    for m in _MARK.finditer(doc.text, 0, pos + 1):
        page = int(m.group(1))
    return page


def search(doc: Doc | None, rx: re.Pattern, start: int = 0) -> tuple[int | None, re.Match] | None:
    """First match in the whole document text (matches may cross line breaks) with its page."""
    if doc is None:
        return None
    m = rx.search(doc.text, start)
    return (page_at(doc, m.start()), m) if m else None


def ordered_pages(ctx: TenderContext) -> list[dict]:
    """Captured pages, the procurement platform's details page first, then the official notice (TED), then the rest."""
    def rank(p: dict) -> int:
        url = p.get("url") or ""
        if re.search(r"tenderdetails|ausschreibungsdetails|/details", url, re.I) and "oeffentlichevergabe" not in url:
            return 0
        if "ted.europa.eu" in url:
            return 1
        return 2
    return sorted((p for p in ctx.pages if p.get("markdown")), key=rank)


def page_search(ctx: TenderContext, rx: re.Pattern) -> tuple[str, re.Match] | None:
    for p in ordered_pages(ctx):
        m = rx.search(p["markdown"])
        if m:
            return p["url"], m
    return None


def aufforderung(ctx: TenderContext) -> Doc | None:
    """The invitation to tender (newest version)."""
    return ctx.doc(r"Aufforderung\s*zur\s*Angebotsabgabe|Angebotsaufforderung|Aufforderung\s*zur\s*Abgabe")


def platform_hints(ctx: TenderContext) -> Doc | None:
    """The file e-Vergabe adds to every download ("_Hinweise von der e-Vergabe.txt") with the current deadlines."""
    return ctx.doc(r"Hinweise von der e-Vergabe")


def notices_to_bidders(ctx: TenderContext) -> list[Doc]:
    """Hinweisblätter / Bieterinformationen / Berichtigungen, newest version last."""
    found = [d for d in ctx.docs if re.search(
        r"Hinweisblatt|Bieterinformation|Bieterrundschreiben|Berichtigung|Änderungsmitteilung|Fragen.?und.?Antworten",
        d.filename, re.I)]
    return sorted(found, key=lambda d: (d.version, d.filename))


def to_iso(m: re.Match) -> str | None:
    """ISO 8601 from a DATE match: date-time with offset (German local time unless the source gives one) or date."""
    try:
        day = date(int(m["y"]), int(m["m"]), int(m["d"]))
    except ValueError:
        return None
    hh, mm = (m["H"], m["M"]) if m["H"] else (m["H2"], m["M2"])
    if not hh:
        return day.isoformat()
    if m["off"]:
        sign = 1 if m["off"][0] == "+" else -1
        h, mi = m["off"][1:].split(":")
        tz = timezone(sign * timedelta(hours=int(h), minutes=int(mi)))
    else:
        tz = BERLIN
    dt = datetime(day.year, day.month, day.day, int(hh), int(mm), tzinfo=tz)
    return dt.isoformat()


def de_date(iso: str) -> str:
    """'2026-11-20T08:00:00+01:00' -> '20.11.2026 08:00' for labels."""
    d = datetime.fromisoformat(iso)
    return d.strftime("%d.%m.%Y %H:%M") if "T" in iso else d.strftime("%d.%m.%Y")


# ---------------------------------------------------------------- deadlines

LABELS = {
    "questions": "Frist für Bieterfragen (questions deadline)",
    "offer": "Angebotsfrist (offer deadline)",
    "award": "Geplanter Zuschlag (planned award)",
    "binding_period": "Zuschlags- und Bindefrist (offer binding until)",
    "contract_start": "Vertragsbeginn (contract start)",
    "contract_end": "Vertragsende (contract end)",
    "participation": "Teilnahmefrist (requests to participate)",
    "activate": "Teilnahme auf der Plattform aktivieren",
    "call_off": "Erster Abruf (first call-off, Rahmenvereinbarung)",
}
KIND = {"award": "other", "participation": "other", "activate": "other", "call_off": "other"}

# rows of the invitation's schedule ("4.2 geplanter Ablauf" / "Voraussichtlicher Zeitplan"): label ... date
_SCHEDULE_START = re.compile(r"Voraussichtlicher\s+Zeitplan|geplanter\s+Ablauf|Terminplan|Zeitplan", re.I)
_ROWS = {
    "questions": re.compile(r"(?:Letzter\s+Termin|Frist|Ende)\s+(?:zur|für\s+die|der)\s+(?:Einreichung|Stellung)\s+"
                            r"(?:von\s+)?Bieter-?\s*fragen\W{0,20}?" + DATE, re.I),
    "offer": re.compile(r"Angebots(?:abgabe)?frist(?:(?!\d{1,2}\.\d{1,2}\.\d{4}).){0,260}?" + DATE, re.I | re.S),
    "award": re.compile(r"(?:Geplanter|Voraussichtlicher)\s+Zuschlags-?\s*termin\W{0,20}?" + DATE, re.I),
    "binding_period": re.compile(r"(?:Zuschlags-\s*und\s+)?Binde-?\s*frist\W{0,20}?" + DATE, re.I),
}
# platform pages / eForms notice (German and English rendering)
_PAGE_ROWS = {
    "offer": re.compile(r"(?:Frist\s+für\s+den\s+Eingang\s+der\s+Angebote|Deadline\s+for\s+receipt\s+of\s+tenders)"
                        r":\s*" + DATE, re.I),
    "participation": re.compile(r"(?:Frist\s+für\s+den\s+Eingang\s+der\s+Teilnahmeanträge|Deadline\s+for\s+receipt\s+"
                                r"of\s+requests\s+to\s+participate):\s*" + DATE, re.I),
    "contract_start": re.compile(r"(?:Datum\s+des\s+Beginns|Start\s+date):\s*" + DATE, re.I),
    "contract_end": re.compile(r"(?:Enddatum\s+der\s+Laufzeit|Duration\s+end\s+date|End\s+date):\s*" + DATE, re.I),
}
_HINT_ROWS = {
    "offer": re.compile(r"^Angebotsfrist:\s*" + DATE, re.I | re.M),
    "participation": re.compile(r"^Teilnahmefrist:\s*" + DATE, re.I | re.M),
    "activate": re.compile(r"bis\s+zum\s+" + DATE + r"\s*(?:Uhr\s+)?die\s+Teilnahme", re.I),
}
# a Hinweisblatt moving a deadline: "<deadline> ... verlängert/verschoben/geändert ... <date>" (or "neue <deadline>: date")
_CHANGE_KEYS = {
    "offer": r"Angebots(?:abgabe)?frist|Frist\s+(?:zur|für\s+die)\s+(?:Abgabe|Einreichung)\s+(?:der\s+|von\s+)?Angebot\w*|Submissionstermin",
    "questions": r"(?:Frist|Termin)\s+(?:zur|für\s+die|der)\s+(?:Einreichung|Stellung)\s+(?:von\s+)?Bieter-?\s*fragen|"
                 r"Bieter-?\s*fragen-?\s*frist|Fragefrist",
    "binding_period": r"Binde-?\s*frist|Zuschlagsfrist",
}
_CHANGE_VERB = re.compile(r"verlängert|verschoben|geändert|ändert\s+sich|neue[rn]?\b|nunmehr|endet\s+(?:nun|jetzt)", re.I)
# a date that is the OLD value in a change sentence: "vom 20.11.2026 auf den 27.11.2026", "(bisher 20.11.2026)"
_OLD_DATE = re.compile(r"(?<!\w)(?:bisher|bislang|vorher|ursprünglich|zuvor|alt|statt|anstatt|anstelle|vom|von)\W{0,3}"
                       r"(?:(?:de[mn]|am|zum)\s+)?$", re.I)
_SENTENCE_END = re.compile(r"\.\s+(?=[A-ZÄÖÜ])|\n\s*\n")
# the platform's weekly maintenance window ("von jedem Freitag ab 14:00 Uhr bis jeden Samstag 23:59 Uhr")
_WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
_WD = "|".join(_WEEKDAYS)
_MAINTENANCE = re.compile(rf"jede[mn]\s+({_WD})\s+ab\s+(\d{{1,2}}):(\d{{2}})\s*Uhr\s+bis\s+(?:jede[mn]\s+)?({_WD})\s+"
                          rf"(\d{{1,2}}):(\d{{2}})", re.I)
# planned call-offs of a framework agreement, stated in the documents or a Hinweisblatt
# ("Die 1. Abrufmenge wird im 1. Quartal 2027 abgerufen; die 2. Abrufmenge im Januar 2028.")
_MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November",
           "Dezember")
_PERIOD = rf"(?:\d\.\s*Quartal|(?:{'|'.join(_MONTHS)}))\s+\d{{4}}"
_CALL_OFF = re.compile(rf"1\.\s*Abrufmenge\s+wird\s+(?:im|in|zum)\s+({_PERIOD})\s+abgerufen"
                       rf"(?:\W{{0,5}}die\s+2\.\s*Abrufmenge\s+(?:im|in|zum)\s+({_PERIOD}))?", re.I)
_TERM = re.compile(r"spätestens\s+jedoch\s+(\d+)\s+(Jahre?|Monate?)\s+nach\s+Inkrafttreten", re.I)
_OPTION = re.compile(r"(?:einmal\s+)?um\s+(\d+)\s+(Jahre?|Monate?)\s+(?:\(bis\s+maximal\s+\d+\s+Jahre?\)\s+)?verlängert", re.I)


def _same(a: str, b: str) -> bool:
    """Same deadline? Compare the instant when both have a time, else the calendar day."""
    if "T" in a and "T" in b:
        return datetime.fromisoformat(a) == datetime.fromisoformat(b)
    return a[:10] == b[:10]


def _from_aufforderung(doc: Doc | None) -> dict[str, dict]:
    if doc is None:
        return {}
    starts = [m.start() for m in _SCHEDULE_START.finditer(doc.text)]
    # the schedule table is the last "Zeitplan/geplanter Ablauf" heading that is followed by dates
    found: dict[str, dict] = {}
    for start in reversed(starts) if starts else [0]:
        block_end = start + 1800 if starts else len(doc.text)
        for kind, rx in _ROWS.items():
            m = rx.search(doc.text, start, block_end)
            if m and kind not in found and (iso := to_iso(m)):
                found[kind] = {"at": iso, "cite": cite(doc, page_at(doc, m.start()), m.group(0))}
        if found:
            break
    return found


def _from_pages(ctx: TenderContext) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for kind, rx in _PAGE_ROWS.items():
        hit = page_search(ctx, rx)
        if hit and (iso := to_iso(hit[1])):
            found[kind] = {"at": iso, "cite": cite(hit[0], None, hit[1].group(0))}
    return found


def _from_hints(doc: Doc | None) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if doc is None:
        return found
    for kind, rx in _HINT_ROWS.items():
        m = rx.search(doc.text)
        if m and (iso := to_iso(m)):
            found[kind] = {"at": iso, "cite": cite(doc, page_at(doc, m.start()), m.group(0))}
    return found


def _changes(ctx: TenderContext, after_version: int) -> dict[str, dict]:
    """Deadlines moved by later Hinweisblätter; the newest one wins."""
    found: dict[str, dict] = {}
    for doc in notices_to_bidders(ctx):
        if doc.version <= after_version:
            continue
        for kind, key in _CHANGE_KEYS.items():
            for km in re.finditer(key, doc.text, re.I):
                window = doc.text[km.start(): min(len(doc.text), km.end() + 250)]
                if (end := _SENTENCE_END.search(window, km.end() - km.start())):
                    window = window[: end.start() + 1]
                if not _CHANGE_VERB.search(window):
                    continue
                # the new date: the first one that is not introduced as the old value ("vom …", "bisher …")
                dm = next((d for d in DATE_RX.finditer(window) if not _OLD_DATE.search(window[: d.start()])), None)
                iso = to_iso(dm) if dm else None
                if iso:
                    quote = window[: max(dm.end(), _CHANGE_VERB.search(window).end())]
                    found[kind] = {"at": iso, "cite": cite(doc, page_at(doc, km.start()), quote),
                                   "by": re.sub(r"\.(pdf|docx?)$", "", doc.filename, flags=re.I)}
                    break
    return found


def _maintenance_note(auf: Doc | None, iso: str | None) -> str | None:
    """Warn when a platform deadline falls into the platform's weekly maintenance window (no communication)."""
    hit = search(auf, _MAINTENANCE)
    if not hit or not iso:
        return None
    m = hit[1]
    days = {d.lower(): i for i, d in enumerate(_WEEKDAYS)}
    start = days[m.group(1).lower()] * 1440 + int(m.group(2)) * 60 + int(m.group(3))
    end = days[m.group(4).lower()] * 1440 + int(m.group(5)) * 60 + int(m.group(6))
    where = f" (Aufforderung S. {hit[0]})" if hit[0] else ""
    d = datetime.fromisoformat(iso)
    if "T" in iso:
        d = d.astimezone(BERLIN)
        t = d.weekday() * 1440 + d.hour * 60 + d.minute
        inside = start <= t <= end if start <= end else (t >= start or t <= end)
        return f"liegt im Wartungsfenster der Plattform ({m.group(1)} {m.group(2)}:{m.group(3)} – " \
               f"{m.group(4)} {m.group(5)}:{m.group(6)}){where}" if inside else None
    day_start, day_end = d.weekday() * 1440, d.weekday() * 1440 + 1439
    if day_start <= start <= day_end:
        return f"{_WEEKDAYS[d.weekday()]}: Plattform ab {m.group(2)}:{m.group(3)} Uhr wegen Wartung nicht erreichbar{where}"
    if (start <= day_start and day_end <= end) if start <= end else False:
        return f"{_WEEKDAYS[d.weekday()]}: Plattform wegen Wartung nicht erreichbar{where}"
    return None


def _period_start(period: str) -> str | None:
    """'1. Quartal 2027' -> '2027-01-01', 'Januar 2028' -> '2028-01-01' (for sorting only)."""
    if (q := re.match(r"(\d)\.\s*Quartal\s+(\d{4})", period)):
        return date(int(q.group(2)), 3 * int(q.group(1)) - 2, 1).isoformat()
    if (mo := re.match(r"(\w+)\s+(\d{4})", period)) and mo.group(1).capitalize() in _MONTHS:
        return date(int(mo.group(2)), _MONTHS.index(mo.group(1).capitalize()) + 1, 1).isoformat()
    return None


def _call_off(ctx: TenderContext) -> dict | None:
    """Planned first call-off of a framework agreement, newest statement wins (Hinweisblätter come last)."""
    found = None
    for doc in sorted(ctx.docs, key=lambda d: d.version):
        hit = search(doc, _CALL_OFF)
        if hit:
            found = (doc, hit)
    if not found:
        return None
    doc, (page, m) = found
    note = clean(m.group(1)) + (f"; 2. Abruf {clean(m.group(2))}" if m.group(2) else "")
    return {"at": None, "sort": _period_start(clean(m.group(1))), "cite": cite(doc, page, m.group(0)), "note": note}


def extract(ctx: TenderContext) -> list[dict]:
    auf = aufforderung(ctx)
    base = _from_aufforderung(auf)
    pages = _from_pages(ctx)
    hints = _from_hints(platform_hints(ctx))
    changed = _changes(ctx, auf.version if auf else -1)

    items: dict[str, dict] = {}
    for kind in ("questions", "offer", "award", "binding_period"):
        if kind in base:
            items[kind] = {"at": base[kind]["at"], "cite": base[kind]["cite"], "note": None}

    # the platform states the current offer deadline; it wins over an older invitation letter
    for kind in ("offer", "participation", "activate"):
        cur = hints.get(kind) or pages.get(kind)
        if not cur:
            continue
        if kind in items and not _same(items[kind]["at"], cur["at"]):
            items[kind] = {"at": cur["at"], "cite": cur["cite"],
                           "note": f"geändert, laut Aufforderung zuvor {de_date(items[kind]['at'])}"}
        elif kind not in items:
            items[kind] = {"at": cur["at"], "cite": cur["cite"], "note": None}
        elif "T" in cur["at"] and "T" not in items[kind]["at"]:
            items[kind]["at"] = cur["at"]       # same day, the platform adds the time

    for kind, ch in changed.items():
        prev = items.get(kind)
        if prev and _same(prev["at"], ch["at"]):
            continue
        note = f"geändert durch {ch['by']}" + (f", vorher {de_date(prev['at'])}" if prev else "")
        items[kind] = {"at": ch["at"], "cite": ch["cite"], "note": note}

    for kind in ("contract_start", "contract_end"):
        if kind in pages:
            items[kind] = {"at": pages[kind]["at"], "cite": pages[kind]["cite"], "note": None}
    for kind in ("contract_start", "contract_end"):
        if kind not in pages:
            continue
        md = next((p["markdown"] for p in ctx.pages if p["url"] == pages[kind]["cite"]["file"]), "")
        notes = []
        if re.search(r"Geschätzte\s+Dauer|Estimated\s+duration", md, re.I):
            notes.append("geschätzt laut Bekanntmachung")
        if kind == "contract_end" and (om := _OPTION.search(md)):
            notes.append(f"Verlängerungsoption +{om.group(1)} {om.group(2)}")
        items[kind]["note"] = "; ".join(notes) or None
    else:
        # no dates on the notice: the framework agreement draft states the term relative to its entry into force
        rv = ctx.doc(r"Entwurf|Rahmenvereinbarung|Rahmenvertrag|_RV[ _]")
        hit = search(rv, _TERM)
        if hit:
            page, m = hit
            opt = _OPTION.search(rv.text, m.end(), m.end() + 1500)
            note = f"{m.group(1)} {m.group(2)} nach Inkrafttreten" + (
                f", Verlängerungsoption +{opt.group(1)} {opt.group(2)}" if opt else "")
            items["contract_end"] = {"at": None, "cite": cite(rv, page, m.group(0)), "note": note}

    if (co := _call_off(ctx)):
        items["call_off"] = co

    # a deadline on the platform's maintenance day: the bidder cannot reach the platform for part of that day
    for kind in ("questions", "offer", "participation"):
        if kind in items and (warn := _maintenance_note(auf, items[kind]["at"])):
            items[kind]["note"] = "; ".join(filter(None, (items[kind]["note"], warn)))

    out = []
    for kind, it in items.items():
        label = LABELS[kind] + (f" — {it['note']}" if it["note"] else "")
        out.append({"label": label, "at": it["at"], "kind": KIND.get(kind, kind), "cite": it["cite"],
                    "_sort": it.get("sort")})

    def when(item: dict) -> datetime:
        at = item["at"] or item["_sort"]
        if not at:
            return datetime.max.replace(tzinfo=timezone.utc)
        d = datetime.fromisoformat(at)
        return d if d.tzinfo else datetime(d.year, d.month, d.day, 23, 59, tzinfo=BERLIN)
    out.sort(key=when)
    for item in out:
        del item["_sort"]
    return out
