"""Logintrade — Polish multi-tenant e-procurement platform (<buyer>.logintrade.net).

There is no API or feed; everything is server-rendered HTML. Two kinds of tenants exist:

* **Shared-platform tenants** (corporates such as Lotos, PGE, Enea, Grupa Azoty; proceeding ids > 1M).
  Their open requests for quotation are aggregated on one platform-wide list,
  `https://platforma.logintrade.net/platforma-zakupowa,zapytania_otwarte.html?page=N` (30 rows/page,
  newest first by publication/"zaktualizowano" date). Rows carry title, publication date, deadline date,
  last-update timestamp, voivodeship and active flag; each links to the tenant's public notice page.
* **Stand-alone tenants** (hospitals, municipalities, universities — the 334 buyers in
  `load_tenants("logintrade")`). Each has its own public lists:
  `https://<tenant>/rejestracja/ustawowe.html` (public procurement under the PZP act) and
  `https://<tenant>/rejestracja/przetargi.html` (proceedings outside the act), 30 rows/page sorted by
  submission deadline (newest deadline first). Rows carry title, case number, created and deadline times.
  These tenants are visited in rotation (`TENANTS_PER_RUN` per sync, cursor in `state`) so each poll stays
  bounded; a per-tenant last-visit time makes the next visit look back far enough.

Everything runs concurrently: the feed walker and up to `TENANT_PARALLEL` tenant walkers share one queue that
`sync` drains, each walker prefetches its next list page while the current page's notice pages are fetched,
and notice pages are fetched in parallel. Throughput is bounded only by the connector's shared rate limit.
Pages are requested with `Accept-Language: pl` — the site serves English labels to English-preferring clients.

For every new or changed row the public notice page (`zapytania_email,<id>,<hash>.html`) is fetched for the
buyer name, NIP, city, exact deadline, procedure type, description and attachments. A small `seen` cache in
`state` (row fingerprint per notice) avoids re-fetching notice pages for rows that did not change. Logintrade
publishes no CPV codes or values on public pages.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from ..connector import Checkpoint, Connector, StoredRef
from ..data import load_tenants
from ..http import HttpError
from ..models import Document, Notice, NoticeType, absolute_url

WARSAW = ZoneInfo("Europe/Warsaw")
FEED = "https://platforma.logintrade.net/platforma-zakupowa,zapytania_otwarte.html"
TENANT_LISTS = (("ustawowe", "/rejestracja/ustawowe.html"), ("przetargi", "/rejestracja/przetargi.html"))
PL_HEADERS = {"Accept-Language": "pl-PL,pl;q=0.9"}  # pages are localised; the parsers expect Polish
TENANTS_PER_RUN = 60
TENANT_PARALLEL = 12        # tenants walked at once (all requests still share the 5 req/s limiter)
DETAIL_PARALLEL = 24        # notice pages in flight at once
MAX_FEED_PAGES = 200
MAX_TENANT_PAGES = 10
FEED_MARGIN = timedelta(days=1)       # feed is sorted by day; read one extra day before stopping
TENANT_OVERLAP = timedelta(days=1)
SEEN_TTL_DAYS = 45
DESCRIPTION_MAX = 4000

PLAN_MARKERS = ("konsultacje rynkowe", "konsultacji rynkowych", "dialog techniczny", "rozeznanie rynku")
CANCEL_MARKERS = ("unieważni",)

_NOTICE_LINK = re.compile(r"https?://([^/]+)/zapytania_email,(\d+),([0-9a-f]+)\.html")
_DT = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?")
_UPDATED = re.compile(r"(?:zaktualizowano|updated):\s*(\d{4}-\d{2}-\d{2})")
_POSTAL = re.compile(r"\b\d{2}-\d{3}\s+(.+)$")
_NIP = re.compile(r"NIP:?\s*([0-9][0-9 -]{8,14}[0-9])")
_PAGE = re.compile(r"[?&]page=(\d+)")
_NUMBER = re.compile(r"(?:Postępowanie nr|ZAPYTANIE OFERTOWE NR)\s*([A-Z]?\d+/\d+)", re.I)
_BZP = re.compile(r"\b(\d{4}/BZP \d{8})")
_TED = re.compile(r"\b(\d{4}/S \d{3}-\d{6})")


# --- small helpers -----------------------------------------------------------------------------

def _clean(s: str | None) -> str | None:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s or None


def _text(node: Node | None) -> str | None:
    return _clean(node.text(separator=" ")) if node is not None else None


def _dt(s: str | None, end_of_day: bool = False) -> datetime | None:
    """'2026-10-23 07:18' / '2026-10-30' -> Warsaw-aware datetime."""
    m = _DT.search(s or "")
    if not m:
        return None
    y, mo, d, hh, mm, ss = m.groups()
    day = date(int(y), int(mo), int(d))
    if hh is not None:
        t = time(int(hh), int(mm), int(ss or 0))
    else:
        t = time(23, 59) if end_of_day else time(0, 0)
    return datetime.combine(day, t, tzinfo=WARSAW)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _host_key(host: str) -> str:
    return host.lower().removesuffix(".logintrade.net")


def parse_notice_link(url: str) -> tuple[str, str] | None:
    """'https://gcm.logintrade.net/zapytania_email,243242,<hash>.html' -> ('gcm.logintrade.net', '243242')."""
    m = _NOTICE_LINK.search(url or "")
    return (m.group(1).lower(), m.group(2)) if m else None


def _max_page(html: HTMLParser) -> int:
    pages = [int(p) for a in html.css(".pagination a, a[href*='page=']")
             for p in _PAGE.findall(a.attributes.get("href") or "")]
    return max(pages, default=1)


# --- list parsers --------------------------------------------------------------------------------

def parse_feed(text: str) -> tuple[list[dict[str, Any]], int]:
    """Platform-wide open-requests list -> (rows, last page number)."""
    html = HTMLParser(text)
    rows: list[dict[str, Any]] = []
    for tr in html.css("table.defaultTable tr"):
        tds = tr.css("td")
        a = tr.css_first("a[href*='zapytania_email']")
        if len(tds) < 7 or a is None:
            continue
        url = a.attributes.get("href") or ""
        if not parse_notice_link(url):
            continue
        published_txt = _text(tds[2]) or ""
        img = tds[6].css_first("img")
        rows.append({
            "url": url,
            "title": _text(a),
            "published": published_txt.split("(")[0].strip() or None,
            "updated_date": (_UPDATED.search(published_txt) or [None, None])[1],
            "deadline": _text(tds[3]),
            "modified": _text(tds[4]),
            "region": _text(tds[5]),
            "active": "nieaktywne" not in ((img.attributes.get("src") or "") if img else ""),
        })
    return rows, _max_page(html)


_HEADERS = {
    "title": ("nazwa", "name of proceeding"),
    "case_number": ("znak sprawy", "signature of the case"),
    "published": ("data założenia", "data publikacji", "establishment date", "publication date"),
    "deadline": ("termin składania", "deadline for making offers"),
    "status": ("status",),
    "buyer": ("zamawiający", "ordering party", "contracting authority"),
}


def parse_tenant_list(text: str) -> tuple[list[dict[str, Any]], int]:
    """A tenant's /rejestracja/{ustawowe,przetargi}.html list -> (rows, last page number)."""
    html = HTMLParser(text)
    table = html.css_first("table.przetargi")
    if table is None:
        return [], 1
    headers: list[str] = []
    for th in table.css("tr th"):
        span = int(th.attributes.get("colspan") or 1)
        headers.extend([(_text(th) or "").lower()] * span)
    cols: dict[str, int] = {}
    for key, needles in _HEADERS.items():
        for i, h in enumerate(headers):
            if any(n in h for n in needles):
                cols.setdefault(key, i)
                break
    rows: list[dict[str, Any]] = []
    for tr in table.css("tr"):
        tds = tr.css("td")
        a = tr.css_first("a[href*='zapytania_email']")
        if a is None or not tds:
            continue
        url = a.attributes.get("href") or ""
        if not parse_notice_link(url):
            continue

        def cell(key: str) -> Node | None:
            i = cols.get(key)
            return tds[i] if i is not None and i < len(tds) else None

        status = cell("status")
        sign = status.css_first(".icon-sign") if status is not None else None
        rows.append({
            "url": url,
            "title": _text(a),
            "case_number": _text(cell("case_number")),
            "published": _text(cell("published")),
            "deadline": _text(cell("deadline")),
            "buyer": _text(cell("buyer")),
            "active": None if sign is None else "inactive" not in (sign.attributes.get("class") or ""),
        })
    return rows, _max_page(html)


