"""Doffin — Norway's national procurement database (all Norwegian buyers, incl. Forsvarsmateriell).

The doffin.no frontend talks to an anonymous JSON API (no key needed):
  * POST https://api.doffin.no/webclient/api/v2/search-api/search — paged search with a
    `publicationDate` facet ({"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}); returns at most 1000
    accessible hits per query, so we query one publication day at a time (~70-150 notices/day).
  * GET  https://api.doffin.no/webclient/api/v2/notices-api/notices/<id> — full notice: CPV codes,
    TED id, procedure id, documents URL and the rendered eForm tree (Norwegian labels) from which we
    take procedure type, buyer city/country and legal basis.
Throughput: detail fetches dominate (one per notice, ~0.6 s latency each), so they run concurrently
(DETAIL_CONCURRENCY in flight, launched as soon as each search page arrives) and the next DAY_LOOKAHEAD
publication days are searched/fetched in the background while the current day is yielded. The shared
rate limiter still caps the total request rate at `requests_per_second`.
Human URL: https://doffin.no/notices/<id>. The official Azure API (dof-notices-prod-api) needs a
subscription key and is not used.
"""

from __future__ import annotations

import asyncio
import re
from collections import deque
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse

from ..connector import Checkpoint, Connector, StoredRef
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status, absolute_url, normalize_cpv

SEARCH_API = "https://api.doffin.no/webclient/api/v2/search-api/search"
NOTICE_API = "https://api.doffin.no/webclient/api/v2/notices-api/notices/{id}"
NOTICE_URL = "https://doffin.no/notices/{id}"
PAGE_SIZE = 100
DETAIL_CONCURRENCY = 8   # detail requests in flight (rate limiter still caps req/s)
DAY_LOOKAHEAD = 2        # publication days prefetched in the background beyond the one being yielded
OSLO = ZoneInfo("Europe/Oslo")

NOTICE_TYPES = {
    "ANNOUNCEMENT_OF_COMPETITION": NoticeType.TENDER,
    "COMPETITION": NoticeType.TENDER,
    "DYNAMIC_PURCHASING_SCHEME": NoticeType.TENDER,
    "QUALIFICATION_SCHEME": NoticeType.TENDER,
    "ANNOUNCEMENT_OF_CONCLUSION_OF_CONTRACT": NoticeType.AWARD,
    "ANNOUNCEMENT_OF_INTENT": NoticeType.AWARD,        # intention to award / voluntary ex-ante transparency
    "RESULT": NoticeType.AWARD,
    "CANCELLED_OR_MISSING_CONCLUSION_OF_CONTRACT": NoticeType.CANCELLATION,
    "CHANGE_OF_CONCLUSION_OF_CONTRACT": NoticeType.MODIFICATION,
    "PLANNING": NoticeType.PLAN,
    "ADVISORY_NOTICE": NoticeType.PLAN,                # market consultation ("Markedsundersøkelse")
    "PRE_ANNOUNCEMENT": NoticeType.PLAN,
    "NOTICE_ON_BUYER_PROFILE": NoticeType.PLAN,
}
STATUSES = {
    "ACTIVE": Status.OPEN,
    "EXPIRED": Status.CLOSED,
    "AWARDED": Status.AWARDED,
    "CANCELLED": Status.CANCELLED,
}
COUNTRY_NAMES = {  # eForm "Land" values (Norwegian)
    "norge": "NO", "sverige": "SE", "danmark": "DK", "finland": "FI", "island": "IS", "tyskland": "DE",
    "storbritannia": "GB", "nederland": "NL", "frankrike": "FR", "polen": "PL", "estland": "EE",
    "latvia": "LV", "litauen": "LT", "belgia": "BE", "spania": "ES", "italia": "IT", "østerrike": "AT",
    "sveits": "CH", "irland": "IE", "usa": "US", "sri lanka": "LK",
}
_DIRECTIVE = re.compile(r"Direktiv\s+(\d{4})/(\d+)/E[UFØ]", re.I)
_ORG = re.compile(r"^ORG-\d+$")


