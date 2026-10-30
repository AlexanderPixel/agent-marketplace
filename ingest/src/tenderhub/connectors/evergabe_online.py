"""e-Vergabe (evergabe-online.de) — the German federal e-procurement platform (Beschaffungsamt des BMI).

Used by federal agencies, the Bundeswehr (BAAINBw, BAIUDBw, Bundeswehrverwaltung), Bundesagentur für
Arbeit, BBR and many Länder/municipal buyers. There is no API or tender feed (the RSS feed on
evergabe-online.info is platform news only), so we read the public search, an Apache Wicket app:

    GET  /search.html                       -> 302 ?cookieCheck (sets COOKIES_SUPPORTED) -> result page,
                                               10 open procedures, sorted by publication date (newest first)
    POST /search.html?N-1.0-rowsPerPage-rowsPerPageForm-rowsPerPageChoice   (Wicket Ajax, rowsPerPageChoice=3)
                                            -> <ajax-response> with the table re-rendered at 100 rows/page
    GET  ./search.html?N-M.-searchPanel-...-navigator-next  (needs a same-site Referer, else 403)
                                            -> next 100 rows
    GET  /download/Bekanntmachung.xml?id=<id>   -> the notice as XML: either eForms UBL (EU procedures) or the
                                               platform's own NATVERFAHREN schema (national UVgO/VOB/A notices);
                                               410 for unknown/withdrawn ids
    GET  /tenderdetails.html?id=<id>         -> human-readable notice page

List rows give id, title, Geschäftszeichen, Vergabestelle (platform account), place, procedure type, next
deadline and publication day. The XML adds the actual contracting authority, CPV codes, description, exact
deadlines, legal basis and value. XMLs of a page are fetched concurrently (the shared rate limiter still
spaces them) while the next page is already being requested.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse
from lxml import etree
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..classify import BUYER_PATTERNS
from ..connector import Checkpoint, Connector, StoredRef
from ..countries import iso3_to_iso2, lang3_to_lang2
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status, normalize_cpv

BASE = "https://www.evergabe-online.de/"
SEARCH = f"{BASE}search.html"
XML_URL = f"{BASE}download/Bekanntmachung.xml"
PDF_URL = f"{BASE}Bekanntmachung.pdf"
DETAIL_URL = f"{BASE}tenderdetails.html"
BERLIN = ZoneInfo("Europe/Berlin")
ROWS_100 = "3"            # rowsPerPageChoice option: 0=10, 1=25, 2=50, 3=100
MAX_PAGES = 60            # safety stop (the search holds ~1-2k open procedures)
DETAIL_CONCURRENCY = 8    # XML downloads in flight; the rate limiter decides the actual pace
CHUNK = 20                # rows whose XMLs are fetched before yielding (keeps the stream responsive)
KNOWN_DAYS = timedelta(days=2)  # remember yielded rows this long before `since` (covers the runner overlap)

FORM_TYPES = {  # eForms cbc:NoticeTypeCode/@listName
    "planning": NoticeType.PLAN,
    "consultation": NoticeType.PLAN,
    "competition": NoticeType.TENDER,
    "result": NoticeType.AWARD,
    "dir-awa-pre": NoticeType.AWARD,
    "change": NoticeType.MODIFICATION,
    "cont-modif": NoticeType.MODIFICATION,
}

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, remove_blank_text=True)
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
_AJAX_ROWS = re.compile(r'"u":"([^"]*rowsPerPageChoice[^"]*)"')
_ID = re.compile(r"[?&]id=(\d+)")
_DMY = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})(?:\D+(\d{1,2}):(\d{2}))?")
_STATE_PREFIX = re.compile(r"^[^:]{2,40}:\s*")
# Bundeswehr bodies: BAAINBw, BAIUDBw, BAPersBw, BwDLZ, 'Bundeswehrverwaltung', 'Wehrbereichsverwaltung' ...
_BUNDESWEHR = re.compile(r"bundeswehr|\b(?:baainbw|baiudbw|bapersbw|bwdlz|bw\s*dlz|wehrbereichsverwaltung)\b"
                         r"|(?-i:[A-Z][A-Za-z]+Bw\b)", re.I)


def _clean(s: str | None) -> str | None:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s or None


def parse_dmy(s: str | None, end_of_day: bool = False) -> datetime | None:
    """'13.11.26, 10:00' / '26.11.2026 13:00' / '30.10.26' -> Berlin-aware datetime."""
    m = _DMY.search(s or "")
    if not m:
        return None
    d, mo, y, hh, mm = m.groups()
    year = int(y) + 2000 if len(y) == 2 else int(y)
    if hh is None:
        hh, mm = (23, 59) if end_of_day else (0, 0)
    try:
        return datetime(year, int(mo), int(d), int(hh), int(mm), tzinfo=BERLIN)
    except ValueError:
        return None


def notice_url(tender_id: str) -> str:
    return f"{DETAIL_URL}?id={tender_id}"


# --------------------------------------------------------------------------- search result pages


def page_html(text: str) -> str:
    """A Wicket <ajax-response> carries the re-rendered components as CDATA; plain pages pass through."""
    if text.lstrip().startswith("<?xml") and "<ajax-response" in text[:500]:
        return "\n".join(_CDATA.findall(text))
    return text


def parse_rows(html: str) -> list[dict[str, str]]:
    tree = HTMLParser(html)
    rows = []
    for tr in tree.css("#datatable tbody tr"):
        a = tr.css_first("a[href*='tenderdetails']")
        m = _ID.search(a.attributes.get("href") or "") if a else None
        if not m:
            continue
        tds = tr.css("td")
        cells = [_clean(td.text()) or "" for td in tds]

        def by_class(cls: str) -> str:
            for td in tds:
                if cls in (td.attributes.get("class") or ""):
                    return _clean(td.text()) or ""
            return ""

        rows.append({
            "id": m.group(1),
            "title": _clean(a.text()) or "",
            "reference": cells[1] if len(cells) > 1 else "",
            "vergabestelle": cells[2] if len(cells) > 2 else "",
            "place": cells[3] if len(cells) > 3 else "",
            "procedure": cells[4] if len(cells) > 4 else "",
            "next_deadline": by_class("result_col_deadline"),
            "published": by_class("result_col_releaseDate"),
        })
    return rows


def next_link(html: str, page_url: str) -> str | None:
    a = HTMLParser(html).css_first("#datatable a.next[href]")
    return urljoin(page_url, a.attributes["href"]) if a else None


def total_hits(html: str) -> int | None:
    m = re.search(r"Zeige\s+\d+\s+bis\s+\d+\s+von\s+(\d+)", html)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- notice XML


def _strip_ns(root: etree._Element) -> etree._Element:
    for el in root.iter():
        if isinstance(el.tag, str):
            el.tag = etree.QName(el).localname
    return root


def _t(el: etree._Element | None, path: str) -> str | None:
    if el is None:
        return None
    r = el.xpath(path)
    if not r:
        return None
    v = r[0] if isinstance(r[0], str) else r[0].text
    return _clean(v)


def _eforms_dt(day: str | None, tm: str | None) -> datetime | None:
    if not day:
        return None
    d = day[:10]
    off = day[10:] if len(day) > 10 and day[10:] != "Z" else "+00:00"
    if tm:
        t = isoparse(f"{d}T{tm.replace('Z', '+00:00')}")
        return t if t.tzinfo else t.replace(tzinfo=isoparse(f"{d}T00:00:00{off}").tzinfo)
    return isoparse(f"{d}T23:59:00{off}")


def parse_xml(content: bytes) -> dict[str, Any]:
    """Normalize either notice XML flavour into a flat dict of the fields we map."""
    root = _strip_ns(etree.fromstring(content, _PARSER))
    if root.tag == "NATVERFAHREN":
        return _parse_national(root)
    return _parse_eforms(root)


def _parse_national(root: etree._Element) -> dict[str, Any]:
    ca = root.xpath("//contracting-authority")
    ca = ca[0] if ca else None
    cpvs = [normalize_cpv(c.text or "") for c in root.xpath("/*/CPVS/CPV/NUMMER")]
    if not cpvs:
        cpvs = [normalize_cpv(c.text or "") for c in root.xpath("//cpv-code/*")]
    offer = _t(root, "/*/EINGANG_ANGEBOTE")
    request = _t(root, "/*/EINGANG_TNANTRAG")
    return {
        "format": "national",
        "title": _t(root, "/*/THEMA"),
        "description": _clean(root.findtext("LEISTUNGASCII")),
        "buyer_name": _t(ca, "name"),
        "buyer_city": _t(ca, "town"),
        "buyer_country": "DE",
        "cpv": list(dict.fromkeys(c for c in cpvs if c)),
        "deadline": parse_dmy(offer) or parse_dmy(request),
        "notice_type": NoticeType.PLAN if _t(root, "/*/VORINFORMATION") == "true" else NoticeType.TENDER,
        "eu": _t(root, "/*/EU_VERFAHREN") == "true",
        "place": _t(root, "/*/ERFUELLUNGSORT"),
        "reference": _t(root, "/*/GESCHAEFTSZEICHEN"),
        "language": "de",
    }


def _parse_eforms(root: etree._Element) -> dict[str, Any]:
    buyer_ref = _t(root, "/*/ContractingParty/Party/PartyIdentification/ID")
    orgs = root.xpath("//Organizations/Organization/Company")
    org = next((o for o in orgs if _t(o, "PartyIdentification/ID") == buyer_ref), orgs[0] if orgs else None)
    project = root.find("ProcurementProject")
    cpvs = [normalize_cpv(c.text or "") for c in root.xpath("//ItemClassificationCode[@listName='cpv']")]
    tp = "//TenderingProcess/"
    deadline = (_eforms_dt(_t(root, f"({tp}TenderSubmissionDeadlinePeriod)[1]/EndDate"),
                           _t(root, f"({tp}TenderSubmissionDeadlinePeriod)[1]/EndTime"))
                or _eforms_dt(_t(root, f"({tp}ParticipationRequestReceptionPeriod)[1]/EndDate"),
                              _t(root, f"({tp}ParticipationRequestReceptionPeriod)[1]/EndTime")))
    ntc = root.find("NoticeTypeCode")
    amount = root.xpath("/*/ProcurementProject/RequestedTenderTotal/EstimatedOverallContractAmount")
    value = None
    if amount and amount[0].text:
        try:
            value = float(amount[0].text)
        except ValueError:
            value = None
    return {
        "format": "eforms",
        "title": _t(project, "Name"),
        "description": _t(project, "Description"),
        "buyer_name": _t(org, "PartyName/Name"),
        "buyer_id": _t(org, "PartyLegalEntity/CompanyID"),
        "buyer_city": _t(org, "PostalAddress/CityName"),
        "buyer_country": iso3_to_iso2(_t(org, "PostalAddress/Country/IdentificationCode")),
        "cpv": list(dict.fromkeys(c for c in cpvs if c)),
        "deadline": deadline,
        "notice_type": FORM_TYPES.get(ntc.get("listName", "") if ntc is not None else "", NoticeType.OTHER),
        "notice_subtype": _t(root, "//NoticeSubType/SubTypeCode"),
        "form": root.tag,
        "issued": _eforms_dt(_t(root, "/*/IssueDate"), _t(root, "/*/IssueTime")),
        "legal_basis": _t(root, "/*/RegulatoryDomain"),
        "procedure_code": _t(root, "/*/TenderingProcess/ProcedureCode"),
        "notice_id": _t(root, "/*/ID[@schemeName='notice-id']"),
        "procedure_id": _t(root, "/*/ContractFolderID"),
        "value": value,
        "currency": amount[0].get("currencyID") if amount else None,
        "language": lang3_to_lang2(_t(root, "/*/NoticeLanguageCode")),
        "eu": True,
    }


# --------------------------------------------------------------------------- connector


def _buyer_name(xml_name: str | None, vergabestelle: str | None) -> str | None:
    """The XML names the actual contracting authority; the list names the platform account (Vergabestelle,
    often 'Land: Name'). Keep the authority's name. Bundeswehr bodies often publish under compound or
    abbreviated names ('Bundeswehrverwaltung', 'BAIUDBw ...', 'BwDLZ ...') that the shared defence
    classifier's word-boundary patterns miss; for those we append the parent organisation '(Bundeswehr)'."""
    account = _STATE_PREFIX.sub("", vergabestelle or "").strip() or None
    name = xml_name or account or vergabestelle or None
    if name and not BUYER_PATTERNS.search(name) and (
            _BUNDESWEHR.search(name) or (account and _BUNDESWEHR.search(account))):
        return f"{name} (Bundeswehr)"
    return name


