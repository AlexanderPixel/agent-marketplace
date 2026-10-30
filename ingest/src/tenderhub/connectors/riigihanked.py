"""Riigihangete register (riigihanked.riik.ee) — Estonia's national public procurement register.

Every Estonian contracting authority publishes here, including the Estonian Centre for Defence
Investment (Riigi Kaitseinvesteeringute Keskus, RKIK) and Kaitseliit.

The register is an AngularJS SPA backed by an anonymous JSON API under `/rhr/api/public/v1`:

* `POST /search/procurements` — the frontend's procurement search. Needs `orderBy` in the body and the
  Spring CSRF double-submit pair (session cookies from any GET + the `X-XSRF-TOKEN` header), exactly as
  the public site does it. It ignores paging parameters and caps every answer at 500 rows, so we query
  one publication ("reveal") day at a time (~30–60 procurements per day).
* `GET /procurement/{id}/header-info` — buyer registry code, current deadline and state.
* `GET /procurement/{id}/latest-version` -> `GET /proc-vers/{version}/general-info` — CPV codes, description,
  estimated value.

Speed: all days are searched concurrently and every procurement's three detail calls overlap (header-info
runs next to the latest-version -> general-info chain), with up to DETAIL_CONCURRENCY procurements in flight;
the shared rate limiter (`requests_per_second`) is the only throttle.

The monthly open-data XML (`/opendata/notice/YYYY/month/MM/xml`) is ~35 MB, too heavy for hourly polling.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse

from ..connector import Checkpoint, Connector, Context, StoredRef
from ..http import HttpError
from ..models import Notice, NoticeType, Status, normalize_cpv

BASE = "https://riigihanked.riik.ee"
API = f"{BASE}/rhr/api/public/v1"
SEARCH = f"{API}/search/procurements"
BOOTSTRAP = f"{API}/config/version"   # any GET hands out JSESSIONID + XSRF-TOKEN cookies
WEB = f"{BASE}/rhr-web/#/procurement"
TZ = ZoneInfo("Europe/Tallinn")
MAX_ROWS = 500                        # the search silently truncates at this many rows
DETAIL_CONCURRENCY = 12               # procurements whose details are in flight at once

# domains/PROCEDURE_TYPE/en/values
PROCEDURE_TYPES = {
    "A": "Open procedure", "LM": "Simple procurement", "LQ": "Simple procurement", "LD": "Simple procurement",
    "P": "Restricted procedure", "PQ": "Restricted procedure", "PD": "Restricted procedure",
    "G": "Competitive procedure with negotiation", "GQ": "Competitive procedure with negotiation",
    "VD": "Competitive dialogue", "IP": "Innovation partnership", "KE": "Concession contract",
    "SE": "Special procedure of social and special services", "LT": "Light regime for buying services",
    "T": "Negotiated procedure without prior publication of tender notice",
    "TQ": "Negotiated procedure without prior publication of tender notice",
    "M": "Reopening of competition on the basis of framework contract",
    "DP": "Dynamic procurement system's restricted procedure", "QS": "Qualification system",
    "IK": "Open design contest", "PK": "Design contest with limited number of participants",
    "LV": "Simplified utilities procedure", "VO": "Low-value purchase", "MS": "Other acquisition",
    "LA": "Based on an exception", "TU": "Market consultations", "PH": "Prior notice for procurement start",
    "PN": "Prior information notice for shortening deadlines",
    "SS": "Procurement related to the advance notice of social and special services",
}
PLAN_PROCEDURES = {"TU", "PH", "PN", "SS"}
DIRECT_AWARD_PROCEDURES = {"T", "TQ", "LA"}

# domains/PROCUREMENT_STATE/en/values
STATES = {
    "01": Status.UNKNOWN,   # preparation
    "02": Status.UNKNOWN,   # prior information published
    "03": Status.UNKNOWN, "04": Status.UNKNOWN, "16": Status.UNKNOWN,
    "11": Status.OPEN,      # started
    "14": Status.OPEN,      # open for entry (dynamic purchasing system / qualification system)
    "12": Status.CLOSED,    # evaluation
    "15": Status.CLOSED,    # closed for entry
    "21": Status.CLOSED,    # requests to participate submitted
    "22": Status.CLOSED,    # pending a solution
    "23": Status.CLOSED,    # pending a tender
    "31": Status.CLOSED,    # pending a contract
    "13": Status.AWARDED,   # contract performance
    "19": Status.AWARDED,   # contract completed
    "91": Status.CANCELLED,  # terminated
    "92": Status.CANCELLED,  # cancelled
}
PROCUREMENT_TYPES = {"A": "supplies", "E": "works", "T": "services", "S": "social services", "R": "special services"}


def _dt(v: str | None) -> datetime | None:
    """'2026-10-30T15:00:18.018+0200' -> aware datetime (naive values are Tallinn local time)."""
    if not v:
        return None
    try:
        d = isoparse(v)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def _str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _cost(g: dict[str, Any]) -> float | None:
    """Estimated value (EUR, without VAT) — only when the buyer made it public (`showCost`)."""
    cost = g.get("cost")
    if not g.get("showCost") or not isinstance(cost, int | float) or cost <= 0:
        return None
    return float(cost)


def _days(since: datetime, now: datetime) -> list[date]:
    first, last = since.astimezone(TZ).date(), now.astimezone(TZ).date()
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


class Riigihanked(Connector):
    name = "riigihanked"
    title = "Riigihangete register (Estonia)"
    countries = ("EE",)
    homepage = "https://riigihanked.riik.ee/rhr-web/"
    kind = "portal"
    poll_interval = timedelta(hours=2)
    first_sync_lookback = timedelta(days=3)
    requests_per_second = 5.0          # small JSON answers from a public API; the limiter is the only throttle
    supports_refresh = True

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self._xsrf: str | None = None
        self._boot_lock = asyncio.Lock()

    # --- transport -------------------------------------------------------------------------------

    async def _token(self) -> str:
        """The current XSRF token, bootstrapping a session once even when many searches race for it."""
        async with self._boot_lock:
            if not self._xsrf:
                r = await self.http.get(BOOTSTRAP, headers={"Accept": "application/json"})
                self._xsrf = r.cookies.get("XSRF-TOKEN") or self.http.client.cookies.get("XSRF-TOKEN")
                if not self._xsrf:
                    raise RuntimeError("riigihanked: no XSRF-TOKEN cookie from the public API")
            return self._xsrf

    async def _search_day(self, day: date) -> list[dict[str, Any]]:
        body = {
            "filter": {"procurementProcessRevealDateBegin": day.isoformat(),
                       "procurementProcessRevealDateEnd": day.isoformat()},
            "orderBy": {"procurementProcessRevealDate": "desc"},
        }
        for attempt in range(2):
            token = await self._token()
            try:
                r = await self.http.post(SEARCH, json=body, headers={
                    "Accept": "application/json", "X-XSRF-TOKEN": token})
            except HttpError as e:
                if e.status in (401, 403) and attempt == 0:   # session/token expired: start a new one
                    if self._xsrf == token:                   # only the first racer resets it
                        self._xsrf = None
                    continue
                raise
            if fresh := r.cookies.get("XSRF-TOKEN"):
                self._xsrf = fresh
            rows = r.json()
            if not isinstance(rows, list):
                raise RuntimeError(f"riigihanked: unexpected search response {str(rows)[:200]}")
            if len(rows) >= MAX_ROWS:
                self.log.warning("riigihanked: %s returned %d rows (cap) — some may be missing", day, len(rows))
            return rows
        raise AssertionError("unreachable")

    async def _general(self, pid: int | str) -> dict[str, Any] | None:
        try:
            ver = await self.http.get_json(f"{API}/procurement/{pid}/latest-version")
            vid = ver.get("value") if isinstance(ver, dict) else None
            if not vid:
                return None
            g = await self.http.get_json(f"{API}/proc-vers/{vid}/general-info")
        except HttpError as e:
            self.log.warning("riigihanked: no general-info for %s: %s", pid, e)
            return None
        return g if isinstance(g, dict) and "procurement" in g else None

    async def _details(self, pid: int | str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """(header-info, general-info of the latest published version), fetched concurrently:
        header-info runs alongside the latest-version -> general-info chain, so each procurement costs
        two round trips of latency instead of three."""
        header, general = await asyncio.gather(
            self.http.get_json(f"{API}/procurement/{pid}/header-info"), self._general(pid))
        if not isinstance(header, dict) or not header.get("procurementName"):
            return None, None
        return header, general

    async def _fetch_day(self, day: date, sem: asyncio.Semaphore) -> list[Notice]:
        rows = [r for r in await self._search_day(day) if r.get("procurementId")]

        async def one(row: dict[str, Any]) -> Notice:
            async with sem:
                header, general = await self._details(row["procurementId"])
            return self.to_notice(row["procurementId"], row, header, general)

        return list(await asyncio.gather(*(one(r) for r in rows)))

    # --- contract --------------------------------------------------------------------------------

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        # Every day's search and all detail fetches run at once (bounded by DETAIL_CONCURRENCY and the
        # shared rate limiter); results are still yielded day by day, oldest first, so a Checkpoint
        # only ever follows a fully emitted day.
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
        days = _days(since, self.ctx.now())
        tasks = [asyncio.create_task(self._fetch_day(d, sem)) for d in days]
        try:
            for day, task in zip(days, tasks, strict=True):
                for notice in await task:
                    yield notice
                yield Checkpoint({"day": day.isoformat()})
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def refresh(self, ref: StoredRef) -> Notice | None:
        pid = ref.source_id
        try:
            header, general = await self._details(pid)
        except HttpError as e:
            if e.status in (404, 410):
                return None
            raise
        if header is None:
            return None
        return self.to_notice(pid, (ref.raw or {}).get("search") or {}, header, general)

    # --- mapping ---------------------------------------------------------------------------------

    def to_notice(self, pid: int | str, row: dict[str, Any], header: dict[str, Any] | None,
                  general: dict[str, Any] | None) -> Notice:
        h, g = header or {}, general or {}
        proc = g.get("procurement") or {}
        ref_nr = _str(h.get("referenceNumber") or row.get("procurementReferenceNr"))
        proc_type = h.get("procedureTypeCode") or row.get("procurementProcessType") or proc.get("procedureTypeCode")
        state = h.get("procurementStateCode") or row.get("procurementStatus")
        if proc_type in PLAN_PROCEDURES:
            notice_type = NoticeType.PLAN
        elif proc_type in DIRECT_AWARD_PROCEDURES:
            notice_type = NoticeType.AWARD
        else:
            notice_type = NoticeType.TENDER
        cpvs = [normalize_cpv(c.get("code", "")) for c in sorted(
            g.get("procurementCpvDtos") or [], key=lambda c: int(c.get("orderNr") or 0))]
        title = _str(h.get("procurementName") or row.get("procurementName") or proc.get("name")) or f"Hange {ref_nr or pid}"
        published = _dt(h.get("publicationDate") or row.get("procProcessRevealDate"))
        url = f"{WEB}/{pid}/general-info"
        return Notice(
            source=self.name,
            source_id=str(pid),
            url=url,
            title=title,
            notice_type=notice_type,
            status=STATES.get(state or "", Status.UNKNOWN),
            description=_str(g.get("shortDescription") or row.get("shortDescription")),
            buyer_name=_str(h.get("contractingAuthorityName") or row.get("contractingAuthorityName")),
            buyer_id=_str(h.get("contractingAuthorityCode")),
            buyer_country="EE",
            cpv_codes=list(dict.fromkeys(c for c in cpvs if c)),
            procedure_type=PROCEDURE_TYPES.get(proc_type or "", proc_type),
            published_at=published,
            value_amount=_cost(g),
            value_currency="EUR" if _cost(g) is not None else None,
            deadline_at=_dt(h.get("submissionDate") or row.get("procProcessSubmitDate")),
            platform_url=f"{WEB}/{pid}/documents",
            language="et",
            refs={k: v for k, v in {"rhr": ref_nr, "rhr_id": str(pid)}.items() if v},
            raw={
                "search": row,
                "header": h,
                "general": {k: g.get(k) for k in ("procurement", "procurementCpvDtos", "startDate", "endDate",
                                                   "isDividedIntoParts", "mainProcurerName",
                                                   "mainProcurerTypeCodes", "submissionDateOpening")
                            if k in g},
                "procurement_type": PROCUREMENT_TYPES.get(
                    proc.get("procurementTypeCode") or row.get("procurementType") or ""),
            },
        )
