"""SmartPZP — Polish e-procurement platform (portal.smartpzp.pl).

Hosts proceedings for many buyers (municipalities, hospitals, transport companies and, historically,
military units / regional logistics bases such as 12 WOG and 1 RBLog), each under its own tenant
slug (`/usk`, `/12wog`, ...). Activity has largely stopped: as of 2026-10-30 the newest proceeding
started on 29-06-2026 (the military tenants' newest date from 2024; smartpzp.pl itself redirects to
marketplanet.pl and CPK announces its move to eZamawiający), so short incremental windows are usually
empty. There is no JSON API: the portal is a JavaServer Faces / PrimeFaces 6
application, so this connector drives the same stateful AJAX calls the browser makes:

1. `GET /` — opens a JSF view (session cookie + `javax.faces.ViewState`).
2. `POST /` partial/ajax "Wyszukaj" with `Data wszczęcia od = since` — the server-side search filter
   on the start date; the response holds the first 10 rows and the total `rowCount`.
3. `POST /` partial/ajax pagination (`_first`, `_rows`) for the remaining rows.
4. Rows carry no proceeding id (`data-rk` is a per-session hash), so for each row the connector
   presses "Podgląd" (preview) with that row selected; the server answers with an AJAX redirect to the
   stable public card `/<tenant>/public/postepowanie?postepowanie=<id>`.
5. `GET` that card (plain HTML, no session needed) for CPV, deadline, value, currency, description and
   the document list.

Speed: the server answers slowly (~1-2 s per request) and serves one request at a time per session
(a concurrent request in the same session gets a 302/500 "another tab" answer). So the work is spread
over independent cookie sessions: the matching rows are split into ranges handled by up to `JSF_LANES`
JSF views (each opens the view, repeats the search, pages to its range and previews its rows in order,
since row selection is view state), and every card GET runs on one of `CARD_LANES` card sessions as
soon as its id is known. Results are still yielded in list order with a checkpoint per range. The
shared rate limiter caps the total request rate.

The card itself is GET-able by id, so `refresh` re-reads it; the list row (buyer, status) is refreshed by
searching for the proceeding number.
"""

from __future__ import annotations

import asyncio
import itertools
import re
from collections import deque
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..connector import Checkpoint, Connector, StoredRef
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status, normalize_cpv

BASE = "https://portal.smartpzp.pl/"
WARSAW = ZoneInfo("Europe/Warsaw")
PAGE_SIZE = 50
CARD_LANES = 4  # concurrent card GETs, each on its own session
JSF_LANES = 3  # concurrent JSF views (search + page + previews), each on its own session
MIN_RANGE = 3  # an extra JSF view costs 2 requests, so do not split finer than this
WINDOW = 2 * JSF_LANES  # row ranges in flight ahead of the one being yielded

SEARCH_FORM = "wyszukPostepowanForm"
LIST_FORM = "listaPostepowanForm"
TABLE = "listaPostepowanForm:postepowaniaTabela"
PREVIEW = "listaPostepowanForm:podgladPublicId"
AJAX_HEADERS = {"Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest"}

# list columns on the portal-wide list (tenant pages omit the last two)
COLUMNS = ("number", "title", "procedure", "deadline", "started", "requests_deadline", "status", "entity", "buyer")

PLAN_PROCEDURES = {"Wstępne konsultacje rynkowe"}

_VIEWSTATE = re.compile(r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"')
_ROWCOUNT = re.compile(r"rowCount:(\d+)")
_REDIRECT = re.compile(r'<redirect url="([^"]+)"')
_DATETIME = re.compile(r"(\d{2})-(\d{2})-(\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?")
_BZP = re.compile(r"\d{4}/BZP \d{8}")
_ROW = re.compile(r"<tr data-ri=\"(\d+)\"[^>]*data-rk=\"([^\"]+)\".*?</tr>", re.S)


def _clean(s: str | None) -> str | None:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if s and s != "-" else None


def _dt(v: str | None) -> datetime | None:
    """'31-07-2026 14:30:00' / '30-04-2026 10:27' / '30-04-2026' (Warsaw local time) -> aware datetime."""
    m = _DATETIME.search(v or "")
    if not m:
        return None
    d, mo, y, h, mi, s = m.groups()
    if h is None:
        return datetime(int(y), int(mo), int(d), 23, 59, tzinfo=WARSAW)
    return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), tzinfo=WARSAW)