# --- notice page parser --------------------------------------------------------------------------

def _address(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        if m := _NIP.search(line):
            out["nip"] = re.sub(r"\D", "", m.group(1))
        elif m := _POSTAL.search(line):
            out["city"] = m.group(1).strip()
        elif line and "street" not in out and "city" not in out:
            out["street"] = line
    return out


def parse_detail(text: str, base_url: str) -> dict[str, Any]:
    """Public notice page (both the current and the legacy layout) -> flat dict of what it states."""
    html = HTMLParser(text)
    d: dict[str, Any] = {}
    body_text = _clean(html.body.text(separator=" ")) if html.body is not None else ""
    if m := _NUMBER.search(body_text or ""):
        d["number"] = m.group(1)

    address = html.css_first("address")
    if address is not None:  # current layout (stand-alone tenants)
        d["layout"] = "current"
        d["buyer"] = _text(address.css_first("h4"))
        d.update(_address([_text(s) or "" for s in address.css("span")]))
        for col in html.css(".col-label-value"):
            label, value = _text(col.css_first(".label")), col.css_first(".value")
            if not label or value is None:
                continue
            key = label.lower()
            if key.startswith(("termin składania", "deadline for making offers")):
                d["deadline"] = _text(value)
            elif key.startswith(("termin otwarcia", "opening date")):
                d["opening"] = _text(value)
            elif key.startswith(("tryb postępowania", "purchasing mode")):
                d["procedure"] = _text(value)
            elif key.startswith(("znak sprawy", "signature of the case")):
                d["case_number"] = _text(value)
            elif key.startswith(("rodzaj zamówienia", "type of order")):
                d["contract_type"] = _text(value)
            elif key.startswith(("progi", "proceeding thresholds")):
                d["threshold"] = _text(value)
            elif key.startswith(("opis przedmiotu", "description of the object")):
                d["description"] = _text(value)
        if (created := html.css_first(".content-block-header .date strong")) is not None:
            d["created"] = _text(created)
        docs = []
        for att in html.css(".attachment"):
            a = att.css_first("a[href]")
            href = absolute_url(a.attributes.get("href"), base_url) if a is not None else None
            if href:
                docs.append({"url": href, "title": _text(att.css_first(".attachment-name"))})
        d["documents"] = docs
    else:  # legacy layout (shared-platform tenants)
        firm = html.css_first("#firma_info")
        if firm is not None:
            d["layout"] = "legacy"
            d["buyer"] = _text(firm.css_first("h2"))
            d.update(_address([_text(h) or "" for h in firm.css("h3")]))
        desc = html.css_first(".niceEdit_text")
        d["description"] = _text(desc)
        vals = {i.attributes.get("name"): (i.attributes.get("value") or "").strip()
                for i in html.css("input[name]")}
        if all(vals.get(k) for k in ("data_rok", "data_miesiac", "data_dzien")):
            hh, mm = vals.get("godz_godz") or "23", vals.get("godz_min") or "59"
            d["deadline"] = f"{vals['data_rok']}-{vals['data_miesiac']}-{vals['data_dzien']} {hh}:{mm}"
        if all(vals.get(k) for k in ("p_data_rok", "p_data_miesiac", "p_data_dzien")):
            hh, mm = vals.get("p_godz_godz") or "00", vals.get("p_godz_min") or "00"
            d["start"] = f"{vals['p_data_rok']}-{vals['p_data_miesiac']}-{vals['p_data_dzien']} {hh}:{mm}"
        if m := re.search(r"Waluta:\s*([A-Z]{3})", body_text or ""):
            d["currency"] = m.group(1)
        d["documents"] = []
    if d.get("description") and len(d["description"]) > DESCRIPTION_MAX:
        d["description"] = d["description"][:DESCRIPTION_MAX].rstrip() + " …"
    refs = {}
    if m := _BZP.search(body_text or ""):
        refs["bzp"] = m.group(1)
    if m := _TED.search(body_text or ""):
        refs["ted"] = m.group(1)
    if refs:
        d["refs"] = refs
    return {k: v for k, v in d.items() if v not in (None, "")}


# --- connector -----------------------------------------------------------------------------------

_DONE = object()


@dataclass
class _Run:
    """Shared state of one sync: producers (feed walker, tenant walkers) push into `queue`, `sync` yields it.

    Invariant: a notice's `seen` entry is written in the same event-loop step that enqueues the notice, and a
    checkpoint snapshots `seen` when it is enqueued — so (FIFO) every fingerprint a checkpoint remembers belongs
    to a notice that was yielded before that checkpoint.
    """

    since: datetime
    today: str
    seen: dict[str, list[str]]
    visited: dict[str, str]
    batch: list[int]
    n_tenants: int
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    inflight: set[str] = field(default_factory=set)
    done: set[int] = field(default_factory=set)
    details: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(DETAIL_PARALLEL))

    def next_offset(self) -> int:
        """First tenant of this run's batch not finished yet (resume point), else the one after the batch."""
        for idx in self.batch:
            if idx not in self.done:
                return idx
        return (self.batch[-1] + 1) % self.n_tenants if self.batch else 0

    def checkpoint(self) -> None:
        cutoff = (datetime.now(WARSAW).date() - timedelta(days=SEEN_TTL_DAYS)).isoformat()
        self.queue.put_nowait(Checkpoint({
            "tenant_offset": self.next_offset(), "visited": dict(self.visited),
            "seen": {k: list(v) for k, v in self.seen.items() if v[1] >= cutoff}}))


