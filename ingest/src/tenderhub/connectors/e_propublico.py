"""e-ProPublico — Polish e-procurement platform (e-propublico.pl, run by Datacomp).

Hosts mostly below-/national-threshold proceedings of ~200 Polish buyers (municipalities, hospitals,
universities). There is no API or feed; the public part is a server-rendered ASP.NET MVC site:

* `GET /Ogloszenia?page=N` — "Aktualne ogłoszenia": every proceeding currently open for tenders,
  10 rows per page, sorted by tender deadline (not by date). Row: sygnatura, title + link to
  `/Ogloszenia/Details/<guid>`, buyer, deadline, status. "Strona N z M" gives the page count; a page past
  the end has an empty table.
* `GET /Ogloszenia/Details/<guid>` — the proceeding card: buyer address, procedure type, order type,
  tender deadline, opening date, publication and last-modification timestamps, status.
* `GET /Ogloszenia/DokumentyZamowienia?przetargId=<guid>` — documents grouped in sections
  (Ogłoszenie / Specyfikacja / Załączniki do specyfikacji / Inne dokumenty) with publication dates, and
  after the opening the "kwota przeznaczona na realizację zamówienia" (budget) card.

Pages are localised from Accept-Language, so every request asks for Polish explicitly.

The list carries no publication date and cannot be sorted or filtered by one, so `sync` walks the whole
list (~30 pages) and reads each card to learn its publication/modification time. To stay polite on
hourly polls, the checkpoint remembers for every listed proceeding a fingerprint of its list row and its
last known change time: a proceeding whose row is unchanged and whose last change is older than `since`
is skipped without fetching its card. Documents are only fetched for proceedings that are yielded.
For speed, list pages 2..N are fetched concurrently once page 1 gives the page count, and cards/documents
of up to CONCURRENCY proceedings are in flight at once; the rate limiter still spaces every request.
CPV codes and BZP numbers are not published on the platform's HTML pages (only inside the PDF notices).

TLS: the site's certificate chains to Let's Encrypt's new "Root YR" (via intermediate YR2), which is not
yet in certifi's bundle, and the server does not send the cross-signed root. The shared client therefore
fails with CERTIFICATE_VERIFY_FAILED. The connector uses its own client whose trust store is certifi plus
Let's Encrypt's published cross-certificate "Root YR signed by ISRG Root X1" (so trust is still anchored in
X1), wrapped in the core `Http` and sharing the core rate limiter.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import ssl
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import certifi
import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from ..connector import Checkpoint, Connector, Context, StoredRef
from ..http import Http, HttpError
from ..models import Document, Notice, NoticeType, Status, absolute_url

# https://letsencrypt.org/certs/gen-y/root-yr-by-x1.pem
# sha256 07:26:39:D0:B1:40:D5:BF:FA:E1:6A:D9:C3:F6:CC:60:86:04:06:21:F5:1E:E6:1A:6D:46:A8:91:5C:07:CF:76
ROOT_YR_BY_X1 = """-----BEGIN CERTIFICATE-----
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
-----END CERTIFICATE-----
"""


def tls_context() -> ssl.SSLContext:
    """certifi roots + the Let's Encrypt Root YR cross-certificate (anchored in ISRG Root X1)."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cadata=ROOT_YR_BY_X1)
    return ctx


def _own_client(shared: httpx.AsyncClient) -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=True, timeout=shared.timeout, headers=dict(shared.headers),
                             verify=tls_context())


def _own_http(shared: Http) -> Http:
    """Core `Http` (retries, request counter) over a client that can verify the site's chain, sharing the
    runner's rate limiter so the connector's politeness settings still apply."""
    http = Http(_own_client(shared.client), max_retries=shared.max_retries, log=shared.log)
    http.limiter = shared.limiter
    return http

BASE = "https://e-propublico.pl"
LIST = f"{BASE}/Ogloszenia"
DETAILS = f"{BASE}/Ogloszenia/Details/{{id}}"
DOCS = f"{BASE}/Ogloszenia/DokumentyZamowienia"
HEADERS = {"Accept-Language": "pl-PL,pl;q=0.9"}
WARSAW = ZoneInfo("Europe/Warsaw")
MAX_PAGES = 300  # safety net; the list has ~32 pages
CONCURRENCY = 8  # proceedings processed at once (requests are still spaced by the rate limiter)
CHECKPOINT_EVERY = 25