def _fingerprint(row: dict[str, str]) -> str:
    """Short hash of the list row's visible fields: a change means the notice was edited."""
    key = "|".join(row.get(k, "") for k in ("title", "reference", "vergabestelle", "procedure", "next_deadline",
                                             "published"))
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _list_notice_type(procedure: str) -> NoticeType:
    p = procedure.lower()
    if "vorinformation" in p or "nichtverbindliche bekanntmachung" in p:
        return NoticeType.PLAN
    return NoticeType.TENDER


class EvergabeOnline(Connector):
    name = "evergabe_online"
    title = "e-Vergabe — Vergabeplattform des Bundes"
    countries = ("DE",)
    homepage = "https://www.evergabe-online.de"
    kind = "platform"
    poll_interval = timedelta(hours=1)
    first_sync_lookback = timedelta(days=7)
    requests_per_second = 3.0
    supports_refresh = True

    # ----------------------------------------------------------------- session / paging

    async def _first_page(self) -> tuple[str, str]:
        """Open a search session (cookie check) and switch it to 100 rows per page. Returns (html, page_url)."""
        r = await self.http.get(SEARCH)
        url, text = str(r.url), r.text
        if "cookieCheck" in url:  # first contact: the cookie is set now; get a clean page id
            r = await self.http.get(SEARCH, headers={"Referer": url})
            url, text = str(r.url), r.text
        m = _AJAX_ROWS.search(text)
        if not m:
            return text, url
        ajax_url = urljoin(url, m.group(1).replace("&amp;", "&"))
        p = urlparse(url)
        base_rel = p.path.lstrip("/") + (f"?{p.query}" if p.query else "")
        try:
            r = await self.http.post(ajax_url, data={"rowsPerPageChoice": ROWS_100}, headers={
                "Wicket-Ajax": "true", "Wicket-Ajax-BaseURL": base_rel, "X-Requested-With": "XMLHttpRequest",
                "Origin": BASE.rstrip("/"), "Referer": url, "Accept": "text/xml",
            })
        except HttpError as e:  # keep going with 10 rows/page rather than failing the sync
            self.log.warning("evergabe_online: could not switch to 100 rows/page (%s)", e)
            return text, url
        html = page_html(r.text)
        return (html, url) if parse_rows(html) else (text, url)

    async def _get_page(self, link: str, referer: str) -> tuple[str, str]:
        r = await self.http.get(link, headers={"Referer": referer})
        return page_html(r.text), str(r.url)

    async def _xml(self, tender_id: str) -> dict[str, Any] | None:
        try:
            r = await self.http.get(XML_URL, params={"id": tender_id})
        except HttpError as e:
            if e.status in (404, 410):
                return None
            raise
        if not r.content.lstrip().startswith(b"<?xml") and b"<html" in r.content[:2000].lower():
            return None  # cookie/error page instead of the notice
        try:
            return parse_xml(r.content)
        except etree.XMLSyntaxError as e:
            self.log.warning("evergabe_online: bad XML for %s: %s", tender_id, e)
            return None

    async def _details(self, rows: list[dict[str, str]]) -> list[dict[str, Any] | None]:
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def one(row: dict[str, str]) -> dict[str, Any] | None:
            async with sem:
                try:
                    return await self._xml(row["id"])
                except HttpError as e:  # one broken notice must not stop the sync; map from the list row
                    self.log.warning("evergabe_online: XML for %s failed: %s", row["id"], e)
                    return None

        return await asyncio.gather(*(one(r) for r in rows))

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        """Walk the search newest-first (100 rows/page) until rows are older than `since`.

        Rows already yielded by an earlier run with an unchanged list row (title, reference, deadline, ...)
        are skipped instead of re-downloading their XML: the overlap window then costs ~2 requests instead of
        ~100. Later changes inside a notice are picked up by `refresh`.
        """
        since_day: date = since.astimezone(BERLIN).date()
        keep_from = (since_day - KNOWN_DAYS).isoformat()
        known: dict[str, list[str]] = {k: v for k, v in (state.get("known") or {}).items()
                                       if isinstance(v, list) and len(v) == 2 and v[0] >= keep_from}
        html, url = await self._first_page()
        skipped = 0
        for page in range(1, MAX_PAGES + 1):
            rows = parse_rows(html)
            fresh = [r for r in rows if (d := parse_dmy(r["published"])) is None or d.date() >= since_day]
            exhausted = len(fresh) < len(rows) or not rows
            nxt = None if exhausted else next_link(html, url)
            todo = [r for r in fresh if known.get(r["id"], [None, None])[1] != _fingerprint(r)]
            skipped += len(fresh) - len(todo)
            # fetch the next result page while this page's XMLs download
            next_task = asyncio.create_task(self._get_page(nxt, url)) if nxt else None
            try:
                for i in range(0, len(todo), CHUNK):
                    chunk = todo[i:i + CHUNK]
                    for row, x in zip(chunk, await self._details(chunk), strict=True):
                        yield self.to_notice(row, x)
                        if x is not None and (d := parse_dmy(row["published"])):
                            known[row["id"]] = [d.date().isoformat(), _fingerprint(row)]
                yield Checkpoint({"page": page, "total": total_hits(html), "skipped_known": skipped,
                                  "known": known})
                if next_task is None:
                    break
                html, url = await next_task
                next_task = None
            finally:
                if next_task is not None:
                    next_task.cancel()

    async def refresh(self, ref: StoredRef) -> Notice | None:
        row = dict((ref.raw or {}).get("list") or {})
        row.setdefault("id", ref.source_id)
        x = await self._xml(ref.source_id)
        if x is None:
            return None
        row["title"] = row.get("title") or x.get("title") or ref.source_id
        return self.to_notice(row, x)

    # ----------------------------------------------------------------- mapping

    def to_notice(self, row: dict[str, str], x: dict[str, Any] | None) -> Notice:
        x = x or {}
        tid = row["id"]
        published = parse_dmy(row.get("published"))
        issued = x.get("issued")
        if issued and published and issued.astimezone(BERLIN).date() == published.date():
            published = issued  # the eForms issue time is more precise than the list's publication day
        deadline = x.get("deadline") or parse_dmy(row.get("next_deadline"), end_of_day=True)
        ntype = x.get("notice_type") or _list_notice_type(row.get("procedure", ""))
        if ntype is NoticeType.OTHER:
            ntype = _list_notice_type(row.get("procedure", ""))
        status = Status.UNKNOWN
        if ntype is NoticeType.TENDER and deadline:
            status = Status.OPEN if deadline > self.ctx.now() else Status.CLOSED
        reference = row.get("reference") or x.get("reference")
        refs = {k: v for k, v in {
            "evergabe": tid, "reference": reference, "notice": x.get("notice_id"),
            "procedure": x.get("procedure_id"),
        }.items() if v}
        raw_x = {k: (v.isoformat() if isinstance(v, datetime) else str(v) if isinstance(v, NoticeType) else v)
                 for k, v in x.items() if k not in ("description",)}
        return Notice(
            source=self.name,
            source_id=tid,
            url=notice_url(tid),
            title=row.get("title") or x.get("title") or tid,
            notice_type=ntype,
            status=status,
            description=x.get("description"),
            buyer_name=_buyer_name(x.get("buyer_name"), row.get("vergabestelle")),
            buyer_id=x.get("buyer_id"),
            buyer_country=x.get("buyer_country") or "DE",
            buyer_city=x.get("buyer_city"),
            cpv_codes=x.get("cpv") or [],
            procedure_type=row.get("procedure") or x.get("procedure_code"),
            legal_basis=x.get("legal_basis"),
            published_at=published,
            deadline_at=deadline,
            value_amount=x.get("value"),
            value_currency=x.get("currency") if x.get("value") is not None else None,
            language=x.get("language") or "de",
            documents=[
                Document(f"{PDF_URL}?id={tid}", "Bekanntmachung (PDF)", "notice"),
                Document(f"{XML_URL}?id={tid}", "Bekanntmachung (XML)", "notice"),
            ],
            refs=refs,
            raw={"list": dict(row), "xml": raw_x},
        )
