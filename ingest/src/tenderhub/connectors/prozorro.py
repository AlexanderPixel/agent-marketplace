"""Prozorro — Ukraine's national e-procurement system (every public tender in UA, incl. defence buyers).

Official open API (OpenProcurement 2.5), anonymous, no key:
  feed   GET https://public-api.prozorro.gov.ua/api/2.5/tenders?offset=<unix ts|cursor>&limit=1000&opt_fields=...
         -> {"data": [{"id", "dateModified", <opt_fields>}], "next_page": {"offset": "<cursor>"}}
         ascending by dateModified; each tender appears once, at its latest modification.
         `opt_fields` only accepts a few fields (status, tenderID, procurementMethodType, procuringEntity,
         tenderPeriod, dateCreated) — no title/CPV, so every notice needs a detail request.
  detail GET https://public-api.prozorro.gov.ua/api/2.5/tenders/<id>  -> {"data": {...full tender...}}
Human page: https://prozorro.gov.ua/tender/<tenderID>.

Volume is very high (~20k feed changes/day, mostly direct-award reports and post-deadline status
changes), so the feed is filtered on `status` and only tenders still accepting bids/questions
(active.enquiries, active.tendering) are fetched in detail. Later lifecycle changes of stored open
notices (cancellation, award) are picked up by `refresh`.

Speed: the sync is a pipeline. The next feed page is prefetched while the current one's details are
in flight, details run in a sliding window of DETAIL_CONCURRENCY requests (no per-batch barrier) and
are yielded as soon as each one lands, so the shared rate limiter (5 req/s) is the only bottleneck
even when individual detail responses take >1 s. Tender versions already fetched inside the resume
window are remembered in the checkpoint and not fetched again on the next run. Measured live: 4.9 req/s
sustained (the cap); 3 days of feed = 61k changes / 65 pages, of which ~10.2k open tenders need a detail
request, so a 3-day backfill takes ~35 min while a 30-minute incremental poll takes well under a minute.
"""

from __future__ import annotations

import asyncio
import re
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from dateutil.parser import isoparse

from ..connector import Checkpoint, Connector, StoredRef
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status, absolute_url, normalize_cpv

API = "https://public-api.prozorro.gov.ua/api/2.5/tenders"
HUMAN_URL = "https://prozorro.gov.ua/tender/{}"
OCID_PREFIX = "ocds-be6bcu-"
PAGE_SIZE = 1000
OPT_FIELDS = "status"        # the only feed field we filter on; keeps a 1000-row page at ~120 KB
OPEN_STATUSES = {"active.enquiries", "active.tendering"}
DETAIL_CONCURRENCY = 16    # in-flight detail requests (responses take 0.3-1.5 s); the rate limiter caps req/s
BACKLOG = 400              # stop reading ahead in the feed while this many details are still queued
RESUME_REWIND = 300.0      # seconds re-read before the checkpointed position (feed writes can land slightly late)

STATUS = {
    "active.enquiries": Status.OPEN,
    "active.tendering": Status.OPEN,
    "active.pre-qualification": Status.CLOSED,
    "active.pre-qualification.stand-still": Status.CLOSED,
    "active.stage2.pending": Status.CLOSED,
    "active.stage2.waiting": Status.CLOSED,
    "active.auction": Status.CLOSED,
    "active.qualification": Status.CLOSED,
    "active.qualification.stand-still": Status.CLOSED,
    "active.awarded": Status.AWARDED,
    "complete": Status.AWARDED,
    "cancelled": Status.CANCELLED,
    "unsuccessful": Status.CLOSED,
}
# procedures that announce an already-chosen supplier (no competition)
DIRECT_AWARD_TYPES = {"reporting", "negotiation", "negotiation.quick"}
COUNTRY_NAMES = {"україна": "UA", "ukraine": "UA", "украина": "UA"}
CPV_SCHEMES = {"ДК021", "CPV"}
_CITY_PREFIX = re.compile(r"^\s*(м\.|місто|смт\.?|с\.)\s*", re.IGNORECASE)


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = isoparse(v)
    except ValueError:
        return None
    return d if d.tzinfo else None