def _amount(v: str | None) -> float | None:
    """'25 900 000 zł brutto' / '1 234,56 zł' -> 25900000.0."""
    m = re.search(r"\d[\d\s .]*(?:,\d+)?", v or "")
    if not m:
        return None
    num = re.sub(r"[\s ]", "", m.group(0))
    if "," in num:
        num = num.replace(".", "").replace(",", ".")
    try:
        return float(num)
    except ValueError:
        return None


def _status(s: str | None) -> Status:
    s = (s or "").lower()
    if "unieważni" in s or "anulowan" in s:
        return Status.CANCELLED
    if "po wyborze" in s or "rozstrzygni" in s or "udzielon" in s:
        return Status.AWARDED
    if "zamknię" in s or "zakończ" in s or "oceny" in s or "otwarci" in s:
        return Status.CLOSED
    return Status.UNKNOWN  # "Ogłoszone", "W trakcie": the runner derives open/closed from the deadline


def parse_rows(fragment: str) -> list[dict[str, Any]]:
    """Rows of the proceedings table (full page, search response or pagination response)."""
    rows = []
    for m in _ROW.finditer(fragment):
        tree = HTMLParser(f"<table>{m.group(0)}</table>")
        cells = [_clean(td.text()) for td in tree.css("td")]
        row: dict[str, Any] = {k: v for k, v in zip(COLUMNS, cells, strict=False)}
        row["ri"], row["rk"] = int(m.group(1)), m.group(2)
        rows.append(row)
    return rows


def parse_card(html: str) -> dict[str, Any]:
    """Public proceeding card -> {label: [values]}, plus value amount and the document list."""
    tree = HTMLParser(html)
    fields: dict[str, list[str]] = {}
    label: str | None = None
    for node in tree.css("span.pzp-outputtext-header, span.pzp-outputtext-content"):
        cls = node.attributes.get("class") or ""
        text = _clean(node.text())
        if "pzp-outputtext-header" in cls:
            label = text
            if label:
                fields.setdefault(label, [])
        elif label and text:
            fields[label].append(text)
    criteria = {}
    for c in tree.css("div.pzp-form-criteria"):
        t, v = c.css_first("div.criteria-title"), c.css_first("div.criteria-value")
        if t and v and _clean(t.text()):
            criteria[_clean(t.text()).rstrip(":").strip()] = _clean(v.text())
    docs = []
    for tr in tree.css("tbody[id$='listaDokumentowTabela_data'] tr[data-rk]"):
        tds = tr.css("td")
        if len(tds) >= 3:
            docs.append({"id": tr.attributes.get("data-rk"), "name": _clean(tds[1].text()),
                         "published": _clean(tds[2].text())})
    return {"fields": fields, "criteria": criteria, "documents": docs}


def _first(fields: dict[str, list[str]], *labels: str) -> str | None:
    for label in labels:
        for k, v in fields.items():
            if k.lower().startswith(label.lower()) and v:
                return v[0]
    return None


def card_url(path: str) -> tuple[str, str, str] | None:
    """'/pkagdynia/public/postepowanie?postepowanie=84547665' -> (absolute url, tenant, id)."""
    p = urlparse(path)
    pid = (parse_qs(p.query).get("postepowanie") or [None])[0]
    tenant = p.path.strip("/").split("/")[0]
    if not pid or not tenant:
        return None
    return f"{BASE}{tenant}/public/postepowanie?postepowanie={pid}", tenant, pid


