"""PLACSP — Plataforma de Contratación del Sector Público (Spain): national, regional and local buyers,
including every Ministerio de Defensa contracting body (Ejército de Tierra, Armada, Ejército del Aire,
ISDEFE, ...), via the official open-data ATOM syndication.

Source (anonymous, no key):
    https://contrataciondelestado.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom
Entries are full CODICE/UBL `cac-place-ext:ContractFolderStatus` records, newest-first by `<updated>`; a
procurement folder gets a fresh entry every time it changes (new notice, award, cancellation, documents),
so walking back to `since` yields everything published *or changed* since then.

Layout (checked live 2026-10-30): the head document (~4 MB, latest changes) links `rel="next"` to a daily
snapshot written around 20:15 Madrid time, `..._YYYYMMDD_HHMMSS.atom`, `..._1.atom` ... `..._5.atom`
(~15 MB / 500 entries each), whose last page links to the previous day's snapshot, and so on. They are
static files (ETag, 304 on If-None-Match) but served at only ~150 KB/s per connection, so a page takes
~100 s. Speed therefore comes from:

* streaming + incremental parsing (lxml pull parser): the `rel="next"` link sits in the first 2 KB,
  so the next page's download starts while the current one is still arriving — up to
  `PARALLEL_PAGES` pages download concurrently, results are yielded strictly in feed order;
* stopping each download as soon as it reaches an entry older than the cutoff (a page that starts
  older than the cutoff costs ~2 KB), and never following a page that is already past it;
* a conditional GET of the head: when it has not changed since the last complete run, a sync is one
  empty 304 response;
* a cursor (`newest` entry of the last complete run, minus a margin) that tightens `since`.

Code lists (checked 2026-10-30):
    https://contrataciondelestado.es/codice/cl/2.04/SyndicationContractFolderStatusCode-2.04.gc
    https://contrataciondelestado.es/codice/cl/2.07/SyndicationTenderingProcessCode-2.07.gc
    https://contrataciondelestado.es/codice/cl/2.08/ContractCode-2.08.gc
"""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dateutil.parser import isoparse
from lxml import etree

from ..connector import Checkpoint, Connector
from ..http import RETRY_STATUSES, HttpError
from ..models import Document, Notice, NoticeType, Status, absolute_url, normalize_cpv

FEED = "https://contrataciondelestado.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom"
MADRID = ZoneInfo("Europe/Madrid")
CURSOR_MARGIN = timedelta(hours=1)   # re-read this much before the newest entry of the last complete run
PARALLEL_PAGES = 6                   # concurrent page downloads (each is bandwidth-bound at ~150 KB/s)
MAX_PAGES = 400                      # safety stop for `--since` backfills (~65 days)
MAX_RETRIES = 4
PAGE_TIMEOUT = httpx.Timeout(120.0, connect=30.0)

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "cbc": "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2",
    "pe": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2",
    "pb": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2",
}

# ContractFolderStatusCode -> (notice type, status). PUB keeps UNKNOWN when a deadline exists so the runner
# derives open/closed from it.
STATUS = {
    "PRE": (NoticeType.PLAN, Status.UNKNOWN),            # anuncio previo
    "PUB": (NoticeType.TENDER, Status.OPEN),             # en plazo
    "EV": (NoticeType.TENDER, Status.CLOSED),            # pendiente de adjudicación
    "ADJ": (NoticeType.AWARD, Status.AWARDED),           # adjudicada
    "RES": (NoticeType.AWARD, Status.AWARDED),           # resuelta (contract formalised)
    "ANUL": (NoticeType.CANCELLATION, Status.CANCELLED),  # anulada
}
PROCEDURES = {
    "1": "Abierto", "2": "Restringido", "3": "Negociado sin publicidad", "4": "Negociado con publicidad",
    "5": "Diálogo competitivo", "6": "Contrato menor", "7": "Derivado de acuerdo marco", "8": "Concurso de proyectos",
    "9": "Abierto simplificado", "10": "Asociación para la innovación",
    "11": "Derivado de asociación para la innovación", "12": "Basado en un sistema dinámico de adquisición",
    "13": "Licitación con negociación", "100": "Normas internas", "999": "Otros",
}
CONTRACT_TYPES = {
    "1": "Suministros", "2": "Servicios", "3": "Obras", "21": "Gestión de Servicios Públicos",
    "22": "Concesión de Servicios", "31": "Concesión de Obras Públicas", "32": "Concesión de Obras",
    "40": "Colaboración entre el sector público y sector privado", "7": "Administrativo especial",
    "8": "Privado", "50": "Patrimonial",
}
_DIRECTIVE = re.compile(r"^(\d{4})/(\d{1,4})/(?:EU|UE|EC|CE|EEC|CEE)$", re.IGNORECASE)