def _clean(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    v = " ".join(v.split())
    return v or None


def _country(name: str | None) -> str | None:
    if not name:
        return "UA"  # Prozorro buyers are Ukrainian; addresses without a country are domestic
    return COUNTRY_NAMES.get(name.strip().lower())


def _city(locality: str | None) -> str | None:
    c = _clean(locality)
    return _clean(_CITY_PREFIX.sub("", c)) if c else None


def _documents(docs: list[dict[str, Any]] | None) -> list[Document]:
    """Tender-level documents, latest version of each (Prozorro keeps old versions under the same id)."""
    latest: dict[str, dict[str, Any]] = {}
    for d in docs or []:
        if d.get("documentOf", "tender") != "tender":
            continue
        title = d.get("title") or ""
        if title.lower().endswith(".p7s"):  # detached e-signature of the notice
            continue
        latest[d.get("id") or d.get("url") or title] = d
    out = []
    for d in latest.values():
        url = absolute_url(d.get("url"))
        if url:
            out.append(Document(url, title=_clean(d.get("title")), kind=d.get("documentType")))
    return out


def _trim(t: dict[str, Any]) -> dict[str, Any]:
    """Keep the useful core of a tender (details can be 500 KB with criteria/questions/bids)."""
    keys = ("id", "tenderID", "status", "procurementMethod", "procurementMethodType", "mainProcurementCategory",
            "title", "title_en", "dateCreated", "dateModified", "noticePublicationDate", "enquiryPeriod",
            "tenderPeriod", "value", "submissionMethod")
    out = {k: t[k] for k in keys if k in t}
    pe = dict(t.get("procuringEntity") or {})
    pe.pop("contactPoint", None)
    pe.pop("additionalContactPoints", None)
    out["procuringEntity"] = pe
    out["items"] = [{k: i[k] for k in ("description", "classification", "quantity", "unit") if k in i}
                    for i in (t.get("items") or [])[:20]]
    out["lots"] = [{k: lot[k] for k in ("id", "title", "status", "value") if k in lot}
                   for lot in (t.get("lots") or [])[:20]]
    return out


@dataclass(slots=True)
class _Page:
    """One feed page in the pipeline: its resume timestamp and the detail fetches it still waits on."""

    ts: float | None
    pending: int = 0
    ids: list[tuple[str, float, str]] = field(default_factory=list)


class Prozorro(Connector):
    name = "prozorro"
    title = "Prozorro — Ukraine public procurement"
    countries = ("UA",)
    homepage = "https://prozorro.gov.ua"
    kind = "portal"
    poll_interval = timedelta(minutes=30)
    overlap = timedelta(hours=2)
    first_sync_lookback = timedelta(days=1)   # ~3-4k open tenders change per day, each needs a detail request
    requests_per_second = 5.0
    supports_refresh = True

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        offset = state.get("offset") or f"{since.timestamp():.6f}"
        # id -> dateModified of open tenders already fetched inside the resume window (skip re-fetching them)
        recent: dict[str, str] = dict(state.get("recent") or {})
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
        pages: deque[_Page] = deque()
        inflight: dict[asyncio.Task[dict[str, Any] | None], _Page] = {}
        fetched: dict[str, str] = {}       # this run: id -> dateModified, guards against a re-listed tender
        window: dict[str, tuple[float, str]] = {}  # completed pages: id -> (ts, dateModified), pruned to the rewind
        feed_offset = offset
        feed: asyncio.Task[Any] | None = asyncio.create_task(self._feed(offset))
        checkpointed = False

        async def detail(tid: str) -> dict[str, Any] | None:
            async with sem:
                return await self._fetch(tid)

        try:
            while True:
                # read ahead in the feed while the detail queue is short; the next page is always prefetched
                while feed is not None and (len(inflight) < BACKLOG or not inflight):
                    data = await feed
                    feed = None
                    rows = data.get("data") or []
                    nxt = (data.get("next_page") or {}).get("offset")
                    if rows and nxt and nxt != feed_offset:
                        feed_offset = nxt
                        feed = asyncio.create_task(self._feed(nxt))
                    if not rows:
                        break
                    last = _dt(rows[-1].get("dateModified"))
                    page = _Page(ts=last.timestamp() if last else (pages[-1].ts if pages else None))
                    for r in rows:
                        tid, dm = r.get("id"), r.get("dateModified") or ""
                        if not tid or r.get("status") not in OPEN_STATUSES:
                            continue
                        d = _dt(dm)
                        if fetched.get(tid) == dm:
                            continue
                        fetched[tid] = dm
                        page.ids.append((tid, d.timestamp() if d else 0.0, dm))
                        if recent.get(tid) == dm:   # this exact version was stored by the previous run
                            continue
                        task = asyncio.create_task(detail(tid))
                        inflight[task] = page
                        page.pending += 1
                    pages.append(page)

                if inflight:
                    done, _ = await asyncio.wait(inflight, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        inflight.pop(task).pending -= 1
                    errors = [e for e in (task.exception() for task in done) if e is not None]
                    if errors:
                        raise errors[0]     # `finally` cancels everything still in flight
                    for task in done:
                        if (t := task.result()) is not None:
                            yield self.to_notice(t)

                # resume point: the newest feed page whose details have all been yielded
                while pages and pages[0].pending == 0:
                    page = pages.popleft()
                    if page.ts is None:
                        continue
                    for tid, ts, dm in page.ids:
                        window[tid] = (ts, dm)
                    floor = page.ts - RESUME_REWIND
                    window = {k: v for k, v in window.items() if v[0] >= floor}
                    yield Checkpoint({"offset": f"{floor:.6f}", "recent": {k: v[1] for k, v in window.items()}})
                    checkpointed = True

                if feed is None and not inflight and not pages:
                    break
            if not checkpointed:
                yield Checkpoint({"offset": offset, "recent": recent})
        finally:
            for task in [*inflight, *([feed] if feed else [])]:
                task.cancel()
            await asyncio.gather(*inflight, *([feed] if feed else []), return_exceptions=True)

    async def _feed(self, offset: str) -> dict[str, Any]:
        return await self.http.get_json(API, params={"offset": offset, "limit": PAGE_SIZE, "opt_fields": OPT_FIELDS})

    async def _fetch(self, tender_id: str) -> dict[str, Any] | None:
        try:
            data = await self.http.get_json(f"{API}/{tender_id}")
        except HttpError as e:
            if e.status in (404, 410):
                return None
            raise
        return data.get("data") if isinstance(data, dict) else None

    async def refresh(self, ref: StoredRef) -> Notice | None:
        tid = ref.refs.get("prozorro_id") or ref.raw.get("id")
        if not tid:
            return None
        t = await self._fetch(tid)
        return self.to_notice(t) if t else None

    def to_notice(self, t: dict[str, Any]) -> Notice:
        tender_id = t["tenderID"]
        pe = t.get("procuringEntity") or {}
        ident = pe.get("identifier") or {}
        addr = pe.get("address") or {}
        tp = t.get("tenderPeriod") or {}
        ep = t.get("enquiryPeriod") or {}
        value = t.get("value") or {}
        title = _clean(t.get("title")) or _clean(t.get("title_en")) or tender_id
        description = _clean(t.get("description"))
        cpvs = [normalize_cpv((i.get("classification") or {}).get("id", ""))
                for i in t.get("items") or [] if (i.get("classification") or {}).get("scheme") in CPV_SCHEMES]
        pmt = t.get("procurementMethodType")
        amount = value.get("amount")
        return Notice(
            source=self.name,
            source_id=tender_id,
            url=HUMAN_URL.format(tender_id),
            title=title,
            notice_type=NoticeType.AWARD if pmt in DIRECT_AWARD_TYPES else NoticeType.TENDER,
            status=STATUS.get(t.get("status", ""), Status.UNKNOWN),
            description=description if description != title else None,
            buyer_name=_clean(pe.get("name")) or _clean(ident.get("legalName")),
            buyer_id=_clean(ident.get("id")),
            buyer_country=_country(addr.get("countryName")),
            buyer_city=_city(addr.get("locality")),
            cpv_codes=list(dict.fromkeys(c for c in cpvs if c)),
            procedure_type=pmt,
            published_at=_dt(t.get("noticePublicationDate")) or _dt(ep.get("startDate"))
            or _dt(tp.get("startDate")) or _dt(t.get("dateCreated")),
            deadline_at=_dt(tp.get("endDate")),
            value_amount=float(amount) if isinstance(amount, (int, float)) else None,
            value_currency=value.get("currency") if amount is not None else None,
            language="uk",
            documents=_documents(t.get("documents")),
            refs={"ocid": OCID_PREFIX + tender_id, "procedure": tender_id, "prozorro_id": t["id"]},
            raw=_trim(t),
        )