def _days(start: date, end: date) -> Iterator[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _walk(nodes: list[dict[str, Any]] | None) -> Iterator[dict[str, Any]]:
    for n in nodes or []:
        yield n
        yield from _walk(n.get("sections"))


def _eform_value(eform: list[dict[str, Any]] | None, label: str) -> str | None:
    for n in _walk(eform):
        if n.get("label") == label and (n.get("value") or "").strip():
            return n["value"].strip()
    return None


def _organisations(eform: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """eForm organisation blocks (value 'ORG-0001') flattened to {label: value}."""
    orgs = []
    for n in _walk(eform):
        if _ORG.match(str(n.get("value") or "")):
            orgs.append({c["label"]: (c.get("value") or "").strip()
                         for c in _walk(n.get("sections")) if c.get("label")})
    return orgs


def _digits(v: str | None) -> str:
    return re.sub(r"\D", "", v or "")


def _legal_basis(eform: list[dict[str, Any]] | None) -> str | None:
    """eForm 'Rettslig grunnlag' child labelled 'Direktiv 2014/24/EU' -> CELEX '32014L0024'.

    `regulationUrl` is deliberately not used: Doffin sets it to the defence regulation (FOSA) for many
    ordinary below-threshold municipal notices, so it is not a reliable legal basis.
    """
    for n in _walk(eform):
        m = _DIRECTIVE.search(n.get("label") or "")
        if m:
            return f"3{m.group(1)}L{int(m.group(2)):04d}"
    return None


def _ts(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        t = isoparse(v)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=OSLO)


def _published(pub_date: str | None, issue: str | None) -> datetime | None:
    """Doffin gives a publication *date*; use the precise issue timestamp when it falls on that day."""
    issued = _ts(issue)
    if not pub_date:
        return issued
    try:
        day = date.fromisoformat(pub_date[:10])
    except ValueError:
        return issued
    if issued and issued.astimezone(OSLO).date() == day:
        return issued
    return datetime.combine(day, time(0, 0), tzinfo=OSLO)


class Doffin(Connector):
    name = "doffin"
    title = "Doffin — Norwegian public procurement database"
    countries = ("NO",)
    homepage = "https://doffin.no"
    kind = "portal"
    poll_interval = timedelta(hours=1)
    first_sync_lookback = timedelta(days=3)
    requests_per_second = 5.0   # contract maximum; detail fetches are pipelined to saturate it
    supports_refresh = True

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        today = self.ctx.now().astimezone(OSLO).date()
        start = min(since.astimezone(OSLO).date(), today)
        days = iter(_days(start, today))
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
        seen: set[str] = set()
        pending: deque[tuple[date, asyncio.Task[list[tuple[dict[str, Any], dict[str, Any] | None]]]]] = deque()

        def schedule() -> None:
            while len(pending) <= DAY_LOOKAHEAD:
                day = next(days, None)
                if day is None:
                    return
                pending.append((day, asyncio.create_task(self._fetch_day(day, today, sem, seen))))

        try:
            schedule()
            while pending:
                day, task = pending.popleft()
                schedule()  # keep the background window full while we wait on this day
                for hit, detail in await task:
                    yield self.to_notice(hit, detail)
                yield Checkpoint({"last_day": day.isoformat()})
        finally:  # error or early close (max_notices): stop background work
            for _, t in pending:
                t.cancel()
            await asyncio.gather(*(t for _, t in pending), return_exceptions=True)

    async def _fetch_day(self, day: date, today: date, sem: asyncio.Semaphore,
                         seen: set[str]) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
        """All hits of one publication day with their details; details start as soon as a page arrives."""
        # the last window is open-ended so nothing dated after "today" (clock skew) is missed
        to = day.isoformat() if day < today else None

        async def one(nid: str) -> dict[str, Any] | None:
            async with sem:
                return await self._detail(nid)

        hits: list[dict[str, Any]] = []
        tasks: list[asyncio.Task[dict[str, Any] | None]] = []
        try:
            async with asyncio.TaskGroup() as tg:
                async for hit in self._search_day(day.isoformat(), to):
                    nid = hit.get("id")
                    if not nid or nid in seen:
                        continue
                    seen.add(nid)
                    hits.append(hit)
                    tasks.append(tg.create_task(one(nid)))
        except ExceptionGroup as eg:  # surface the real error (e.g. HttpError) to the runner
            raise eg.exceptions[0] from eg
        return [(h, t.result()) for h, t in zip(hits, tasks, strict=True)]

    async def _search_day(self, frm: str, to: str | None) -> AsyncIterator[dict[str, Any]]:
        page = 1
        while True:
            body = {
                "numHitsPerPage": PAGE_SIZE,
                "page": page,
                "searchString": "",
                "sortBy": "PUBLICATION_DATE_DESC",
                "facets": {"publicationDate": {"from": frm, "to": to}},
            }
            data = await self.http.post_json(SEARCH_API, json=body, headers={"Accept": "application/json"})
            hits = data.get("hits") or []
            for h in hits:
                yield h
            total = int(data.get("numHitsTotal") or 0)
            accessible = int(data.get("numHitsAccessible") or total)
            if total > accessible and page == 1:
                self.log.warning("doffin %s: %d hits but only %d accessible", frm, total, accessible)
            if not hits or len(hits) < PAGE_SIZE or page * PAGE_SIZE >= min(total, accessible):
                break
            page += 1

    async def _detail(self, nid: str) -> dict[str, Any] | None:
        try:
            return await self.http.get_json(NOTICE_API.format(id=nid), headers={"Accept": "application/json"})
        except HttpError as e:
            if e.status in (403, 404, 410):
                self.log.info("doffin %s: detail unavailable (%s), using search hit only", nid, e.status)
                return None
            raise

    async def refresh(self, ref: StoredRef) -> Notice | None:
        detail = await self._detail(ref.source_id)
        if detail is None:
            return None
        hit = (ref.raw or {}).get("hit")
        return self.to_notice(hit if isinstance(hit, dict) else None, detail)

    def to_notice(self, hit: dict[str, Any] | None, detail: dict[str, Any] | None) -> Notice:
        h = hit or {}
        d = detail or {}
        nid = d.get("id") or h["id"]
        eform = d.get("eform")

        types = [t for t in [d.get("noticeType") or h.get("type"), *(d.get("allTypes") or h.get("allTypes") or [])] if t]
        ntype = next((NOTICE_TYPES[t] for t in types if t in NOTICE_TYPES), NoticeType.OTHER)
        raw_status = d.get("noticeStatus") if detail else h.get("status")
        status = STATUSES.get(raw_status or "", Status.UNKNOWN)
        if status is Status.UNKNOWN:
            status = {NoticeType.AWARD: Status.AWARDED, NoticeType.CANCELLATION: Status.CANCELLED}.get(ntype, status)

        buyers = d.get("buyer") or h.get("buyer") or []
        b0 = buyers[0] if buyers else {}
        buyer_name = (b0.get("name") or "").strip() or None
        # search hits carry the org number as `organizationId` (their `id` is a hash); details use `id`
        buyer_id = _digits(b0.get("organizationId") or (b0.get("id") if d.get("buyer") else None)) or None
        orgs = _organisations(eform)
        org = next((o for o in orgs if buyer_id and _digits(o.get("Organisasjonsnummer")) == buyer_id), None)
        if org is None and buyer_name:
            org = next((o for o in orgs if (o.get("Offisielt navn") or "").casefold() == buyer_name.casefold()), None)
        if org is None and orgs and not buyer_name:
            org = orgs[0]
        org = org or {}
        land = (org.get("Land") or "").casefold()
        buyer_country = COUNTRY_NAMES.get(land) if land else "NO"
        city = (org.get("By") or "").strip()
        buyer_city = city.title() if city.isupper() else (city or None)

        cpv = d.get("directCpvCodes") or d.get("allCpvCodes") or []
        cpv_codes = list(dict.fromkeys(c for c in (normalize_cpv(str(x)) for x in cpv) if c))

        value = ((d.get("core") or {}).get("estimatedValue") if detail else h.get("estimatedValue")) or {}
        amount = value.get("amount")
        currency = value.get("code") or value.get("currencyCode")

        deadline = None
        if ntype in (NoticeType.TENDER, NoticeType.PLAN, NoticeType.OTHER):
            deadline = _ts(d.get("deadline") or h.get("deadline") or d.get("qualificationDeadline"))

        docs_url = absolute_url(d.get("competitionDocsUrl"))
        refs = {k: v for k, v in {
            "ted": d.get("tedId"),
            "procedure": d.get("procedureId"),
            "notice": d.get("eFormId"),
            "internal": _eform_value(eform, "Intern identifikator"),
        }.items() if isinstance(v, str) and v}

        raw: dict[str, Any] = {"hit": h} if hit else {}
        if detail:
            raw["detail"] = {k: v for k, v in d.items() if k != "eform"}

        return Notice(
            source=self.name,
            source_id=nid,
            url=NOTICE_URL.format(id=nid),
            title=(d.get("heading") or h.get("heading") or "").strip() or nid,
            notice_type=ntype,
            status=status,
            description=(d.get("description") or h.get("description") or "").strip() or None,
            buyer_name=buyer_name,
            buyer_id=buyer_id,
            buyer_country=buyer_country,
            buyer_city=buyer_city,
            cpv_codes=cpv_codes,
            procedure_type=_eform_value(eform, "Type prosedyre"),
            legal_basis=_legal_basis(eform),
            published_at=_published(d.get("publicationDate") or h.get("publicationDate"),
                                    d.get("issueDate") or h.get("issueDate")),
            deadline_at=deadline,
            value_amount=float(amount) if isinstance(amount, (int, float)) and amount > 0 else None,
            value_currency=currency if isinstance(amount, (int, float)) and amount > 0 else None,
            platform_url=docs_url,
            language="no",
            documents=[Document(docs_url, kind="procurement_documents")] if docs_url else [],
            refs=refs,
            raw=raw,
        )
