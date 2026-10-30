"""TenderNed — the Netherlands' national e-procurement platform (incl. Ministerie van Defensie / DMO).

Uses the public JSON API the tenderned.nl frontend calls (anonymous, no key):

- list:      GET /papi/tenderned-rs-tns/v2/publicaties?page=N&size=100&publicatieDatumVanaf=YYYY-MM-DD
             newest first, Spring-style paging ({"content": [...], "last": bool, "number": N}).
             The list items carry no CPV codes, legal framework or publication time.
- detail:    GET /papi/tenderned-rs-tns/v2/publicaties/<publicatieId>  (CPV, juridisch kader, formType, TED number)
- documents: GET /papi/tenderned-rs-tns/v2/publicaties/<publicatieId>/documenten
- human:     https://www.tenderned.nl/aankondigingen/overzicht/<publicatieId>

Every publication (including a rectification) gets its own immutable publicatieId; publications of the
same procedure share a `kenmerk`. To stay polite on hourly polls, ids already yielded are remembered in
the checkpoint state and their details are not fetched again.

Speed: list pages are prefetched ahead and detail/document requests run concurrently (bounded), so a sync
runs at the rate limit instead of at one round trip per request.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse

from ..connector import Checkpoint, Connector, StoredRef
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status, absolute_url, normalize_cpv

BASE = "https://www.tenderned.nl"
API = f"{BASE}/papi/tenderned-rs-tns/v2/publicaties"
HUMAN = f"{BASE}/aankondigingen/overzicht/{{id}}"
PAGE_SIZE = 100
MAX_PAGES = 200
PAGE_LOOKAHEAD = 2        # list pages fetched ahead of the one being processed
DETAIL_CONCURRENCY = 10   # detail/document requests in flight (the rate limiter still caps req/s)
TZ = ZoneInfo("Europe/Amsterdam")

# aankondigingCode / typePublicatie.code
TYPES = {
    "AAO": NoticeType.TENDER,        # Aankondiging opdracht
    "AGO": NoticeType.AWARD,         # Aankondiging gegunde opdracht (also VEAT / vrijwillig gunningsbericht)
    "REC": NoticeType.MODIFICATION,  # Rectificatie
    "AAW": NoticeType.MODIFICATION,  # Aankondiging van wijziging van een opdracht
    "MAC": NoticeType.PLAN,          # Marktconsultatie
    "VAK": NoticeType.PLAN,          # Vooraankondiging
    "VBE": NoticeType.CANCELLATION,  # Vroegtijdige beëindiging
}
# eForms formType from the detail record, used when the TenderNed code is unknown
FORM_TYPES = {
    "planning": NoticeType.PLAN,
    "competition": NoticeType.TENDER,
    "result": NoticeType.AWARD,
    "dir-awa-pre": NoticeType.AWARD,
    "change": NoticeType.MODIFICATION,
    "cont-modif": NoticeType.MODIFICATION,
    "consultation": NoticeType.PLAN,
}
# EU directive (CELEX) by the wording of the eForms notice type, e.g. "... - defensierichtlijn, standaardregeling"
DIRECTIVES = {
    "defensierichtlijn": "32009L0081",
    "sectorale richtlijn": "32014L0025",
    "concessierichtlijn": "32014L0023",
    "algemene richtlijn": "32014L0024",
}
DOC_TYPES = {NoticeType.TENDER, NoticeType.PLAN, NoticeType.MODIFICATION}  # TenderNed hosts tender documents


def _code(v: Any) -> str | None:
    return v.get("code") if isinstance(v, dict) else None


def _desc(v: Any) -> str | None:
    if isinstance(v, dict):
        return v.get("omschrijving") or None
    return v if isinstance(v, str) and v else None


def _dt(v: str | None, *, end_of_day: bool = False) -> datetime | None:
    """TenderNed times are naive Amsterdam local time ('2026-11-10T17:00:00' or '2026-11-02')."""
    if not v:
        return None
    try:
        d = isoparse(v)
    except ValueError:
        return None
    if len(v) == 10 and end_of_day:
        d = d.replace(hour=23, minute=59)
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def _unique(xs: list[str | None]) -> list[str]:
    return list(dict.fromkeys(x for x in xs if x))


class TenderNed(Connector):
    name = "tenderned"
    title = "TenderNed (Netherlands)"
    countries = ("NL",)
    homepage = "https://www.tenderned.nl"
    kind = "portal"
    poll_interval = timedelta(hours=1)
    first_sync_lookback = timedelta(days=7)
    requests_per_second = 5.0     # JSON API (contract max); requests run concurrently up to this rate
    supports_refresh = True

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        since_day = since.astimezone(TZ).date()
        since_iso = since_day.isoformat()
        # publicatieId -> publication date of ids already yielded (publications are immutable)
        seen: dict[str, str] = {k: v for k, v in (state.get("seen") or {}).items()
                                if v >= (since_day - timedelta(days=2)).isoformat()}
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def list_page(page: int) -> dict[str, Any]:
            params = {"page": page, "size": PAGE_SIZE, "publicatieDatumVanaf": since_iso}
            return await self.http.get_json(API, params=params)

        async def detail(pid: str, item: dict[str, Any]) -> Notice | None:
            async with sem:
                return await self._fetch(pid, fallback=item)

        # Pipeline: a producer walks the list pages (prefetching PAGE_LOOKAHEAD pages ahead) and starts a
        # detail task per new publication right away; the consumer below yields them in list order.
        # Throughput is bounded only by the shared rate limiter, not by per-request latency.
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
        tasks: list[asyncio.Task[Any]] = []

        def spawn(coro: Any) -> asyncio.Task[Any]:
            t = asyncio.ensure_future(coro)
            tasks.append(t)
            return t

        async def produce() -> None:
            try:
                pages: dict[int, asyncio.Task[Any]] = {0: spawn(list_page(0))}
                total: int | None = None
                spawned: set[str] = set()
                for page in range(MAX_PAGES):
                    data = await pages.pop(page)
                    if total is None:
                        total = min(int(data.get("totalPages") or 0), MAX_PAGES) or None
                    # prefetch the next pages (known from totalPages, else one at a time via `last`)
                    upto = min(page + PAGE_LOOKAHEAD, (total or page + 2) - 1)
                    for p in range(page + 1, upto + 1):
                        if p not in pages:
                            pages[p] = spawn(list_page(p))
                    items = data.get("content") or []
                    older = False
                    for item in items:
                        pid = str(item.get("publicatieId") or "")
                        day = (item.get("publicatieDatum") or "")[:10]
                        if not pid:
                            continue
                        if day and day < since_iso:
                            older = True  # list is newest first; the date filter should already exclude these
                            continue
                        if pid in seen or pid in spawned:  # spawned: items shifting pages mid-walk
                            continue
                        spawned.add(pid)
                        await queue.put(("notice", (pid, day or since_iso, spawn(detail(pid, item)))))
                    await queue.put(("page", None))
                    if not items or older or (data.get("last", True) if total is None else page + 1 >= total):
                        break
            except BaseException as e:  # hand any failure to the consumer, which re-raises it
                await queue.put(("error", e))
                raise
            finally:
                await queue.put(None)

        producer = spawn(produce())
        try:
            while (msg := await queue.get()) is not None:
                kind, val = msg
                if kind == "error":
                    raise val
                if kind == "page":
                    yield Checkpoint({"seen": dict(seen)})
                    continue
                pid, day, task = val
                n = await task
                if n is not None:
                    yield n
                seen[pid] = day
            await producer
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def refresh(self, ref: StoredRef) -> Notice | None:
        pid = ref.refs.get("tenderned") or ref.source_id
        return await self._fetch(pid)

    async def _fetch(self, pid: str, fallback: dict[str, Any] | None = None) -> Notice | None:
        try:
            detail = await self.http.get_json(f"{API}/{pid}")
        except HttpError as e:
            if e.status != 404:
                raise
            self.log.info("tenderned %s: detail not found", pid)
            return self.to_notice(fallback) if fallback else None
        docs = None
        if not detail.get("isGeimporteerd") and self._type(detail) in DOC_TYPES:
            try:
                docs = await self.http.get_json(f"{API}/{pid}/documenten")
            except HttpError as e:
                if e.status != 404:
                    raise
        return self.to_notice(detail, docs)

    @staticmethod
    def _type(x: dict[str, Any]) -> NoticeType:
        code = _code(x.get("aankondigingCode")) or _code(x.get("typePublicatie"))
        if x.get("isVroegtijdigBeeindigd") or x.get("isVroegtijdigeBeeindiging"):
            return NoticeType.CANCELLATION
        return TYPES.get(code or "") or FORM_TYPES.get(x.get("formType") or "", NoticeType.OTHER)

    def to_notice(self, x: dict[str, Any], docs: dict[str, Any] | None = None,
                  now: datetime | None = None) -> Notice:
        """Map a detail record (or, as a fallback, a list item) to a Notice."""
        now = now or datetime.now(TZ)
        pid = str(x["publicatieId"])
        notice_type = self._type(x)
        is_detail = "aankondigingCode" in x or "cpvCodes" in x

        published = _dt(x.get("publicatieDatum"))
        deadline = _dt(x.get("sluitingsDatum")) or _dt(x.get("sluitingsDatumMarktconsultatie"), end_of_day=True)

        if notice_type is NoticeType.CANCELLATION:
            status = Status.CANCELLED
        elif notice_type is NoticeType.AWARD:
            status = Status.AWARDED
        elif notice_type in (NoticeType.TENDER, NoticeType.MODIFICATION, NoticeType.PLAN) and deadline:
            status = Status.OPEN if deadline > now else Status.CLOSED
        else:
            status = Status.UNKNOWN

        cpvs = sorted(x.get("cpvCodes") or [], key=lambda c: not c.get("isHoofdOpdracht"))
        form_desc = (x.get("typePublicatie") if is_detail else _desc(x.get("publicatiecode"))) or ""
        legal = _desc(x.get("juridischKaderCode"))
        legal = None if (legal or "").lower() in ("anders", "onbekend") else legal
        if _code(x.get("juridischKaderCode")) == "DEF":
            legal_basis = DIRECTIVES["defensierichtlijn"]
        else:
            europees = _code(x.get("nationaalOfEuropeesCode")) == "EU" if is_detail else x.get("europees")
            legal_basis = next((v for k, v in DIRECTIVES.items() if k in form_desc), None) if europees else None
            legal_basis = legal_basis or legal

        url = HUMAN.format(id=pid)
        documents = []
        pdf = ((x.get("links") or {}).get("pdf") or {}).get("href")
        if pdf:
            documents.append(Document(absolute_url(pdf, BASE), title="Aankondiging (PDF)", kind="notice"))
        for d in (docs or {}).get("documenten") or []:
            href = ((d.get("links") or {}).get("download") or {}).get("href")
            u = absolute_url(href, BASE) if href else None
            if u:
                cat = _code(d.get("publicatieCategorie"))
                kind = {"ANK": "notice", "DOC": "procurement_documents", "MAC": "consultation"}.get(cat or "", cat)
                documents.append(Document(u, title=d.get("documentNaam"), kind=kind and kind.lower()))
        documents = list({d.url: d for d in documents}.values())

        refs = {
            "tenderned": pid,
            "tenderned_kenmerk": str(x["kenmerk"]) if x.get("kenmerk") else None,
            "ted": x.get("pbNummerTed"),
            "reference": x.get("referentieNummer"),
        }
        raw = {k: v for k, v in x.items() if k not in ("links", "link", "score", "aantalDagenTotSluitingsDatum",
                                                       "numberOfDaysBeforeAanmeldenInschrijven")}
        title = (x.get("aanbestedingNaam") or "").strip()
        return Notice(
            source=self.name,
            source_id=pid,
            url=url,
            title=title or f"TenderNed {pid}",
            notice_type=notice_type,
            status=status,
            description=(x.get("opdrachtBeschrijving") or "").strip() or None,
            buyer_name=(x.get("opdrachtgeverNaam") or "").strip() or None,
            buyer_country="NL",
            cpv_codes=_unique([normalize_cpv(c.get("code") or "") for c in cpvs]),
            procedure_type=_desc(x.get("procedureCode") if is_detail else x.get("procedure")),
            legal_basis=legal_basis,
            published_at=published,
            deadline_at=deadline,
            platform_url=absolute_url(x.get("urlTsenderWebsite") or x.get("tsenderLink")),
            language="nl",
            documents=documents,
            refs={k: v for k, v in refs.items() if v},
            raw=raw,
        )