def _text(el: Any, path: str) -> str | None:
    if el is None:
        return None
    v = el.findtext(path, namespaces=NS)
    v = (v or "").strip()
    return v or None


def _all(el: Any, path: str) -> list[str]:
    return [t.strip() for t in (x.text for x in el.iterfind(path, NS)) if t and t.strip()]


def _unique(xs: list[str | None]) -> list[str]:
    return list(dict.fromkeys(x for x in xs if x))


def _date(v: str | None) -> datetime | None:
    """'2026-08-07' or '2026-08-06+02:00' -> midnight in Madrid (or the given offset)."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(Z|[+-]\d{2}:\d{2})?$", v or "")
    if not m:
        return None
    d = isoparse(m.group(1))
    if m.group(2):
        return isoparse(f"{m.group(1)}T00:00:00{'+00:00' if m.group(2) == 'Z' else m.group(2)}")
    return d.replace(tzinfo=MADRID)


def _deadline(day: str | None, time: str | None) -> datetime | None:
    d = _date(day)
    if d is None:
        return None
    if time and re.match(r"^\d{2}:\d{2}(:\d{2})?", time):
        t = isoparse(f"{d.date().isoformat()}T{time[:8]}")
        return t.replace(tzinfo=d.tzinfo) if t.tzinfo is None else t
    return d.replace(hour=23, minute=59)


def _amount(v: str | None) -> float | None:
    try:
        return float(v) if v else None
    except ValueError:
        return None


def _celex(ref: str | None) -> str | None:
    """'2014/24/EU' -> '32014L0024' (CELEX number of the directive); 'N/A' -> None."""
    m = _DIRECTIVE.match((ref or "").strip())
    return f"3{m.group(1)}L{int(m.group(2)):04d}" if m else None


def entry_updated(entry: Any) -> datetime | None:
    v = _text(entry, "a:updated")
    if not v:
        return None
    d = isoparse(v)
    return d if d.tzinfo else d.replace(tzinfo=MADRID)


def entry_id(entry: Any) -> str | None:
    v = _text(entry, "a:id")
    return v.rstrip("/").rsplit("/", 1)[-1] if v else None


A_LINK = "{http://www.w3.org/2005/Atom}link"
A_ENTRY = "{http://www.w3.org/2005/Atom}entry"


@dataclass(slots=True)
class Page:
    """One feed document, as far as it was read."""

    url: str
    etag: str | None = None
    not_modified: bool = False
    next: str | None = None                      # absolute URL of the older page
    items: list[tuple[str, datetime | None, Notice]] = field(default_factory=list)
    entries: int = 0                             # entries parsed (in scope or not)
    reached_cutoff: bool = False                 # saw an entry older than the cutoff -> the walk ends here


class PageParser:
    """Incremental (chunk-fed) parser for one feed page.

    Collects the in-scope entries as Notices, records the `rel="next"` link, and stops at the first entry
    older than `cutoff`. `on_ready(page)` fires once, as soon as it is known whether the walk continues past
    this page (at its first entry, or at the end of an entry-less page) — the caller uses it to start the
    next download while this one is still streaming.
    """

    def __init__(self, page: Page, cutoff: datetime, to_notice: Callable[[Any], Notice | None],
                 on_ready: Callable[[Page], None] | None = None):
        self.page, self.cutoff, self.to_notice, self.on_ready = page, cutoff, to_notice, on_ready
        self.fed = False
        self._ready = False
        self._pp = etree.XMLPullParser(events=("end",), tag=(A_LINK, A_ENTRY), huge_tree=True,
                                       resolve_entities=False, no_network=True)

    def feed(self, chunk: bytes) -> bool:
        """Feed bytes; returns True once the cutoff was reached (stop reading)."""
        if not chunk:
            return self.page.reached_cutoff
        self.fed = True
        self._pp.feed(chunk)
        return self._drain()

    def close(self) -> None:
        if self.fed:            # the server answers past-the-end pages with an empty 200
            self._pp.close()
            self._drain()
        self._signal()

    def _signal(self) -> None:
        if not self._ready:
            self._ready = True
            if self.on_ready:
                self.on_ready(self.page)

    def _drain(self) -> bool:
        page = self.page
        for _, el in self._pp.read_events():
            parent = el.getparent()
            if el.tag == A_LINK:
                if parent is not None and parent.getparent() is None and el.get("rel") == "next" and el.get("href"):
                    page.next = absolute_url(el.get("href"), page.url)
                continue
            page.entries += 1
            upd = entry_updated(el)
            if upd is not None and upd < self.cutoff:
                page.reached_cutoff = True
                self._signal()
                return True
            eid = entry_id(el)
            n = self.to_notice(el) if eid else None
            if n is not None:
                page.items.append((eid, upd, n))
            self._signal()
            el.clear()
            if parent is not None:
                while el.getprevious() is not None:
                    del parent[0]
        return False


def parse_page(content: bytes, url: str, cutoff: datetime, to_notice: Callable[[Any], Notice | None]) -> Page:
    """Parse a whole page at once (tests / tooling)."""
    page = Page(url)
    p = PageParser(page, cutoff, to_notice)
    if not p.feed(content):
        p.close()
    return page


class _Retryable(Exception):
    pass


def retry_delay(attempt: int) -> float:
    return min(60.0, 2 ** attempt) + random.random()


class Placsp(Connector):
    name = "placsp"
    title = "PLACSP — Plataforma de Contratación del Sector Público (Spain)"
    countries = ("ES",)
    homepage = "https://contrataciondelestado.es"
    kind = "portal"
    poll_interval = timedelta(hours=2)        # an unchanged head costs one empty 304
    first_sync_lookback = timedelta(days=2)   # ~3k changed folders per working day
    requests_per_second = 1.0                 # spacing of request *starts*; PARALLEL_PAGES caps concurrency

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        newest: datetime | None = isoparse(state["newest"]) if state.get("newest") else None
        cutoff = max(since, newest - CURSOR_MARGIN) if newest else since
        head_headers: dict[str, str] = {}
        if state.get("etag") and state.get("covered") and since >= isoparse(state["covered"]):
            head_headers["If-None-Match"] = state["etag"]   # nothing older than `covered` is needed

        tasks: list[asyncio.Task[Page]] = []
        launched: set[str] = set()
        slots = asyncio.Semaphore(PARALLEL_PAGES)

        def launch(url: str, headers: dict[str, str] | None = None) -> None:
            if url in launched:
                return
            if len(launched) >= MAX_PAGES:
                self.log.warning("placsp: stopping after %d pages (since=%s)", MAX_PAGES, since.isoformat())
                return
            launched.add(url)
            tasks.append(asyncio.create_task(fetch(url, headers)))

        def on_ready(page: Page) -> None:
            # follow as soon as this page is known to start inside the window (its next link precedes entries)
            if page.next and not (page.reached_cutoff and page.entries == 1):
                launch(page.next)

        async def fetch(url: str, headers: dict[str, str] | None) -> Page:
            async with slots:
                return await self._fetch_page(url, cutoff, headers, on_ready)

        launch(FEED, head_headers)
        seen: set[str] = set()
        etag: str | None = None
        done = 0
        try:
            while done < len(tasks):
                page = await tasks[done]   # strictly in feed order (newest first), later pages keep downloading
                done += 1
                if page.not_modified:
                    self.log.info("placsp: feed unchanged since the last complete run")
                    yield Checkpoint(dict(state))
                    return
                if done == 1:
                    etag = page.etag
                for eid, upd, n in page.items:
                    if eid in seen:   # newest-first: a repeated folder is an older version of it
                        continue
                    seen.add(eid)
                    if upd is not None and (newest is None or upd > newest):
                        newest = upd
                    yield n
                if page.reached_cutoff:
                    break
        finally:
            pending = tasks[done:]
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        new_state: dict[str, Any] = {"covered": cutoff.isoformat()}
        if newest:
            new_state["newest"] = newest.isoformat()
        if etag:
            new_state["etag"] = etag
        yield Checkpoint(new_state)

    async def _fetch_page(self, url: str, cutoff: datetime, headers: dict[str, str] | None,
                          on_ready: Callable[[Page], None]) -> Page:
        """Stream one page through the shared client + rate limiter, parsing as bytes arrive and closing the
        connection at the cutoff. Streaming is not offered by `Http`, so its retry policy is mirrored here."""
        for attempt in range(MAX_RETRIES + 1):
            page = Page(url)
            parser = PageParser(page, cutoff, self.to_notice, on_ready)
            await self.http.limiter.wait()
            self.http.requests += 1
            try:
                async with self.http.client.stream("GET", url, headers=headers or None, timeout=PAGE_TIMEOUT) as r:
                    if r.status_code == 304:
                        page.not_modified = True
                        return page
                    if r.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                        raise _Retryable(f"HTTP {r.status_code}")
                    if r.status_code >= 400:
                        raise HttpError(r.status_code, str(r.url), (await r.aread()).decode(errors="replace"))
                    page.etag = r.headers.get("etag")
                    async for chunk in r.aiter_bytes():
                        if parser.feed(chunk):
                            break
                    else:
                        parser.close()
                    return page
            except (httpx.TransportError, _Retryable) as e:
                if attempt == MAX_RETRIES:
                    raise
                delay = retry_delay(attempt)
                self.log.warning("GET %s failed (%s), retry in %.1fs", url, e, delay)
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    def to_notice(self, e: Any) -> Notice | None:
        eid = entry_id(e)
        cfs = e.find("pe:ContractFolderStatus", NS)
        if not eid or cfs is None:
            return None
        link = next((ln.get("href") for ln in e.iterfind("a:link", NS) if ln.get("href")), None)
        url = absolute_url(link) or absolute_url(_text(e, "a:id"))
        proj = cfs.find("cac:ProcurementProject", NS)
        title = _text(e, "a:title") or _text(proj, "cbc:Name") or _text(cfs, "cbc:ContractFolderID") or eid
        status_code = _text(cfs, "pb:ContractFolderStatusCode")
        notice_type, status = STATUS.get(status_code or "", (NoticeType.OTHER, Status.UNKNOWN))

        # --- buyer ---
        lcp = cfs.find("pe:LocatedContractingParty", NS)
        party = lcp.find("cac:Party", NS) if lcp is not None else None
        ids = {}
        if party is not None:
            for x in party.iterfind("cac:PartyIdentification/cbc:ID", NS):
                if x.text and x.get("schemeName") not in ids:
                    ids[x.get("schemeName")] = x.text.strip()
        parents = _all(lcp, ".//pe:ParentLocatedParty/cac:PartyName/cbc:Name") if lcp is not None else []
        buyer = _text(party, "cac:PartyName/cbc:Name")
        ministry = next((p for p in parents if p.lower().startswith("ministerio")), None)
        if buyer and ministry and ministry.lower() not in buyer.lower():
            buyer = f"{buyer} ({ministry})"   # e.g. "Jefatura ... Base Aérea de Albacete (Ministerio de Defensa)"
        country = _text(party, "cac:PostalAddress/cac:Country/cbc:IdentificationCode")

        # --- object, lots, CPV ---
        lots = []
        for lot in cfs.iterfind("cac:ProcurementProjectLot", NS):
            lp = lot.find("cac:ProcurementProject", NS)
            lots.append({
                "id": _text(lot, "cbc:ID"),
                "name": _text(lp, "cbc:Name"),
                "cpv": _unique([normalize_cpv(c) for c in _all(lp, "cac:RequiredCommodityClassification/"
                                                                    "cbc:ItemClassificationCode")])
                if lp is not None else [],
                "amount": _amount(_text(lp, "cac:BudgetAmount/cbc:TaxExclusiveAmount")),
            })
        cpv = _unique([normalize_cpv(c) for c in _all(proj, "cac:RequiredCommodityClassification/"
                                                            "cbc:ItemClassificationCode")] if proj is not None else [])
        cpv = _unique(cpv + [c for lot in lots for c in lot["cpv"]])
        contract_type = CONTRACT_TYPES.get(_text(proj, "cbc:TypeCode") or "")
        description = None
        if lots:
            names = "; ".join(f"{lot['id'] or i + 1}) {lot['name']}" for i, lot in enumerate(lots) if lot["name"])
            description = f"{contract_type + '. ' if contract_type else ''}{len(lots)} lotes: {names}"[:2000]
        elif contract_type:
            description = contract_type

        # --- value ---
        value, currency = None, None
        for tag in ("cbc:EstimatedOverallContractAmount", "cbc:TaxExclusiveAmount", "cbc:TotalAmount"):
            el = proj.find(f"cac:BudgetAmount/{tag}", NS) if proj is not None else None
            if el is not None and _amount(el.text) is not None:
                value, currency = _amount(el.text), el.get("currencyID")
                break

        # --- process ---
        tp = cfs.find("cac:TenderingProcess", NS)
        procedure_code = _text(tp, "cbc:ProcedureCode")
        deadline = _deadline(_text(tp, "cac:TenderSubmissionDeadlinePeriod/cbc:EndDate"),
                             _text(tp, "cac:TenderSubmissionDeadlinePeriod/cbc:EndTime"))
        if notice_type is NoticeType.TENDER and status is Status.OPEN and deadline is not None:
            status = Status.UNKNOWN   # let the runner decide open/closed from the deadline
        legislation = _all(cfs, "cac:TenderingTerms/cac:ProcurementLegislationDocumentReference/cbc:ID")
        legal_basis = next((c for c in (_celex(x) for x in legislation) if c), None)

        # --- publications (DOC_CN, DOC_CAN_ADJ, DOC_PIN, ...) ---
        pubs = []
        for vni in cfs.iterfind("pe:ValidNoticeInfo", NS):
            kind = _text(vni, "pb:NoticeTypeCode")
            for aps in vni.iterfind("pe:AdditionalPublicationStatus", NS):
                pubs.append({"type": kind, "media": _text(aps, "pb:PublicationMediaName"),
                             "date": _text(aps, "pe:AdditionalPublicationDocumentReference/cbc:IssueDate")})
        dates = [d for d in (_date(p["date"]) for p in pubs) if d]
        published = max(dates) if dates else entry_updated(e)

        # --- documents ---
        docs: list[Document] = []
        for path, kind in (("cac:LegalDocumentReference", "administrative_specification"),
                           ("cac:TechnicalDocumentReference", "technical_specification"),
                           ("cac:AdditionalDocumentReference", "annex")):
            for ref in cfs.iterfind(path, NS):
                u = absolute_url(_text(ref, "cac:Attachment/cac:ExternalReference/cbc:URI"))
                if u:
                    docs.append(Document(u, title=_text(ref, "cbc:ID"), kind=kind))
        for ref in cfs.iterfind("pe:GeneralDocument/pe:GeneralDocumentDocumentReference", NS):
            u = absolute_url(_text(ref, "cac:Attachment/cac:ExternalReference/cbc:URI"))
            if u:
                docs.append(Document(u, title=_text(ref, "cac:Attachment/cac:ExternalReference/cbc:FileName"),
                                     kind="general"))
        docs = list({d.url: d for d in docs}.values())

        # --- award ---
        awards = []
        for tr in cfs.iterfind("cac:TenderResult", NS):
            awards.append({
                "result": _text(tr, "cbc:ResultCode"),
                "date": _text(tr, "cbc:AwardDate"),
                "lot": _text(tr, "cac:AwardedTenderedProject/cbc:ProcurementProjectLotID"),
                "winner": _text(tr, "cac:WinningParty/cac:PartyName/cbc:Name"),
                "winner_id": _text(tr, "cac:WinningParty/cac:PartyIdentification/cbc:ID"),
                "amount": _amount(_text(tr, "cac:AwardedTenderedProject/cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount")),
                "tenders": _text(tr, "cbc:ReceivedTenderQuantity"),
            })

        folder_id = _text(cfs, "cbc:ContractFolderID")
        uuid = next((x.text.strip() for x in cfs.iterfind("cbc:UUID", NS)
                     if x.text and x.get("schemeName") == "TED"), None)
        refs = {k: v for k, v in {"placsp": eid, "expediente": folder_id, "procedure": uuid}.items() if v}
        raw = {
            "id": _text(e, "a:id"), "link": link, "updated": _text(e, "a:updated"), "summary": _text(e, "a:summary"),
            "folder_id": folder_id, "status_code": status_code, "contract_type": _text(proj, "cbc:TypeCode"),
            "contract_subtype": _text(proj, "cbc:SubTypeCode"), "procedure_code": procedure_code,
            "buyer_ids": ids, "buyer_parents": parents, "buyer_profile": _text(lcp, "cbc:BuyerProfileURIID"),
            "buyer_type": _text(lcp, "cbc:ContractingPartyTypeCode"),
            "nuts": _text(proj, "cac:RealizedLocation/cbc:CountrySubentityCode"),
            "over_threshold": _text(tp, "cbc:OverThresholdIndicator"), "legislation": legislation,
            "publications": pubs, "lots": lots, "awards": awards,
        }
        return Notice(
            source=self.name,
            source_id=eid,
            url=url or FEED,
            title=title,
            notice_type=notice_type,
            status=status,
            description=description,
            buyer_name=buyer,
            buyer_id=ids.get("NIF") or ids.get("DIR3"),
            buyer_country=(country or "ES").upper()[:2],
            buyer_city=_text(party, "cac:PostalAddress/cbc:CityName"),
            cpv_codes=cpv,
            procedure_type=PROCEDURES.get(procedure_code or "", procedure_code),
            legal_basis=legal_basis,
            published_at=published,
            deadline_at=deadline,
            value_amount=value,
            value_currency=currency,
            platform_url=None,
            language="es",
            documents=docs,
            refs=refs,
            raw=raw,
        )
