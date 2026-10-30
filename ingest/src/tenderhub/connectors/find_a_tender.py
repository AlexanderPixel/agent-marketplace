"""Find a Tender Service (FTS) — the UK's above-threshold notice service, incl. MOD / DE&S, as OCDS.

API docs: https://www.find-tender.service.gov.uk/Developer/Documentation — anonymous, no key needed.
`GET /api/1.0/ocdsReleasePackages?updatedFrom=<UTC>[&updatedTo=<UTC>]&limit=<=100` returns OCDS releases
newest-first; each release is one published notice (id "103262-2026"). Paging is cursor based via
`links.next` (absent on the last page); the cursor URL carries updatedFrom/updatedTo/limit itself.

Speed. Responses take ~0.35 s, so the only real limit is the API's burst limit: ~12 requests per ~2 minutes
(measured: the 12th request within 73 s got 429 "Rate limit of 12 exceeded. Please retry after 120
seconds." + `Retry-After: 120`). Instead of pacing every request ~11 s apart, we
  * spend a sliding-window budget of BURST requests per BURST_WINDOW at full speed, and persist the
    request timestamps in the checkpoint so the next run (another process) keeps respecting the window;
  * split the `since..now` window into up to MAX_SLICES time slices (updatedFrom/updatedTo) and walk their
    cursor chains concurrently, merging pages as they arrive.
A typical hourly incremental sync (~1 day of notices, 5-8 pages) now finishes in a couple of seconds instead
of ~80 s; larger backfills run at the API's sustained rate. A 429 still waits as long as the API asks.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse

from ..connector import Checkpoint, Connector
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status, absolute_url, normalize_cpv

BASE = "https://www.find-tender.service.gov.uk"
API = f"{BASE}/api/1.0/ocdsReleasePackages"
NOTICE_URL = BASE + "/Notice/{}"
PAGE_SIZE = 100            # API maximum ("'limit' must not exceed the maximum value" above it)
LONDON = ZoneInfo("Europe/London")
RATE_LIMIT_RETRIES = 4
BURST = 10                 # requests per BURST_WINDOW we allow ourselves (API: 12 per 120 s)
BURST_WINDOW = 125.0       # seconds
MAX_SLICES = 4             # concurrent time slices; each costs at most one extra (partial) page
MIN_SLICE = timedelta(hours=12)
_RETRY_AFTER = re.compile(r"retry after (\d+) seconds", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s<>\"')]+|www\.[^\s<>\"')]+", re.IGNORECASE)


def _wall() -> float:
    """Wall-clock seconds (not monotonic: the budget history is persisted across processes)."""
    return time.time()


class _Budget:
    """Sliding window: at most `n` requests in any `window` seconds, shared by all concurrent slices.

    No lock needed: the check and the append happen without an await in between (single-threaded loop).
    """

    def __init__(self, n: int, window: float, history: Iterable[Any] = ()):
        self.n, self.window = n, window
        self.times: deque[float] = deque(sorted(float(t) for t in history if isinstance(t, (int, float))))

    def _prune(self, now: float) -> None:
        while self.times and self.times[0] <= now - self.window:
            self.times.popleft()

    async def acquire(self) -> None:
        while True:
            now = _wall()
            self._prune(now)
            if len(self.times) < self.n:
                self.times.append(now)
                return
            await asyncio.sleep(self.times[0] + self.window - now + 0.05)

    def block_for(self, seconds: float) -> None:
        """After a 429: nobody may send before now + seconds."""
        until = _wall() + seconds - self.window
        self.times = deque([until] * self.n)

    def recent(self) -> list[float]:
        self._prune(_wall())
        return [round(t, 3) for t in self.times]


def _fmt(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _slices(since: datetime, now: datetime) -> list[tuple[datetime, datetime | None]]:
    """Split since..now into contiguous windows, newest first; the newest is open-ended (no updatedTo).
    Boundaries overlap by one second so nothing on a boundary is lost (duplicates are harmless)."""
    span = max(now - since, timedelta(0))
    n = max(1, min(MAX_SLICES, math.ceil(span / MIN_SLICE)))
    step = span / n
    bounds = [since + step * i for i in range(n)]
    out: list[tuple[datetime, datetime | None]] = []
    for i, lo in enumerate(bounds):
        hi = bounds[i + 1] + timedelta(seconds=1) if i + 1 < n else None
        out.append((lo, hi))
    return out[::-1]


# OCDS release tags, most specific first: the first tag found decides the notice type.
TAG_TYPES = [
    ("tenderCancellation", NoticeType.CANCELLATION),
    ("awardCancellation", NoticeType.CANCELLATION),
    ("contractTermination", NoticeType.CANCELLATION),
    ("tenderAmendment", NoticeType.MODIFICATION),
    ("tenderUpdate", NoticeType.MODIFICATION),
    ("awardUpdate", NoticeType.MODIFICATION),
    ("contractUpdate", NoticeType.MODIFICATION),
    ("contractAmendment", NoticeType.MODIFICATION),
    ("award", NoticeType.AWARD),
    ("contract", NoticeType.AWARD),
    ("tender", NoticeType.TENDER),
    ("planning", NoticeType.PLAN),
    ("planningUpdate", NoticeType.PLAN),
]
CANCELLED = {"cancelled", "withdrawn", "unsuccessful", "terminated"}
COUNTRY_NAMES = {"united kingdom": "GB", "uk": "GB", "great britain": "GB", "ireland": "IE"}


def _dt(v: Any) -> datetime | None:
    """OCDS dates: '2026-10-30T14:11:39Z' / '...+01:00'; naive times are UK local."""
    if not v or not isinstance(v, str):
        return None
    try:
        d = isoparse(v)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=LONDON)


def _unique(xs: list[Any]) -> list[Any]:
    return list(dict.fromkeys(x for x in xs if x))


def _cpvs(r: dict[str, Any]) -> list[str]:
    t = r.get("tender") or {}
    classes: list[dict[str, Any]] = []
    if isinstance(t.get("classification"), dict):
        classes.append(t["classification"])
    items = list(t.get("items") or [])
    for a in r.get("awards") or []:
        items += a.get("items") or []
    for it in items:
        if isinstance(it.get("classification"), dict):
            classes.append(it["classification"])
        classes += it.get("additionalClassifications") or []
    return _unique([normalize_cpv(str(c.get("id") or "")) for c in classes
                    if str(c.get("scheme") or "").upper() == "CPV"])


def _notice_type(tags: list[str]) -> NoticeType:
    for tag, kind in TAG_TYPES:
        if tag in tags:
            return kind
    return NoticeType.OTHER


def _status(kind: NoticeType, tender: dict[str, Any]) -> Status:
    st = (tender.get("status") or "").lower()
    if kind is NoticeType.CANCELLATION or st in CANCELLED:
        return Status.CANCELLED
    if kind is NoticeType.AWARD or st == "complete":
        return Status.AWARDED
    if kind in (NoticeType.TENDER, NoticeType.MODIFICATION) and st == "active":
        return Status.OPEN
    return Status.UNKNOWN


def _buyer(r: dict[str, Any]) -> dict[str, Any]:
    parties = r.get("parties") or []
    bid = (r.get("buyer") or {}).get("id")
    for p in parties:
        if bid and p.get("id") == bid:
            return p
    for p in parties:
        if "buyer" in (p.get("roles") or []):
            return p
    return dict(r.get("buyer") or {})


def _country(address: dict[str, Any]) -> str:
    code = (address.get("country") or "").strip().upper()
    if re.fullmatch(r"[A-Z]{2}", code):
        return "GB" if code == "UK" else code
    return COUNTRY_NAMES.get((address.get("countryName") or "").strip().lower(), "GB")


def _value(r: dict[str, Any]) -> tuple[float | None, str | None]:
    t = r.get("tender") or {}
    v = t.get("value") or {}
    if isinstance(v.get("amount"), (int, float)):
        return float(v["amount"]), v.get("currency")
    for group in (t.get("lots"), r.get("contracts"), r.get("awards")):
        vals = [x.get("value") for x in group or [] if isinstance((x.get("value") or {}).get("amount"), (int, float))]
        currencies = {x.get("currency") for x in vals}
        if vals and len(currencies) == 1:
            return float(sum(x["amount"] for x in vals)), currencies.pop()
    return None, None


def _deadline(t: dict[str, Any]) -> datetime | None:
    d = _dt((t.get("tenderPeriod") or {}).get("endDate")) or _dt(t.get("expressionOfInterestDeadline"))
    if d:
        return d
    lot_ends = [_dt((lot.get("tenderPeriod") or {}).get("endDate")) for lot in t.get("lots") or []]
    lot_ends = [x for x in lot_ends if x]
    return min(lot_ends) if lot_ends else None


def _documents(r: dict[str, Any], self_url: str) -> list[Document]:
    sections = [(r.get("tender") or {}).get("documents"), (r.get("planning") or {}).get("documents")]
    sections += [x.get("documents") for x in (r.get("awards") or []) + (r.get("contracts") or [])]
    docs: dict[str, Document] = {}
    for section in sections:
        for d in section or []:
            u = absolute_url(d.get("url"))
            if u and u != self_url and u not in docs:
                docs[u] = Document(u, title=d.get("title") or d.get("description"), kind=d.get("documentType"))
    return list(docs.values())


def _platform_url(t: dict[str, Any]) -> str | None:
    comm = t.get("communication") or {}
    for u in (comm.get("atypicalToolUrl"), comm.get("documentsUrl")):
        if u := absolute_url(u):
            return u
    m = _URL.search(t.get("submissionMethodDetails") or "")
    return absolute_url(m.group(0).rstrip(".,;:")) if m else None


class FindATender(Connector):
    name = "find_a_tender"
    title = "Find a Tender Service (UK)"
    countries = ("GB",)
    homepage = BASE
    kind = "portal"
    poll_interval = timedelta(hours=1)
    first_sync_lookback = timedelta(days=3)
    requests_per_second = 5.0       # only spaces concurrent slices; the real limit is the _Budget burst window

    async def _get_page(self, budget: _Budget, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            await budget.acquire()
            try:
                return await self.http.get_json(url, params=params)
            except HttpError as e:
                if e.status != 429 or attempt == RATE_LIMIT_RETRIES:
                    raise
                m = _RETRY_AFTER.search(e.body or "")
                delay = float(m.group(1)) + 2 if m else 120.0
                self.log.warning("find_a_tender rate limited, waiting %.0fs", delay)
                budget.block_for(delay)   # concurrent slices pause too instead of piling on more 429s
        raise AssertionError("unreachable")

    async def _walk(self, budget: _Budget, lo: datetime, hi: datetime | None,
                    out: asyncio.Queue[tuple[str, Any]]) -> None:
        """Follow one slice's cursor chain, putting ("page", releases) on `out`, then ("done", None)."""
        try:
            url: str | None = API
            params: dict[str, Any] | None = {"updatedFrom": _fmt(lo), "limit": PAGE_SIZE}
            if hi is not None:
                params["updatedTo"] = _fmt(hi)
            while url:
                data = await self._get_page(budget, url, params)
                releases = data.get("releases") or []
                await out.put(("page", releases))
                nxt = (data.get("links") or {}).get("next")
                if not releases or not nxt or nxt == url:
                    break
                url, params = nxt, None
            await out.put(("done", None))
        except Exception as e:  # noqa: BLE001 — handed to the consumer, which re-raises it
            await out.put(("error", e))

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        budget = _Budget(BURST, BURST_WINDOW, state.get("recent_requests") or [])
        slices = _slices(since, self.ctx.now())
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        tasks = [asyncio.create_task(self._walk(budget, lo, hi, queue)) for lo, hi in slices]
        pending, pages = len(tasks), 0

        def checkpoint() -> Checkpoint:
            return Checkpoint({"updated_from": _fmt(since), "slices": len(slices), "pages": pages,
                               "recent_requests": budget.recent()})

        try:
            while pending:
                kind, val = await queue.get()
                if kind == "error":
                    raise val
                if kind == "done":
                    pending -= 1
                    continue
                pages += 1
                for r in val:
                    if r.get("id"):
                        yield self.to_notice(r)
                yield checkpoint()   # persists the request history; the high-water mark guards coverage
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        yield checkpoint()

    def to_notice(self, r: dict[str, Any]) -> Notice:
        rid = str(r["id"])
        t = r.get("tender") or {}
        url = NOTICE_URL.format(rid)
        kind = _notice_type(list(r.get("tag") or []))
        buyer = _buyer(r)
        address = buyer.get("address") or {}
        amount, currency = _value(r)
        tender_ref = t.get("id")
        return Notice(
            source=self.name,
            source_id=rid,
            url=url,
            title=(t.get("title") or r.get("title") or rid).strip(),
            notice_type=kind,
            status=_status(kind, t),
            description=t.get("description") or r.get("description"),
            buyer_name=buyer.get("name") or (r.get("buyer") or {}).get("name"),
            buyer_id=buyer.get("id") or (r.get("buyer") or {}).get("id"),
            buyer_country=_country(address),
            buyer_city=address.get("locality"),
            cpv_codes=_cpvs(r),
            procedure_type=t.get("procurementMethodDetails") or t.get("procurementMethod"),
            legal_basis=(t.get("legalBasis") or {}).get("id"),
            published_at=_dt(r.get("date")),
            deadline_at=None if kind is NoticeType.PLAN else _deadline(t),
            value_amount=amount,
            value_currency=currency,
            platform_url=_platform_url(t),
            language=(r.get("language") or "en")[:2].lower(),
            documents=_documents(r, url),
            refs={k: v for k, v in {"ocid": r.get("ocid"), "notice": rid,
                                    "procedure": tender_ref if tender_ref != r.get("ocid") else None}.items() if v},
            raw=r,
        )