class _Session:
    """One server-side session (JSESSIONID + load-balancer SERVERID), kept outside the shared cookie jar.

    The portal allows one request at a time per session (a concurrent request in the same session gets a
    302 / 500 "another tab" response), so every concurrent lane has its own session. Cookies are sent as
    an explicit header, which keeps the shared client's jar out of it.
    """

    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}
        self.view_state = ""

    def headers(self) -> dict[str, str]:
        # a non-empty Cookie header also stops httpx from adding cookies from the shared jar
        return {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()) or "tenderhub=1"}

    def absorb(self, r: httpx.Response) -> None:
        for resp in (*r.history, r):
            for h in resp.headers.get_list("set-cookie"):
                name, _, rest = h.partition("=")
                if name.strip() in ("JSESSIONID", "SERVERID"):
                    self.cookies[name.strip()] = rest.split(";", 1)[0].strip()


class SmartPzp(Connector):
    name = "smartpzp"
    title = "SmartPZP"
    countries = ("PL",)
    homepage = "https://portal.smartpzp.pl"
    kind = "platform"
    poll_interval = timedelta(hours=3)
    first_sync_lookback = timedelta(days=7)
    requests_per_second = 3.0
    supports_refresh = True

    # --- HTTP sessions ------------------------------------------------------------------------------

    async def _get(self, s: _Session, url: str, **kw: Any) -> httpx.Response:
        r = await self.http.get(url, headers=s.headers(), **kw)
        s.absorb(r)
        return r

    async def _post(self, s: _Session, url: str, **kw: Any) -> httpx.Response:
        r = await self.http.post(url, headers={**s.headers(), **AJAX_HEADERS}, **kw)
        s.absorb(r)
        return r

    async def _fetch_card(self, lanes: asyncio.Queue[_Session], url: str) -> str | None:
        """Card HTML, fetched on a free lane (own server session); None if the id redirects away (gone)."""
        s = await lanes.get()
        try:
            r = await self._get(s, url, follow_redirects=False)
        finally:
            lanes.put_nowait(s)
        if r.is_redirect:
            return None
        return r.text

    @staticmethod
    def _lanes(n: int = CARD_LANES) -> asyncio.Queue[_Session]:
        q: asyncio.Queue[_Session] = asyncio.Queue()
        for _ in range(n):
            q.put_nowait(_Session())
        return q

    # --- JSF plumbing -------------------------------------------------------------------------------

    async def _open_view(self) -> _Session:
        s = _Session()
        html = (await self._get(s, BASE)).text
        m = _VIEWSTATE.search(html)
        if not m:
            raise RuntimeError("SmartPZP: no javax.faces.ViewState on the start page")
        s.view_state = m.group(1)
        return s

    async def _ajax(self, s: _Session, data: dict[str, str]) -> str:
        r = await self._post(s, BASE, data={"javax.faces.partial.ajax": "true", **data,
                                            "javax.faces.ViewState": s.view_state})
        text = r.text
        if "<partial-response" not in text or "<error>" in text:
            raise RuntimeError(f"SmartPZP: unexpected JSF response: {text[:300]}")
        return text

    async def _search(self, s: _Session, *, started_from: str = "", number: str = "") -> tuple[str, int]:
        f = SEARCH_FORM
        text = await self._ajax(s, {
            "javax.faces.source": f"{f}:wyszukaj", "javax.faces.partial.execute": f,
            "javax.faces.partial.render": TABLE, f"{f}:wyszukaj": f"{f}:wyszukaj", f: f,
            f"{f}:dataWszczeciaOd_input": started_from, f"{f}:dataWszczeciaDo_input": "",
            f"{f}:numerPostepowania": number, f"{f}:nazwaPodmiotu": "", f"{f}:fd234_input": "",
            f"{f}:fd235_input": "", f"{f}:slowaKluczowe": "",
            # "Tylko niezakończone" (fc236) left unchecked: return finished proceedings too
        })
        m = _ROWCOUNT.search(text)
        return text, int(m.group(1)) if m else 0

    async def _page(self, s: _Session, first: int, rows: int = PAGE_SIZE) -> str:
        return await self._ajax(s, {
            "javax.faces.source": TABLE, "javax.faces.partial.execute": TABLE, "javax.faces.partial.render": TABLE,
            f"{TABLE}_pagination": "true", f"{TABLE}_first": str(first), f"{TABLE}_rows": str(rows),
            f"{TABLE}_skipChildren": "true", f"{TABLE}_encodeFeature": "true", LIST_FORM: LIST_FORM,
        })

    async def _preview(self, s: _Session, row: dict[str, Any]) -> tuple[str, str, str] | None:
        """Select the row and press "Podgląd" -> (card url, tenant, id) from the AJAX redirect."""
        text = await self._ajax(s, {
            "javax.faces.source": PREVIEW, "javax.faces.partial.execute": "@all", PREVIEW: PREVIEW,
            LIST_FORM: LIST_FORM, f"{TABLE}_selection": row["rk"],
        })
        m = _REDIRECT.search(text)
        target = card_url(m.group(1).replace("&amp;", "&")) if m else None
        if not target:
            self.log.warning("no card redirect for row %s", row.get("number"))
        return target

    # --- sync / refresh -----------------------------------------------------------------------------

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        started_from = since.astimezone(WARSAW).strftime("%d-%m-%Y")
        s0 = await self._open_view()
        text, total = await self._search(s0, started_from=started_from)
        first_rows = parse_rows(text) if total else []
        if not first_rows:
            yield Checkpoint({"started_from": started_from, "done": 0, "total": total})
            return

        # Split [0, total) into ranges so every JSF view gets work; the first range is served from the rows
        # the search already returned when it fits, the others are paged to on their own view.
        size = min(PAGE_SIZE, max(MIN_RANGE, -(-total // JSF_LANES)))
        ranges = [(f, min(size, total - f)) for f in range(0, total, size)]

        views: asyncio.Queue[_Session | None] = asyncio.Queue()
        views.put_nowait(s0)
        for _ in range(min(JSF_LANES, len(ranges)) - 1):
            views.put_nowait(None)  # opened (view + same search) on first use
        cards = self._lanes()

        async def chunk(first: int, rows_n: int) -> list[tuple[str, str, dict[str, Any], asyncio.Task[str | None]]]:
            s = await views.get()
            try:
                if s is None:
                    s = await self._open_view()
                    await self._search(s, started_from=started_from)
                if first == 0 and rows_n <= len(first_rows) and s is s0:
                    rows = first_rows[:rows_n]
                else:
                    rows = parse_rows(await self._page(s, first, rows_n))
                rows = [r for r in rows if first <= r["ri"] < first + rows_n]
                out = []
                for row in rows:
                    target = await self._preview(s, row)
                    if target:  # card GET starts on a free card lane while the next preview runs
                        url, tenant, pid = target
                        out.append((tenant, pid, row, asyncio.create_task(self._fetch_card(cards, url))))
                return out
            finally:
                views.put_nowait(s)

        pending: deque[tuple[int, int, asyncio.Task[Any]]] = deque()
        todo = iter(ranges)
        try:
            for first, rows_n in itertools.islice(todo, WINDOW):
                pending.append((first, rows_n, asyncio.create_task(chunk(first, rows_n))))
            while pending:
                first, rows_n, job = pending.popleft()
                got = await job
                try:
                    for tenant, pid, row, task in got:
                        html = await task
                        if html is None:
                            self.log.warning("card %s/%s redirected away, skipped", tenant, pid)
                            continue
                        yield self.to_notice(pid, tenant, row, parse_card(html))
                finally:
                    for *_, task in got:
                        task.cancel()
                done = min(total, first + rows_n)
                yield Checkpoint({"started_from": started_from, "done": done, "total": total})
                for nxt in itertools.islice(todo, 1):
                    pending.append((*nxt, asyncio.create_task(chunk(*nxt))))
        finally:
            for *_, job in pending:
                if job.done() and not job.cancelled() and job.exception() is None:
                    for *_, task in job.result():
                        task.cancel()
                job.cancel()

    async def refresh(self, ref: StoredRef) -> Notice | None:
        target = card_url(ref.url)
        if not target:
            return None
        url, tenant, pid = target
        row = {k: v for k, v in (ref.raw.get("row") or {}).items()}
        # card (own session) and a fresh JSF view for the list row, concurrently
        view = asyncio.create_task(self._open_view())
        try:
            try:
                html = await self._fetch_card(self._lanes(1), url)
            except HttpError as e:
                if e.status == 404:
                    return None
                raise
            card = parse_card(html or "")
            number = _first(card["fields"], "Numer Postępowania")
            if not number:  # unknown id: redirects to the tenant's proceedings list
                return None
            title = _first(card["fields"], "Nazwa Postępowania")
            s = await view
            text, _ = await self._search(s, number=number)  # pick up the current status/buyer
        finally:
            if not view.done():
                view.cancel()
        hits = [r for r in parse_rows(text) if r.get("number") == number
                and (not title or r.get("title") == title)
                and (not row.get("buyer") or r.get("buyer") in (None, row.get("buyer")))]
        if len(hits) == 1:
            row = {**row, **{k: v for k, v in hits[0].items() if k not in ("ri", "rk")}}
        return self.to_notice(pid, tenant, row, card)

    # --- mapping ------------------------------------------------------------------------------------

    def to_notice(self, pid: str, tenant: str, row: dict[str, Any], card: dict[str, Any]) -> Notice:
        f = card.get("fields") or {}
        url = f"{BASE}{tenant}/public/postepowanie?postepowanie={pid}"
        number = _first(f, "Numer Postępowania") or row.get("number")
        title = _first(f, "Nazwa Postępowania") or row.get("title") or number or pid
        procedure = _first(f, "Tryb Postępowania") or row.get("procedure")
        buyer = row.get("buyer")
        entity = row.get("entity")
        if entity and (not buyer or (len(buyer) <= 6 and buyer.isupper())):
            buyer = entity  # "CPK" -> "Centralny Port Komunikacyjny Sp. z o.o."
        cpvs = list(dict.fromkeys(c for c in (normalize_cpv(v) for v in f.get("Kod CPV", [])) if c))
        deadline = (_dt(_first(f, "Termin składania ofert")) or _dt(row.get("deadline"))
                    or _dt(_first(f, "Termin składania wniosków")) or _dt(row.get("requests_deadline")))
        value = next((_amount(v) for k, v in (card.get("criteria") or {}).items()
                      if k.lower().startswith("kwota jaką") and _amount(v) is not None), None)
        currency = _first(f, "Waluta postępowania") or ("PLN" if value is not None else None)
        docs_tab = f"{url}#postepowanieTabs:dp"
        documents = [Document(docs_tab, title=d["name"], kind="document")
                     for d in card.get("documents") or [] if d.get("name")]
        bzp = _BZP.search(_first(f, "Numer ogłoszenia") or "")
        clean_row = {k: v for k, v in row.items() if k not in ("ri", "rk") and v is not None}
        # first value per label only: unlabelled contact spans (names, e-mails) trail some fields
        fields = {k: (v if k == "Kod CPV" else v[0]) for k, v in f.items() if v}
        raw = {"row": clean_row, "tenant": tenant, "id": pid, "fields": fields,
               "criteria": card.get("criteria") or {}, "documents": card.get("documents") or []}
        return Notice(
            source=self.name,
            source_id=pid,
            url=url,
            title=title,
            notice_type=NoticeType.PLAN if procedure in PLAN_PROCEDURES else NoticeType.TENDER,
            status=_status(row.get("status")),
            description=_first(f, "Skrócony opis przedmiotu zamówienia"),
            buyer_name=buyer,
            buyer_country="PL",
            cpv_codes=cpvs,
            procedure_type=procedure,
            published_at=_dt(row.get("started")),
            deadline_at=deadline,
            value_amount=value,
            value_currency=currency.upper() if currency else None,
            platform_url=url,
            language="pl",
            documents=documents,
            refs={k: v for k, v in {"procedure": number, "bzp": bzp.group(0) if bzp else None,
                                    "smartpzp_tenant": tenant}.items() if v},
            raw=raw,
        )

