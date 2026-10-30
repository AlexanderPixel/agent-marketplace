"""CVP IS — Lithuania's central public procurement information system (viesiejipirkimai.lt).

Every Lithuanian contracting authority publishes here, including the defence buyers (KAM's Defence
Resources Agency, the Lithuanian Armed Forces units, CPVA for defence, ...). The site is a European
Dynamics "EPPS" install with no public API or feed (the result export needs a CAPTCHA), so this connector
reads the public HTML that anonymous visitors see:

* list — `/epps/quickSearchAction.do?searchType=cftFTS` is the public "all procedures" search
  (calls for tenders, market consultations, dynamic purchasing systems). It is a displaytag table that
  takes `T01_ps` (page size), `d-3680175-p` (page), `d-3680175-s=datePublished` + `d-3680175-o=1`
  (sort by publication date, descending). Rows give id, title, buyer, short description, publication
  date, deadline, procedure, status, notice PDF, award date and estimated value.
* detail — `/epps/cft/prepareViewCfTWS.do`, `/epps/pmc/viewPmc.do`, `/epps/dps/prepareViewCfTDPSWS.do`
  (`?resourceId=<id>`) are `<dt>/<dd>` lists with CPV codes, directive, TED notice links, NUTS, value.
* buyer — `/epps/prepareViewCAOrganisation.do?id=<n>` gives the buyer's city (no registration code).

Dates in the list carry an EET/EEST suffix; detail dates are naive Europe/Vilnius times.
The list date is the first publication of the procedure, so later amendments are picked up by `refresh`.
Detail pages and buyer cities are cached in the checkpoint state so hourly syncs only fetch new rows;
uncached ones are fetched concurrently per list page (and the next list page is prefetched meanwhile),
with the shared rate limiter still spacing requests.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from ..connector import Checkpoint, Connector, StoredRef
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status, absolute_url, normalize_cpv

BASE = "https://viesiejipirkimai.lt"
LIST_URL = f"{BASE}/epps/quickSearchAction.do"
ORG_URL = f"{BASE}/epps/prepareViewCAOrganisation.do"
PAGE_SIZE = 50
TABLE = "d-3680175"  # displaytag id of the search result table (T01)
VILNIUS = ZoneInfo("Europe/Vilnius")
MAX_PAGES = 200
ORG_CACHE_MAX = 5000
CONCURRENCY = 6  # in-flight requests; throughput is still capped by requests_per_second

STATUS = {
    "pasiūlymų teikimas": Status.OPEN,                    # submission of tenders
    "sukurtas": Status.OPEN,                              # DPS created (open for new participants)
    "pasiūlymų teikimas baigtas": Status.CLOSED,          # submission finished
    "laukiama susipažinimo su pasiūlymais": Status.CLOSED,  # waiting for opening of tenders
    "vertinimas": Status.CLOSED,                          # evaluation
    "uždarytas": Status.CLOSED,
    "nustatytas laimėtojas": Status.AWARDED,              # winner determined
    "atlikta": Status.AWARDED,                            # completed
    "atšauktas": Status.CANCELLED,
    "nutrauktas": Status.CANCELLED,
}

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_TZ = {"EET": timezone(timedelta(hours=2)), "EEST": timezone(timedelta(hours=3)), "UTC": UTC, "GMT": UTC}
_JAVA_DATE = re.compile(r"\w{3} (\w{3}) (\d{1,2}) (\d{2}):(\d{2}):(\d{2}) (\w+) (\d{4})")
_LT_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?")
_TED = re.compile(r"TED:NOTICE:(\d+-\d{4})")
_DIRECTIVE = re.compile(r"(\d{4})/(\d+)/(?:ES|EB|EU|EC|EEB)")


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def java_date(s: str | None) -> datetime | None:
    """'Fri Oct 30 16:14:16 EET 2026' (java.util.Date.toString) -> aware datetime."""
    m = _JAVA_DATE.search(s or "")
    if not m or m.group(1) not in _MONTHS:
        return None
    mon, day, hh, mm, ss, tz, year = m.groups()
    dt = datetime(int(year), _MONTHS[mon], int(day), int(hh), int(mm), int(ss))
    return dt.replace(tzinfo=_TZ.get(tz, VILNIUS))


def lt_date(s: str | None) -> datetime | None:
    """'16/11/2026 10:00[:ss]' (Europe/Vilnius) or a java.util.Date string -> aware datetime.

    A bare date means end of that day. The list table switches between both formats depending on the
    session's locale cookie, so every list date goes through here."""
    m = _LT_DATE.search(s or "")
    if not m:
        return java_date(s)
    d, mo, y, hh, mm, ss = m.groups()
    if hh is None:
        return datetime(int(y), int(mo), int(d), 23, 59, tzinfo=VILNIUS)
    return datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0), tzinfo=VILNIUS)


