"""BZP — Biuletyn Zamówień Publicznych on e-Zamówienia: every Polish below-EU-threshold notice.

Source: the public, key-less JSON API behind the e-Zamówienia notice board
    GET https://ezamowienia.gov.pl/mo-board/api/v1/notice
        ?NoticeType=<type>&PublicationDateFrom=<naive UTC>&PublicationDateTo=<naive UTC>&PageSize=<n>
        [&SearchAfter=<objectId of the last item>] [&NoticeNumber=<"2026/BZP 00505976/01">]
`NoticeType` is mandatory and takes one value. Results come oldest-first (by objectId, which follows the
publication time); the cursor is `SearchAfter=<objectId>` and the last page is the one shorter than `PageSize`.
`PublicationDateFrom` is exact, but the server compares `publicationDate < PublicationDateTo + 1 day`
(checked 2026-10-30: To=10-28T12:00 returns notices up to 10-29T11:59), so a bounded window [a, b) is
requested as From=a, To=b-1d (and items >= b are dropped client-side as well).
Each record carries the full notice as `htmlBody` (~30 kB, the server does not compress), which we parse for
the bits the JSON lacks (platform URL, procedure, short description, value, plan number) and keep out of `raw`.

Speed. A 3-day backfill is ~9-10k notices / ~300 MB, and one response stream only runs at ~3 MB/s, while
HTTP/2 streams in parallel scale (4 concurrent ≈ 10 MB/s). So a sync runs everything concurrently:
  * every notice type is walked at the same time (bounded by MAX_IN_FLIGHT requests);
  * each type starts with a small probe page; if that page is full, the publication rate it shows sizes a
    fan-out: the rest of the window is cut into time slices of ~SLICE_TARGET notices, each walked as its own
    SearchAfter chain, all in parallel (a big type over 3 days becomes ~10 parallel one-request slices);
  * tender plans are paged newest-first by page number, so all estimated pages are fetched at once;
  * JSON decoding and HTML parsing run in worker threads while the event loop keeps downloading.

Tender plans (`TenderPlanNotice`, "plan postępowań") are rejected by the notice endpoint (HTTP 400); the board's
frontend lists them through `GET /mo-board/api/v1/Board/Search`, which returns light rows (no htmlBody), caps pages
at 10 items, pages with `PageNumber`, and sorts newest-first with `SortingColumnName=PublicationDate&
SortingDirection=DESC` (its date filters are unreliable, so the walk stops once rows are older than `since`).
Plan rows carry no title/CPV (only the buyer); their human page is `/mo-client-board/bzp/tender-details/<number>`.

The board sits behind an Azure WAF ("Dostęp zablokowany / Access Blocked", HTTP 403, lifts after about a
minute). On a block every request waits out a shared cool-down, retries, and the rest of the run goes serial
and slower instead of failing.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from dateutil.parser import isoparse
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..connector import Checkpoint, Connector, StoredRef
from ..http import HttpError
from ..models import Notice, NoticeType, Status, absolute_url

API = "https://ezamowienia.gov.pl/mo-board/api/v1/notice"
PLAN_API = "https://ezamowienia.gov.pl/mo-board/api/v1/Board/Search"
NOTICE_URL = "https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/id/{}"
PLAN_URL = "https://ezamowienia.gov.pl/mo-client-board/bzp/tender-details/{}"

MAX_IN_FLIGHT = 8      # concurrent requests (HTTP/2 streams) across the whole sync
PROBE_SIZE = 100       # first page of every type: cheap, and enough to measure its publication rate
PAGE_SIZE = 500        # pages after the probe (API maximum)
SLICE_TARGET = 250     # notices per fan-out slice: one request each, with headroom up to PAGE_SIZE
MAX_SLICES = 24
TO_SHIFT = timedelta(days=1)  # the API adds one day to PublicationDateTo
PLAN_PAGE_SIZE = 10    # Board/Search ignores larger PageSize values
MAX_PLAN_PAGES = 600   # safety stop (~300 plan versions are published per working day)
WAF_BACKOFF = 75.0     # seconds; the WAF block lifts after about a minute
WAF_RETRIES = 4
WAF_PAUSE = 2.0        # after a block: requests go one at a time with this gap (doubles per block, max 30)

# Every national (BZP) notice type the board's frontend offers.
# (The *EU variants are TED notices mirrored on the board; the `ted` connector covers those.)
NOTICE_TYPES: dict[str, NoticeType] = {
    "ContractNotice": NoticeType.TENDER,                      # ogłoszenie o zamówieniu
    "NoticeUpdateNotice": NoticeType.MODIFICATION,            # ogłoszenie o zmianie ogłoszenia (deadline changes...)
    "TenderResultNotice": NoticeType.AWARD,                   # ogłoszenie o wyniku postępowania
    "SmallContractNotice": NoticeType.AWARD,                  # concluded small-value contract (umowa zawarta)
    "CompetitionNotice": NoticeType.TENDER,                   # ogłoszenie o konkursie
    "ConcessionNotice": NoticeType.TENDER,
    "AgreementIntentionNotice": NoticeType.AWARD,             # zamiar zawarcia umowy (direct award, like VEAT)
    "ConcessionIntentionAgreementNotice": NoticeType.AWARD,
    "CompetitionResultNotice": NoticeType.AWARD,
    "ConcessionAgreementNotice": NoticeType.AWARD,
    "AgreementUpdateNotice": NoticeType.MODIFICATION,         # zmiana umowy
    "ConcessionUpdateAgreementNotice": NoticeType.MODIFICATION,
    "ContractPerformingNotice": NoticeType.OTHER,             # wykonanie umowy
    "CircumstancesFulfillmentNotice": NoticeType.OTHER,       # spełnianie okoliczności (art. 214 ust. 1 pkt 11-14)
}
# "TenderPlanNotice" (plan postępowań) is rejected here with HTTP 400 and is read from Board/Search instead.
# "DefenceAndSecurityContractNotice" is a frontend option too, but the notice endpoint rejects it (400) and
# Board/Search returns nothing for it (checked 2026-10-30).
PLAN_TYPE = "TenderPlanNotice"

Row = tuple[str, datetime | None, Notice | None]  # (objectId, publicationDate, notice) per API record

_CPV = re.compile(r"\b(\d{8})-\d\b")
_LABEL = re.compile(r"^\s*\d+(?:\.\d+)*\.?\)\s*(.*)$")
_URL = re.compile(r"(https?://[^\s<>\"']+|www\.[^\s<>\"']+)")
_AMOUNT = re.compile(r"^((?:\d{1,3}(?:[  ]\d{3})+|\d+)(?:[.,]\d+)?)\s*([A-Z]{3})\b")
_VALUE_LABEL = re.compile(r"^Wartość (zamówienia|umowy)", re.I)
_WS = re.compile(r"\s+")


def _dt(v: str | None) -> datetime | None:
    """API datetimes are UTC with a 'Z' and up to 7 fractional digits."""
    if not v:
        return None
    d = isoparse(v)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _api_dt(d: datetime) -> str:
    """The API takes naive datetimes and compares them against UTC publication timestamps."""
    return d.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def _clean(s: str | None) -> str | None:
    s = _WS.sub(" ", s or "").strip()
    return s or None


def html_fields(html: str | None) -> list[tuple[str, str]]:
    """Split a BZP notice body into (numbered label, value) pairs: '3.1.) Adres strony ...' -> 'https://...'.

    Labels are the numbered headings ("2.16.) Tryb udzielenia zamówienia ..."); a value is the text up to
    the next numbered heading (inline `<span>` answers and free text after the heading alike)."""
    if not html:
        return []
    tree = HTMLParser(html)
    for node in tree.css("style, script, head"):
        node.decompose()
    root = tree.body or tree.root
    if root is None:
        return []
    out: list[tuple[str, list[str]]] = []
    for line in root.text(separator="\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = _LABEL.match(line)
        if m:
            out.append((m.group(1).strip(), []))
        elif line.startswith("SEKCJA ") or line.startswith("Biuletyn Zamówień Publicznych"):
            out.append(("", []))  # section heading / footer: ends the previous value
        elif out:
            out[-1][1].append(line)
    return [(label.rstrip(": ").strip(), _clean(" ".join(v)) or "") for label, v in out if label]


def _find(fields: list[tuple[str, str]], pattern: str | re.Pattern[str]) -> str | None:
    rx = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
    for label, value in fields:
        if rx.search(label) and value:
            return value
    return None


def _amount(v: str | None) -> tuple[float | None, str | None]:
    """'223153,87 PLN' / '1 234 567,00 PLN' -> (223153.87, 'PLN')."""
    m = _AMOUNT.match((v or "").replace(" ", " ").strip())
    if not m:
        return None, None
    num = re.sub(r"[  ]", "", m.group(1)).replace(",", ".")
    try:
        return float(num), m.group(2)
    except ValueError:
        return None, None


def _procedure(v: str | None) -> str | None:
    """'Zamówienie udzielane jest w trybie podstawowym na podstawie: art. 275 pkt 1 ustawy'
    -> 'podstawowym na podstawie: art. 275 pkt 1 ustawy'."""
    if not v:
        return None
    m = re.search(r"\bw trybie\s+(.+)", v)
    return _clean((m.group(1) if m else v)[:300])


def _cpvs(v: str | None) -> list[str]:
    return list(dict.fromkeys(_CPV.findall(v or "")))


def _is_waf_block(e: HttpError) -> bool:
    return e.status == 403 and ("WAF" in e.body or "Access Blocked" in e.body or "zablokowany" in e.body)


def _first_error(e: BaseException) -> BaseException:
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return e


def _slices(start: datetime, end: datetime, n: int) -> list[tuple[datetime, datetime | None]]:
    """[start, end) cut into n equal windows; the last one is open-ended (also catches what is published now)."""
    step = (end - start) / n
    bounds = [start + step * i for i in range(n)] + [None]
    return [(bounds[i], bounds[i + 1]) for i in range(n)]  # type: ignore[misc]


class Bzp(Connector):
    name = "bzp"
    title = "BZP — Biuletyn Zamówień Publicznych (e-Zamówienia)"
    countries = ("PL",)
    homepage = "https://ezamowienia.gov.pl/mo-client-board/bzp/list"
    kind = "portal"
    poll_interval = timedelta(hours=1)        # ~800 contract notices/day, published around the clock
    overlap = timedelta(hours=6)
    first_sync_lookback = timedelta(days=3)
    requests_per_second = 4.0                 # official JSON API; parallel pages, WAF-aware back-off below
    supports_refresh = True                   # deadline extensions update `submittingOffersDate` in place

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._sem: asyncio.Semaphore | None = None
        self._serial = asyncio.Lock()   # used once the WAF has blocked us: one request at a time
        self._pause = 0.0               # extra gap per request after a block
        self._cooldown_until = 0.0      # loop time until which nobody sends (shared WAF back-off)

    # ---------------------------------------------------------------- HTTP

    async def _fetch(self, url: str, params: dict[str, Any]) -> bytes:
        if self._sem is None:
            self._sem = asyncio.Semaphore(MAX_IN_FLIGHT)
        loop = asyncio.get_running_loop()
        async with self._sem:
            for attempt in range(WAF_RETRIES + 1):
                if (wait := self._cooldown_until - loop.time()) > 0:
                    await asyncio.sleep(wait)
                try:
                    if self._pause:
                        async with self._serial:
                            await asyncio.sleep(self._pause)
                            r = await self.http.get(url, params=params)
                    else:
                        r = await self.http.get(url, params=params)
                    return r.content
                except HttpError as e:
                    if not _is_waf_block(e) or attempt == WAF_RETRIES:
                        raise
                    if loop.time() >= self._cooldown_until:  # first request to notice this block
                        self._pause = min(30.0, max(WAF_PAUSE, self._pause * 2))
                        self._cooldown_until = loop.time() + WAF_BACKOFF
                        self.log.warning("bzp: blocked by the WAF, pausing %.0fs, then serial with %.0fs gaps",
                                         WAF_BACKOFF, self._pause)
        raise AssertionError("unreachable")

    def _rows(self, content: bytes) -> list[Row]:
        """Decode a page and map it (runs in a worker thread: JSON + HTML parsing is the CPU-heavy part)."""
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError(f"unexpected BZP response: {str(data)[:300]}")
        return [(x.get("objectId") or "", _dt(x.get("publicationDate")),
                 self.to_notice(x) if x.get("noticeNumber") and (x.get("objectId") or x.get("noticeType") == PLAN_TYPE)
                 else None)
                for x in data]

    async def _page(self, url: str, params: dict[str, Any]) -> list[Row]:
        return await asyncio.to_thread(self._rows, await self._fetch(url, params))

    # ---------------------------------------------------------------- sync

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        now = self.ctx.now()
        queue: asyncio.Queue[list[Notice] | Checkpoint | BaseException | None] = asyncio.Queue()

        async def run() -> None:
            try:
                async with asyncio.TaskGroup() as tg:
                    for notice_type in NOTICE_TYPES:
                        tg.create_task(self._sync_type(notice_type, since, now, queue))
                    tg.create_task(self._sync_plans(since, now, queue))
            except Exception as e:  # noqa: BLE001 — handed to the consumer, which re-raises it
                queue.put_nowait(_first_error(e))
            else:
                queue.put_nowait(None)

        producer = asyncio.create_task(run())
        seen: set[str] = set()  # parallel slices may overlap at their edges
        try:
            while (item := await queue.get()) is not None:
                if isinstance(item, BaseException):
                    raise item
                if isinstance(item, Checkpoint):
                    yield item
                    continue
                for n in item:
                    if n.source_id not in seen:
                        seen.add(n.source_id)
                        yield n
            yield Checkpoint({"since": since.isoformat(), "until": now.isoformat()})
        finally:
            producer.cancel()
            with suppress(asyncio.CancelledError):
                await producer

    def _notice_params(self, notice_type: str, start: datetime, end: datetime | None, now: datetime,
                       size: int, after: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "NoticeType": notice_type,
            "PublicationDateFrom": _api_dt(start),
            # bounded slice: the API's To is one day late; open-ended: a day past now under either reading
            "PublicationDateTo": _api_dt(end - TO_SHIFT if end else now + timedelta(days=1)),
            "PageSize": size,
        }
        if after:
            params["SearchAfter"] = after
        return params

    async def _sync_type(self, notice_type: str, since: datetime, now: datetime, queue: asyncio.Queue) -> None:
        probe = await self._page(API, self._notice_params(notice_type, since, None, now, PROBE_SIZE, None))
        queue.put_nowait([n for _, _, n in probe if n])
        if len(probe) >= PROBE_SIZE:
            last_oid, last_pub, _ = probe[-1]
            first_pub = probe[0][1] or since
            last_pub = max(last_pub or now, first_pub)
            # notices per second over the probe -> how many more to expect -> how many parallel slices
            rate = len(probe) / max((last_pub - since).total_seconds(), 600.0)
            expected = rate * max((now - last_pub).total_seconds(), 0.0)
            n = max(1, min(MAX_SLICES, math.ceil(expected / SLICE_TARGET)))
            slices = _slices(last_pub, now, n)
            self.log.debug("bzp: %s probe full, ~%d more expected, %d slices", notice_type, expected, n)
            async with asyncio.TaskGroup() as tg:
                # slice 0 continues the probe's cursor; the others start fresh at their own window
                tg.create_task(self._chain(notice_type, since, slices[0][1], now, last_oid, queue))
                for start, end in slices[1:]:
                    tg.create_task(self._chain(notice_type, start, end, now, None, queue))
        queue.put_nowait(Checkpoint({"since": since.isoformat(), "done_type": notice_type}))

    async def _chain(self, notice_type: str, start: datetime, end: datetime | None, now: datetime,
                     after: str | None, queue: asyncio.Queue) -> None:
        """Walk one time window [start, end) with the SearchAfter cursor."""
        while True:
            rows = await self._page(API, self._notice_params(notice_type, start, end, now, PAGE_SIZE, after))
            queue.put_nowait([n for _, pub, n in rows if n and (end is None or pub is None or pub < end)])
            if len(rows) < PAGE_SIZE or (end and (rows[-1][1] or end) >= end):
                return
            after = rows[-1][0]

    async def _sync_plans(self, since: datetime, now: datetime, queue: asyncio.Queue) -> None:
        """Plan versions, newest first by page number: fetch every page the first one says we need at once."""
        params = {"NoticeType": PLAN_TYPE, "PageSize": PLAN_PAGE_SIZE,
                  "SortingColumnName": "PublicationDate", "SortingDirection": "DESC"}

        async def page(no: int) -> tuple[list[Row], bool]:
            rows = await self._page(PLAN_API, {**params, "PageNumber": no})
            queue.put_nowait([n for _, pub, n in rows if n and (pub is None or pub >= since)])
            # last page: short, or reaches past `since`
            return rows, len(rows) < PLAN_PAGE_SIZE or any(pub is not None and pub < since for _, pub, _ in rows)

        rows, last = await page(1)
        next_page = 2
        while not last and next_page <= MAX_PLAN_PAGES:
            newest, oldest = rows[0][1] or now, rows[-1][1] or now
            span = max((newest - oldest).total_seconds(), 60.0) / PLAN_PAGE_SIZE  # seconds per plan version
            wave = max(1, math.ceil((oldest - since).total_seconds() / span / PLAN_PAGE_SIZE))
            wave = min(wave, MAX_PLAN_PAGES + 1 - next_page, 4 * MAX_IN_FLIGHT)
            results = await asyncio.gather(*(page(p) for p in range(next_page, next_page + wave)))
            next_page += wave
            last = any(done for _, done in results)
            rows = results[-1][0]
        if not last:
            self.log.warning("bzp: stopped tender plans after %d pages", MAX_PLAN_PAGES)
        queue.put_nowait(Checkpoint({"since": since.isoformat(), "done_type": PLAN_TYPE}))

    # ---------------------------------------------------------------- refresh

    async def refresh(self, ref: StoredRef) -> Notice | None:
        notice_type = (ref.raw or {}).get("noticeType") or "ContractNotice"
        if notice_type == PLAN_TYPE:
            return None  # plan versions never change; a changed plan is published as a new "/NN/P" notice
        now = self.ctx.now()
        published = _dt((ref.raw or {}).get("publicationDate")) or now - timedelta(days=365)
        rows = await self._page(API, {
            "NoticeType": notice_type,
            "NoticeNumber": ref.source_id,
            "PublicationDateFrom": _api_dt(published - timedelta(days=1)),
            "PublicationDateTo": _api_dt(now + timedelta(days=1)),
            "PageSize": 10,
        })
        for _, _, n in rows:
            if n and n.source_id == ref.source_id:
                return n
        return None

    def to_notice(self, x: dict[str, Any]) -> Notice:
        kind = x.get("noticeType") or ""
        fields = html_fields(x.get("htmlBody"))
        notice_type = NOTICE_TYPES.get(kind, NoticeType.OTHER)
        deadline = _dt(x.get("submittingOffersDate"))

        status = Status.UNKNOWN
        if notice_type is NoticeType.TENDER and deadline:
            status = Status.OPEN if deadline > self.ctx.now() else Status.CLOSED
        elif kind == "TenderResultNotice":
            results = [r for r in (x.get("procedureResult") or "").split(";") if r]
            if any(r == "zawarcieUmowy" for r in results):
                status = Status.AWARDED
            elif results and all(r == "uniewaznienie" for r in results):
                status, notice_type = Status.CANCELLED, NoticeType.CANCELLATION
        elif notice_type is NoticeType.AWARD:
            status = Status.AWARDED

        platform = _find(fields, r"Adres strony internetowej prowadzonego postępowania")
        platform_url = None
        if platform and (m := _URL.search(platform)):
            platform_url = absolute_url(m.group(1).rstrip(".,;)"))
        value, currency = None, None
        for label, v in fields:
            if _VALUE_LABEL.search(label) and "zmian" not in label.lower():
                value, currency = _amount(v)
                if value is not None:
                    break
        description = _find(fields, r"^Krótki opis przedmiotu") or _find(fields, r"^Krótki opis")
        if kind == "NoticeUpdateNotice":  # "8.1. Termin składania ofert Przed zmianą: ... Po zmianie: ..."
            changes = [v for label, v in fields if label.startswith("Opis zmiany") and v]
            description = " | ".join(changes) or description
        procedure = _procedure(_find(fields, r"^Tryb udzielenia"))

        cpvs = _cpvs(x.get("cpvCode"))
        if not cpvs:
            cpvs = _cpvs(" ".join(v for label, v in fields if "kod CPV" in label))

        refs = {
            "bzp": x.get("bzpNumber"),
            "notice": x.get("noticeNumber"),
            "ocid": x.get("tenderId"),
            "bzp_object_id": x.get("objectId"),
            "bzp_changed": _find(fields, r"^Numer zmienianego ogłoszenia"),  # NoticeUpdateNotice -> original
            "bzp_plan": _find(fields, r"^Numer planu postępowań w BZP"),       # tender -> its plan version
        }
        raw = {k: v for k, v in x.items() if k != "htmlBody" and v is not None}
        if raw.get("contractors"):
            raw["contractors"] = [c for c in raw["contractors"] if any(c.values())]

        buyer = _clean(x.get("organizationName"))
        if kind == PLAN_TYPE:
            notice_type = NoticeType.PLAN
            url = PLAN_URL.format(quote(x["noticeNumber"], safe=""))
            default_title = f"Plan postępowań: {buyer}" if buyer else f"Plan postępowań {x['noticeNumber']}"
        else:
            url = NOTICE_URL.format(x["objectId"])
            default_title = f"{kind} {x['noticeNumber']}"

        return Notice(
            source=self.name,
            source_id=x["noticeNumber"],
            url=url,
            title=_clean(x.get("orderObject")) or default_title,
            notice_type=notice_type,
            status=status,
            description=description[:4000] if description else None,
            buyer_name=buyer,
            buyer_id=_clean(x.get("organizationNationalId")),
            buyer_country=(x.get("organizationCountry") or "PL").upper()[:2],
            buyer_city=_clean(x.get("organizationCity")),
            cpv_codes=cpvs,
            procedure_type=procedure,
            published_at=_dt(x.get("publicationDate")),
            deadline_at=deadline,
            value_amount=value,
            value_currency=currency,
            platform_url=platform_url,
            language="pl",
            refs={k: v for k, v in refs.items() if isinstance(v, str) and v},
            raw=raw,
        )