class Logintrade(Connector):
    name = "logintrade"
    title = "Logintrade — platforma zakupowa"
    countries = ("PL",)
    homepage = "https://platforma.logintrade.net"
    kind = "platform"
    poll_interval = timedelta(hours=1)
    first_sync_lookback = timedelta(days=3)
    requests_per_second = 5.0
    supports_refresh = True

    def __init__(self, ctx):
        super().__init__(ctx)
        self.tenants = load_tenants("logintrade")
        self.tenant_buyers = {t["tenant"].lower(): t.get("buyer") for t in self.tenants}

    async def _get(self, url: str, page: int = 1) -> str:
        return await self.http.get_text(url, params={"page": page} if page > 1 else None, headers=PL_HEADERS)

    # ---- sync ----

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        n = len(self.tenants)
        offset = int(state.get("tenant_offset") or 0) % n if n else 0
        run = _Run(since=since, today=datetime.now(WARSAW).date().isoformat(),
                   seen=dict(state.get("seen") or {}), visited=dict(state.get("visited") or {}),
                   batch=[(offset + i) % n for i in range(min(TENANTS_PER_RUN, n))], n_tenants=n)
        gate = asyncio.Semaphore(TENANT_PARALLEL)
        # the platform-wide feed and every tenant of this run's batch are walked at the same time
        tasks = [asyncio.create_task(self._feed(run))]
        tasks += [asyncio.create_task(self._tenant_task(run, idx, gate)) for idx in run.batch]

        def finished(t: asyncio.Task) -> None:
            run.queue.put_nowait(t.exception() if not t.cancelled() and t.exception() else _DONE)

        for t in tasks:
            t.add_done_callback(finished)
        try:
            remaining = len(tasks)
            while remaining:
                item = await run.queue.get()
                if item is _DONE:
                    remaining -= 1
                elif isinstance(item, BaseException):
                    raise item
                else:
                    yield item
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _feed(self, run: _Run) -> None:
        """Platform-wide feed of open requests (shared-platform tenants), newest update first."""
        since, stop_day = run.since, (run.since.astimezone(WARSAW) - FEED_MARGIN).date()

        def select(rows: list[dict[str, Any]]) -> tuple[list[tuple], bool]:
            items = []
            for row in rows:
                touched = max(filter(None, [_dt(row.get("modified")), _dt(row.get("updated_date")),
                                            _dt(row.get("published"))]), default=None)
                if touched is None or touched >= since:
                    fp = "|".join(str(row.get(k)) for k in ("modified", "updated_date", "deadline", "title"))
                    items.append((row, "feed", fp))
            days = [(_dt(r.get("updated_date")) or _dt(r.get("published"))) for r in rows]
            return items, not all(d is not None and d.date() < stop_day for d in days)

        await self._walk(run, FEED, parse_feed, select, MAX_FEED_PAGES, checkpoint=True)

    async def _tenant_task(self, run: _Run, idx: int, gate: asyncio.Semaphore) -> None:
        host = self.tenants[idx]["tenant"].lower()
        async with gate:
            started = datetime.now(WARSAW)
            cutoff = run.since
            if last_visit := run.visited.get(host):
                cutoff = min(cutoff, datetime.fromisoformat(last_visit) - TENANT_OVERLAP)
            results = await asyncio.gather(*(self._tenant_list(run, host, kind, path, cutoff)
                                             for kind, path in TENANT_LISTS), return_exceptions=True)
        for (kind, _), res in zip(TENANT_LISTS, results, strict=True):
            if isinstance(res, (HttpError, httpx.HTTPError)):
                self.log.info("logintrade tenant %s %s list skipped: %s", host, kind, str(res)[:120])
            elif isinstance(res, BaseException):
                raise res
        run.visited[host] = started.isoformat()
        run.done.add(idx)
        run.checkpoint()

    async def _tenant_list(self, run: _Run, host: str, kind: str, path: str, cutoff: datetime) -> None:
        """A stand-alone tenant's list, sorted by submission deadline (latest first)."""

        def select(rows: list[dict[str, Any]]) -> tuple[list[tuple], bool]:
            items = []
            for row in rows:
                deadline, created = _dt(row.get("deadline"), end_of_day=True), _dt(row.get("published"))
                if (deadline and deadline >= cutoff) or (created and created >= cutoff):
                    fp = "|".join(str(row.get(k)) for k in ("published", "deadline", "title", "case_number"))
                    items.append((row, kind, fp))
            last = _dt(rows[-1].get("deadline"), end_of_day=True) if rows else None
            return items, last is None or last >= cutoff

        await self._walk(run, f"https://{host}{path}", parse_tenant_list, select, MAX_TENANT_PAGES)

    async def _walk(self, run: _Run, url: str, parse, select, max_pages: int, checkpoint: bool = False) -> None:
        """Page through a list; the next page is fetched while the current page's notice pages are."""
        page, nxt = 1, asyncio.create_task(self._get(url))
        try:
            while nxt is not None:
                rows, last = parse(await nxt)
                nxt = None
                items, more = select(rows)
                if rows and more and page < min(last, max_pages):
                    nxt = asyncio.create_task(self._get(url, page + 1))
                await asyncio.gather(*(self._emit(run, *item) for item in items))
                if checkpoint:
                    run.checkpoint()
                page += 1
        finally:
            if nxt is not None:
                nxt.cancel()
                await asyncio.gather(nxt, return_exceptions=True)

    async def _emit(self, run: _Run, row: dict[str, Any], kind: str, fp: str) -> None:
        host, nid = parse_notice_link(row["url"])  # type: ignore[misc]
        sid = f"{_host_key(host)}:{nid}"
        if (prev := run.seen.get(sid)) and prev[0] == fp:
            run.seen[sid] = [fp, run.today]
            return
        if sid in run.inflight:
            return
        run.inflight.add(sid)
        try:
            async with run.details:
                detail = await self._detail(row["url"])
            notice = self.to_notice(row, kind, detail)
            run.seen[sid] = [fp, run.today]   # same step as the enqueue: see _Run
            run.queue.put_nowait(notice)
        finally:
            run.inflight.discard(sid)

    async def _detail(self, url: str) -> dict[str, Any] | None:
        try:
            return parse_detail(await self._get(url), url)
        except (HttpError, httpx.HTTPError) as e:
            self.log.info("logintrade notice page %s unavailable: %s", url, str(e)[:120])
            return None

    # ---- refresh ----

    async def refresh(self, ref: StoredRef) -> Notice | None:
        row, kind = ref.raw.get("list"), ref.raw.get("kind")
        if not row or not kind:
            return None
        try:
            detail = parse_detail(await self._get(ref.url), ref.url)
        except HttpError as e:
            if e.status in (404, 410):
                return None
            raise
        return self.to_notice(row, kind, detail)

    # ---- mapping ----

    def to_notice(self, row: dict[str, Any], kind: str, detail: dict[str, Any] | None) -> Notice:
        host, nid = parse_notice_link(row["url"])  # type: ignore[misc]
        d = detail or {}
        title = row.get("title") or d.get("case_number") or f"Logintrade {nid}"
        procedure = d.get("procedure") or {"feed": "Zapytanie ofertowe",
                                           "przetargi": "Postępowanie z wyłączeniem ustawy Pzp"}.get(kind)
        low = f"{title} {procedure or ''}".lower()
        if any(m in low for m in PLAN_MARKERS):
            notice_type = NoticeType.PLAN
        elif any(m in low for m in CANCEL_MARKERS):
            notice_type = NoticeType.CANCELLATION
        else:
            notice_type = NoticeType.TENDER
        published = _dt(row.get("published")) or _dt(d.get("created"))
        deadline = _dt(d.get("deadline")) or _dt(row.get("deadline"), end_of_day=True)
        case_number = d.get("case_number") or row.get("case_number")
        refs = {"procedure": case_number} if case_number else {}
        if d.get("number"):
            refs["logintrade"] = d["number"]
        refs.update(d.get("refs") or {})
        buyer = d.get("buyer") or row.get("buyer") or self.tenant_buyers.get(host)
        return Notice(
            source=self.name,
            source_id=f"{_host_key(host)}:{nid}",
            url=row["url"],
            title=title,
            notice_type=notice_type,
            description=d.get("description"),
            buyer_name=buyer,
            buyer_id=d.get("nip"),
            buyer_country="PL",
            buyer_city=d.get("city"),
            procedure_type=procedure,
            published_at=published,
            deadline_at=deadline,
            value_currency=d.get("currency"),
            platform_url=f"https://{host}/",
            language="pl",
            documents=[Document(x["url"], title=x.get("title"), kind="attachment") for x in d.get("documents") or []],
            refs=refs,
            raw={"kind": kind, "tenant": host, "list": row,
                 "detail": {k: v for k, v in d.items() if k not in ("description", "documents")}},
        )