def parse_amount(s: str | None) -> float | None:
    """'87603.31', '87,603.31', '1145504,13.', '3870000.', '0.0' -> float (None for empty/zero).

    The decimal separator depends on the session locale: when both '.' and ',' occur the last one is
    decimal; a lone ',' is decimal unless it groups thousands ('3,870,000' / '423,500')."""
    s = re.sub(r"[\s\u00a0]", "", s or "").rstrip(".")
    if not s:
        return None
    if "," in s and "." in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        s = s.replace("," if dec == "." else ".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def legal_basis(directive: str | None) -> str | None:
    """'2009/81/EB (Gynyba)' -> '32009L0081' (CELEX id, as TED uses)."""
    m = _DIRECTIVE.search(directive or "")
    return f"3{m.group(1)}L{int(m.group(2)):04d}" if m else None


def _resource_id(href: str | None) -> str | None:
    q = parse_qs(urlparse(href or "").query)
    return (q.get("resourceId") or q.get("id") or [None])[0]


def strip_pv(name: str | None) -> str | None:
    """Buyer names carry a ' (PV)' marker (pirkimo vykdytojas); drop it."""
    name = re.sub(r"\s*\(PV\)\s*$", "", _clean(name))
    return name or None


# --- list page --------------------------------------------------------------------------------

def parse_list(html: str) -> tuple[list[dict[str, Any]], bool]:
    """Search result table -> (rows, has_next_page)."""
    tree = HTMLParser(html)
    rows: list[dict[str, Any]] = []
    table = tree.css_first("table#T01")
    for tr in table.css("tr") if table else []:
        tds = tr.css("td")
        if len(tds) < 9:
            continue
        a = tds[1].css_first("a")
        href = a.attributes.get("href") if a else None
        rid = _clean(tds[2].text()) or _resource_id(href)
        if not rid:
            continue
        info = tds[4].css_first("img")
        pdf = tds[9].css_first("a") if len(tds) > 9 else None
        rows.append({
            "id": rid,
            "title": _clean((a or tds[1]).text()),
            "href": absolute_url(href, BASE + "/epps/") if href else None,
            "buyer": _clean(tds[3].text()),
            "description": _clean(info.attributes.get("title")) if info else "",
            "published": _clean(tds[5].text()),
            "deadline": _clean(tds[6].text()),
            "procedure": _clean(tds[7].text()),
            "status": _clean(tds[8].text()),
            "pdf": absolute_url(pdf.attributes.get("href"), BASE) if pdf else None,
            "award_date": _clean(tds[10].text()) if len(tds) > 10 else "",
            "value": _clean(tds[11].text()) if len(tds) > 11 else "",
        })
    nxt = tree.css_first("#nextNav")
    has_next = bool(nxt and "disabled" not in nxt.attributes and nxt.attributes.get("href"))
    return rows, has_next


# --- detail page ------------------------------------------------------------------------------

def _label(node: Node) -> str:
    return _clean(node.text()).rstrip(":").strip()


def parse_detail(html: str) -> dict[str, Any]:
    """`<dt>label:</dt><dd>value</dd>` pairs -> the fields we map."""
    tree = HTMLParser(html)
    pairs: dict[str, Node] = {}
    for dt in tree.css("dt"):
        dd = dt.next
        while dd is not None and dd.tag != "dd":
            if dd.tag == "dt":
                dd = None
                break
            dd = dd.next
        if dd is not None:
            pairs.setdefault(_label(dt), dd)

    def text(*labels: str) -> str:
        for lab in labels:
            if lab in pairs:
                v = _clean(pairs[lab].text())
                if v:
                    return v
        return ""

    buyer_a = pairs["Pirkimo vykdytojo pavadinimas"].css_first("a") if "Pirkimo vykdytojo pavadinimas" in pairs else None
    cpv_node = pairs.get("BVPŽ kodai")
    cpv = list(dict.fromkeys(
        c for c in (normalize_cpv(x) for x in re.findall(r"\d{8}(?:-\d)?", cpv_node.text() if cpv_node else "")) if c))
    ted_node = pairs.get("TED nuorodos į paskelbtus pranešimus")
    ted = sorted(set(_TED.findall((ted_node.html or "") if ted_node else "")),
                 key=lambda x: (int(x.split("-")[1]), int(x.split("-")[0])))  # oldest -> newest
    d = {
        "title": text("Pavadinimas"),
        "buyer": text("Pirkimo vykdytojo pavadinimas"),
        "buyer_org": _resource_id(buyer_a.attributes.get("href")) if buyer_a else None,
        "description": text("Aprašymas")[:2000],
        "object_type": text("Pirkimo objekto tipas"),
        "directive": text("Direktyva"),
        "procedure": text("Pirkimo būdas"),
        "status": text("Statusas"),
        "cpv": cpv,
        "nuts": text("NUTS kodai"),
        "value": text("Numatoma vertė (EUR)", "Suteikta vertė/maksimali DPS vertė"),
        "threshold": text("Virš arba žemiau tarptautinio pirkimo vertės ribos"),
        "deadline": text("Pasiūlymų arba paraiškų dalyvauti pirkime pateikimo terminas", "Pasiūlymų pateikimo terminas"),
        "published": text("Paskelbimo ir (arba) kvietimo data"),
        "award_date": text("Laimėtojo nustatymo data"),
        "reference": text("Pirkimų suvestinės nuoroda"),
        "language": text("Skelbimo kalba"),
        "ted": ted,
    }
    return {k: v for k, v in d.items() if v}


def parse_org_city(html: str) -> str | None:
    tree = HTMLParser(html)
    for dt in tree.css("dt"):
        if _label(dt) == "Miestas":
            dd = dt.next
            while dd is not None and dd.tag != "dd":
                dd = dd.next
            return _clean(dd.text()) or None if dd is not None else None
    return None


async def _gather_limited(coros: list[Any], limit: int = CONCURRENCY) -> list[Any]:
    """asyncio.gather with at most `limit` coroutines in flight, results in input order."""
    sem = asyncio.Semaphore(limit)

    async def one(c: Any) -> Any:
        async with sem:
            return await c

    return list(await asyncio.gather(*(one(c) for c in coros)))


def _fingerprint(row: dict[str, Any]) -> str:
    """Change marker for a list row; dates are normalised so a locale switch does not look like a change."""
    def norm(k: str) -> str:
        v = row.get(k) or ""
        dt = lt_date(v) if k in ("deadline", "published", "award_date") else None
        return dt.astimezone(UTC).isoformat() if dt else v
    return "|".join(norm(k) for k in ("title", "status", "deadline", "published", "award_date", "value"))


def _kind(url: str) -> str:
    if "/pmc/" in url:
        return "pmc"
    if "/dps/" in url:
        return "dps"
    return "cft"


class CvpLt(Connector):
    name = "cvp_lt"
    title = "CVP IS — Lithuanian central public procurement portal (viesiejipirkimai.lt)"
    countries = ("LT",)
    homepage = "https://viesiejipirkimai.lt"
    kind = "portal"
    poll_interval = timedelta(hours=1)
    first_sync_lookback = timedelta(days=3)
    requests_per_second = 2.0        # scraped government site (~0.3 s responses): stay polite
    supports_refresh = True

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        old_details: dict[str, Any] = dict(state.get("details") or {})
        orgs: dict[str, Any] = dict(state.get("orgs") or {})
        details: dict[str, Any] = {}
        page = 1
        pending: asyncio.Task[str] | None = asyncio.ensure_future(self._list_page(page))
        try:
            while pending is not None:
                rows, has_next = parse_list(await pending)
                pending = None
                keep: list[dict[str, Any]] = []
                reached_end = False
                for row in rows:
                    published = lt_date(row["published"])
                    if published is not None and published < since:
                        reached_end = True
                        break
                    keep.append(row)
                # prefetch the next list page while this page's details download
                if not reached_end and has_next and rows and page < MAX_PAGES:
                    pending = asyncio.ensure_future(self._list_page(page + 1))

                # detail pages for new/changed rows, in parallel (the shared rate limiter spaces them)
                fps = [_fingerprint(r) for r in keep]
                todo = [i for i, r in enumerate(keep)
                        if (old_details.get(r["id"]) or {}).get("fp") != fps[i]]
                fetched = await _gather_limited([self._detail(self._url(keep[i])) for i in todo])
                got = dict(zip(todo, fetched, strict=True))
                page_details = [got[i] if i in got else (old_details[r["id"]].get("d") or {})
                                for i, r in enumerate(keep)]

                # buyer cities for organisations not cached yet, in parallel
                new_orgs = list(dict.fromkeys(
                    d["buyer_org"] for d in page_details if d.get("buyer_org") and d["buyer_org"] not in orgs))
                await _gather_limited([self._city(o, orgs) for o in new_orgs])

                for row, fp, detail in zip(keep, fps, page_details, strict=True):
                    details[row["id"]] = {"fp": fp, "d": detail}
                    yield self.to_notice(row, detail, orgs.get(detail.get("buyer_org") or ""))
                yield Checkpoint({"details": {**old_details, **details}, "orgs": orgs, "page": page})
                page += 1
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
        if len(orgs) > ORG_CACHE_MAX:
            orgs = dict(list(orgs.items())[-ORG_CACHE_MAX:])
        yield Checkpoint({"details": details, "orgs": orgs})

    async def _list_page(self, page: int) -> str:
        return await self.http.get_text(LIST_URL, params={
            "searchType": "cftFTS", "T01_ps": PAGE_SIZE, f"{TABLE}-p": page,
            f"{TABLE}-s": "datePublished", f"{TABLE}-o": "1",
        })

    async def refresh(self, ref: StoredRef) -> Notice | None:
        row = dict((ref.raw or {}).get("row") or {})
        row.setdefault("id", ref.source_id)
        row.setdefault("href", ref.url)
        try:
            detail = await self._detail(self._url(row), strict=True)
        except HttpError as e:
            if e.status in (404, 410):
                return None
            raise
        if not detail.get("title") and not row.get("title"):
            return None
        org = detail.get("buyer_org")
        city = await self._city(org, {}) if org else None
        return self.to_notice(row, detail, city, refreshed=True)

    # --- helpers --------------------------------------------------------------------------------

    @staticmethod
    def _url(row: dict[str, Any]) -> str:
        return row.get("href") or f"{BASE}/epps/cft/prepareViewCfTWS.do?resourceId={row['id']}"

    async def _detail(self, url: str, strict: bool = False) -> dict[str, Any]:
        try:
            return parse_detail(await self.http.get_text(url))
        except HttpError as e:
            if strict:
                raise
            self.log.warning("cvp_lt detail %s failed: %s", url, e)
            return {}

    async def _city(self, org: str | None, cache: dict[str, Any]) -> str | None:
        if not org:
            return None
        if org in cache:
            return cache[org]
        try:
            city = parse_org_city(await self.http.get_text(ORG_URL, params={"id": org}))
        except HttpError as e:
            self.log.warning("cvp_lt organisation %s failed: %s", org, e)
            if e.status in (403, 404, 410):
                cache[org] = None
            return None
        cache[org] = city
        return city

    def to_notice(self, row: dict[str, Any], detail: dict[str, Any], city: str | None = None,
                  refreshed: bool = False) -> Notice:
        rid = str(row["id"])
        url = self._url(row)
        kind = _kind(url)
        status = STATUS.get((detail.get("status") or row.get("status") or "").lower(), Status.UNKNOWN)
        deadline = lt_date(row.get("deadline")) or lt_date(detail.get("deadline"))
        if refreshed:
            # the detail page is fresher than the stored list row
            deadline = lt_date(detail.get("deadline")) or deadline
            if detail.get("award_date"):
                status = Status.AWARDED
            elif status is Status.OPEN and deadline and deadline < datetime.now(UTC) and kind != "dps":
                status = Status.CLOSED
        elif row.get("award_date") and status is Status.UNKNOWN:
            status = Status.AWARDED
        value = parse_amount(row.get("value")) or parse_amount(detail.get("value"))
        ted = detail.get("ted") or []
        lang = (detail.get("language") or "lt").lower()
        refs = {"cvp": rid}
        if ted:
            refs["ted"] = ted[-1]
            if len(ted) > 1:
                refs["ted_all"] = ",".join(ted)
        if detail.get("reference"):
            refs["procedure"] = detail["reference"]
        documents = [Document(row["pdf"], title="Skelbimas (PDF)", kind="notice")] if row.get("pdf") else []
        platform = f"{BASE}/epps/cft/listContractDocuments.do?resourceId={rid}" if kind == "cft" else url
        return Notice(
            source=self.name,
            source_id=rid,
            url=url,
            title=row.get("title") or detail.get("title") or rid,
            notice_type=NoticeType.PLAN if kind == "pmc" else NoticeType.TENDER,
            status=status,
            description=detail.get("description") or row.get("description") or None,
            buyer_name=strip_pv(detail.get("buyer") or row.get("buyer")),
            buyer_country="LT",
            buyer_city=city,
            cpv_codes=list(detail.get("cpv") or []),
            procedure_type=row.get("procedure") or detail.get("procedure") or None,
            legal_basis=legal_basis(detail.get("directive")),
            published_at=lt_date(row.get("published")) or lt_date(detail.get("published")),
            deadline_at=deadline,
            value_amount=value,
            value_currency="EUR" if value else None,
            platform_url=platform,
            language=lang if re.fullmatch(r"[a-z]{2}", lang) else None,
            documents=documents,
            refs=refs,
            raw={"row": row, "detail": detail, "kind": kind},
        )