_GUID = re.compile(r"/Ogloszenia/Details/([0-9a-fA-F-]{36})")
_PAGES = re.compile(r"Strona\s+(\d+)\s+z\s+(\d+)")
_ISO_DT = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")
_PL_DT = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})(?:\D+(\d{2}):(\d{2})(?::(\d{2}))?)?")
_POSTCODE = re.compile(r"^\d{2}-\d{3}\s+(.+)$")
_BZP = re.compile(r"\d{4}/BZP\s*\d{8}")
_AMOUNT = re.compile(r"\d[\d\s .]*(?:,\d+)?")

SECTION_KINDS = {
    "ogłoszenie": "notice",
    "specyfikacja": "specification",
    "załączniki do specyfikacji": "annex",
}


def _clean(s: str | None) -> str | None:
    s = re.sub(r"\s+", " ", s or "").strip().strip(",").strip()
    return s or None


def _text(node: Node | None) -> str | None:
    return _clean(node.text(deep=True)) if node is not None else None


def _dt(v: str | None) -> datetime | None:
    """'2026-11-02 09:00:00' / '02.11.2026 09:00:00' / '2026-12-01' (Warsaw local time) -> aware datetime.
    A bare date means the end of that day."""
    v = v or ""
    m = _ISO_DT.search(v)
    if m:
        y, mo, d, h, mi, s = m.groups()
    else:
        m = _PL_DT.search(v)
        if not m:
            return None
        d, mo, y, h, mi, s = m.groups()
    if h is None:
        return datetime(int(y), int(mo), int(d), 23, 59, tzinfo=WARSAW)
    return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), tzinfo=WARSAW)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _from_iso(v: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


def _amount(v: str | None) -> float | None:
    """'16000,00 zł brutto' / '1 234 567,89 zł' -> 16000.0."""
    m = _AMOUNT.search(v or "")
    if not m:
        return None
    s = re.sub(r"[\s .]", "", m.group(0)).replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _status(s: str | None) -> Status:
    s = (s or "").lower()
    if "składanie" in s:
        return Status.OPEN
    if "unieważn" in s:
        return Status.CANCELLED
    if s:
        return Status.CLOSED  # otwarcie / ocena ofert / postępowanie zakończone
    return Status.UNKNOWN


def _doc_kind(section: str | None, name: str | None) -> str:
    sec = (section or "").lower()
    if sec in SECTION_KINDS:
        return SECTION_KINDS[sec]
    n = (name or "").lower()
    if re.search(r"zmian|modyfikac|sprostowan|przedłuż", n):
        return "amendment"
    if re.search(r"odpowied|wyjaśnie|pytani|zapytan", n):
        return "q&a"
    if "załącznik" in n:
        return "annex"
    if re.search(r"siwz|swz|specyfikac", n):
        return "specification"
    if "ogłoszeni" in n:
        return "notice"
    if re.search(r"otwarci|wyb[oó]r|wynik|uniewa", n):
        return "result"
    return "other"


# --- page parsers (pure, unit-tested against recorded pages) ---------------------------------------


def parse_list(html: str) -> tuple[list[dict[str, Any]], int | None]:
    """List page -> (rows, total pages)."""
    tree = HTMLParser(html)
    rows = []
    for tr in tree.css("table.table tbody tr"):
        tds = tr.css("td")
        link = tr.css_first("a[href]")
        m = _GUID.search(link.attributes.get("href") or "") if link else None
        if len(tds) < 5 or not m:
            continue
        deadline = " ".join(tds[3].text(separator=" ").split())
        rows.append({
            "id": m.group(1).lower(),
            "signature": _text(tds[0]),
            "title": _text(link),
            "buyer": _text(tds[2]),
            "deadline": deadline or None,
            "status": _text(tds[4]),
        })
    pages = _PAGES.search(tree.css_first("span.pageCounter").text() if tree.css_first("span.pageCounter") else "")
    return rows, (int(pages.group(2)) if pages else None)


def _label_value(label: Node) -> str | None:
    """Value next to a `<label for=...>`: either the sibling `div.col` of its `div.col-3`, or the text
    after the label inside the same div ("Data publikacji: 2026-10-19 14:54:23")."""
    node = label.parent
    while node is not None and node.tag != "div":
        node = node.parent
    if node is None:
        return None
    if "col-3" in (node.attributes.get("class") or "").split():
        sib = node.next
        while sib is not None and sib.tag != "div":
            sib = sib.next
        return _text(sib)
    full = node.text(deep=True)
    rest = full.split(label.text(deep=True), 1)[-1]
    return _clean(rest.lstrip(" : "))


def parse_details(html: str) -> dict[str, Any]:
    tree = HTMLParser(html)
    fields: dict[str, str | None] = {}
    for label in tree.css("label[for]"):
        key = label.attributes.get("for")
        if key and key not in fields:
            fields[key] = _label_value(label)
    out: dict[str, Any] = {
        "title": fields.get("TematZamowienia"),
        "signature": fields.get("Sygnatura"),
        "procedure": fields.get("TrybPostepowania"),
        "order_type": fields.get("RodzajZamowienia"),
        "deadline": fields.get("TerminSkladania"),
        "opening": fields.get("TerminOtwarcia"),
        "binding_until": fields.get("TerminZwiazaniaOferta"),
        "published": fields.get("DataPublikacji"),
        "modified": fields.get("DataModyfikacji"),
    }
    alert = tree.css_first("div.alert")
    if alert is not None:
        out["status"] = _clean((alert.text(deep=True) or "").split(":", 1)[-1])
    for card in tree.css("div.card"):
        header = card.css_first(".card-header")
        if header is None or "zamawiający" not in (header.text() or "").lower():
            continue
        body = card.css_first(".card-body")
        out["buyer"] = _text(card.css_first(".card-title"))
        children = [c for c in body.iter() if c.tag == "div"] if body is not None else []
        lines = [t for t in (_text(d) for d in children) if t]
        out["buyer_address"] = [ln for ln in lines if not ln.startswith(("Tel.", "Faks", "E-mail", "Adres strony"))]
        for ln in lines:
            if (m := _POSTCODE.match(ln)) and "buyer_city" not in out:
                out["buyer_city"] = m.group(1)
            elif ln.startswith("Adres strony"):
                out["buyer_website"] = _clean(ln.split(":", 1)[-1])
        break
    if not out.get("title"):
        h5 = tree.css_first("h5.mb-3")
        out["title"] = _text(h5)
    return {k: v for k, v in out.items() if v}


def parse_docs(html: str) -> dict[str, Any]:
    tree = HTMLParser(html)
    docs: list[dict[str, Any]] = []
    for header in tree.css("h5.section-header"):
        section = _text(header)
        container = header.parent
        if container is None:
            continue
        for tr in container.css("tbody tr"):
            a = tr.css_first("a[href]")
            if a is None:
                continue
            url = absolute_url(a.attributes.get("href"), BASE + "/")
            if not url:
                continue
            tds = [_text(td) for td in tr.css("td")]
            name = _text(a)
            docs.append({
                "url": url,
                "title": name,
                "section": section,
                "kind": _doc_kind(section, name),
                "type": tds[2] if len(tds) > 2 else None,
                "date": tds[4] if len(tds) > 4 else None,
            })
    out: dict[str, Any] = {"documents": docs}
    for card in tree.css("div.card"):
        header = card.css_first(".card-header")
        if header is None or "kwoc" not in (header.text() or "").lower():
            continue
        bodies = card.css(".card-body")
        if bodies:
            text = _text(bodies[-1])
            out["budget"] = text
            out["budget_amount"] = _amount(text)
            out["budget_currency"] = "PLN" if text and ("zł" in text or "PLN" in text) else None
    return out


def _fingerprint(row: dict[str, Any]) -> str:
    key = "|".join(str(row.get(k) or "") for k in ("signature", "title", "buyer", "deadline", "status"))
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _changed_at(details: dict[str, Any]) -> datetime | None:
    stamps = [d for d in (_dt(details.get("published")), _dt(details.get("modified"))) if d]
    return max(stamps) if stamps else None


class EPropublico(Connector):
    name = "e_propublico"
    title = "e-ProPublico"
    countries = ("PL",)
    homepage = "https://e-propublico.pl"
    kind = "platform"
    poll_interval = timedelta(hours=2)
    first_sync_lookback = timedelta(days=7)
    requests_per_second = 4.0
    supports_refresh = True

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.http = _own_http(ctx.http)

    async def _get(self, url: str, **kw: Any) -> str:
        if self.http.client.is_closed:  # closed at the end of the previous sync on this instance
            self.http.client = _own_client(self.ctx.http.client)
        return await self.http.get_text(url, headers=HEADERS, **kw)

    async def _docs(self, pid: str) -> dict[str, Any]:
        return parse_docs(await self._get(DOCS, params={"przetargId": pid}))

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        try:
            async for item in self._sync(since, state):
                yield item
        finally:
            await self.http.client.aclose()

    async def _rows(self) -> list[dict[str, Any]]:
        """All list rows: page 1 tells the page count, the rest are fetched concurrently (the shared
        rate limiter still spaces the requests). De-duplicated, in list order."""
        rows, total = parse_list(await self._get(LIST, params={"page": 1}))
        if rows and total and total > 1:
            pages = await asyncio.gather(*(self._get(LIST, params={"page": p})
                                           for p in range(2, min(total, MAX_PAGES) + 1)))
            for html in pages:
                rows.extend(parse_list(html)[0])
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            out.setdefault(row["id"], row)  # a row can shift onto the next page while paging
        return list(out.values())

    async def _process(self, row: dict[str, Any], since: datetime,
                       sem: asyncio.Semaphore) -> tuple[str, dict[str, Any], Notice | None]:
        """Card (and, if changed since `since`, documents) of one listed proceeding."""
        pid = row["id"]
        async with sem:
            details = parse_details(await self._get(DETAILS.format(id=pid)))
            changed = _changed_at(details)
            entry = {"fp": _fingerprint(row), "changed": _iso(changed)}
            if changed is not None and changed < since:
                return pid, entry, None
            return pid, entry, self.to_notice(pid, details, await self._docs(pid), row)

    async def _sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        cache: dict[str, dict[str, Any]] = dict(state.get("rows") or {})
        seen: dict[str, dict[str, Any]] = {}
        todo: list[dict[str, Any]] = []
        for row in await self._rows():
            cached = cache.get(row["id"])
            if cached and cached.get("fp") == _fingerprint(row) and (c := _from_iso(cached.get("changed"))) and c < since:
                seen[row["id"]] = cached  # unchanged list row and nothing new on the card since `since`
            else:
                todo.append(row)
        sem = asyncio.Semaphore(CONCURRENCY)
        tasks = [asyncio.create_task(self._process(row, since, sem)) for row in todo]
        try:
            for i, fut in enumerate(asyncio.as_completed(tasks), 1):
                pid, entry, notice = await fut
                seen[pid] = entry
                if notice is not None:
                    yield notice
                if i % CHECKPOINT_EVERY == 0:
                    yield Checkpoint({"rows": {**cache, **seen}})
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        # only proceedings still on the list are worth remembering
        yield Checkpoint({"rows": seen})

    async def refresh(self, ref: StoredRef) -> Notice | None:
        try:
            details = parse_details(await self._get(DETAILS.format(id=ref.source_id)))
        except HttpError as e:
            if e.status in (404, 410):
                return None
            raise
        if not details.get("title"):
            return None
        return self.to_notice(ref.source_id, details, await self._docs(ref.source_id), (ref.raw or {}).get("row"))

    def to_notice(self, pid: str, details: dict[str, Any], docs: dict[str, Any],
                  row: dict[str, Any] | None = None) -> Notice:
        row = row or {}
        signature = details.get("signature") or row.get("signature")
        documents = [Document(d["url"], title=d.get("title"), kind=d.get("kind")) for d in docs.get("documents", [])]
        refs = {"procedure": signature} if signature else {}
        for d in docs.get("documents", []):
            if m := _BZP.search(d.get("title") or ""):
                refs.setdefault("bzp", re.sub(r"BZP\s*", "BZP ", m.group(0)))
        return Notice(
            source=self.name,
            source_id=pid,
            url=DETAILS.format(id=pid),
            title=details.get("title") or row.get("title") or signature or pid,
            notice_type=NoticeType.TENDER,
            status=_status(details.get("status") or row.get("status")),
            buyer_name=details.get("buyer") or row.get("buyer"),
            buyer_country="PL",
            buyer_city=details.get("buyer_city"),
            procedure_type=details.get("procedure"),
            published_at=_dt(details.get("published")),
            deadline_at=_dt(details.get("deadline") or row.get("deadline")),
            value_amount=docs.get("budget_amount"),
            value_currency=docs.get("budget_currency") if docs.get("budget_amount") is not None else None,
            language="pl",
            documents=documents,
            refs=refs,
            raw={
                "row": row or None,
                "details": details,
                "budget": docs.get("budget"),
                "documents": [{k: d.get(k) for k in ("title", "section", "type", "date")}
                              for d in docs.get("documents", [])],
            },
        )
