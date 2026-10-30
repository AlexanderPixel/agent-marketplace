"""udbud.dk — Denmark's national notice portal (Erhvervsstyrelsen): every Danish notice, EU (TED) and
below-threshold (national "DKE*" subtypes), incl. the defence acquisition agency (FMI / DALO).

The Vue frontend talks to an anonymous JSON API:
  POST https://udbud.dk/soegning/public/soegeresultat   search; filter by publication date, max 100/page
  GET  https://udbud.dk/soegning/visning/{noticeId}/{noticeVersion}   detail (eForms HTML + summary)
Human-readable page: https://udbud.dk/detaljevisning?noticeId=...&noticeVersion=...

The search rows already carry title, buyer (+ CVR), publication date, main CPV, form type, eForms
subtype, deadlines and estimated value, so `sync` never touches detail pages: one request per 100
notices. Page 1 tells us the total; the remaining pages are fetched concurrently (the shared rate
limiter still paces them) and yielded in order.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse

from ..connector import Checkpoint, Connector
from ..models import Notice, NoticeType, Status, normalize_cpv

BASE = "https://udbud.dk"
API = f"{BASE}/soegning/public/soegeresultat"
DETAIL_PAGE = f"{BASE}/detaljevisning"
PAGE_SIZE = 100            # server rejects maksElementer > 100
MAX_PAGES = 100            # search index result window (10 000 hits)
PAGE_CONCURRENCY = 4       # pages in flight at once; the Http rate limiter does the actual pacing
CPH = ZoneInfo("Europe/Copenhagen")

FORM_TYPES = {
    "planning": NoticeType.PLAN,
    "consultation": NoticeType.PLAN,
    "competition": NoticeType.TENDER,
    "result": NoticeType.AWARD,
    "dir-awa-pre": NoticeType.AWARD,      # voluntary ex-ante transparency (direct award announced)
    "change": NoticeType.MODIFICATION,
    "cont-modif": NoticeType.MODIFICATION,
}
# English eForms subtype label ("Contract notice – defence directive, standard regime") -> CELEX
LEGAL_BASIS = (
    ("defence directive", "32009L0081"),
    ("sectoral directive", "32014L0025"),
    ("utilities directive", "32014L0025"),
    ("concession", "32014L0023"),
    ("general directive", "32014L0024"),
)


def _query(since: datetime, page: int) -> dict[str, Any]:
    return {
        "pagineringDto": {"aktuelSide": page, "maksElementer": PAGE_SIZE,
                          "sorteringFelt": "PUBLIKATION_DATO", "retning": "Asc"},
        "filterDto": {"publikationDatoFra": since.astimezone(CPH).date().isoformat()},
        "udbudStatusFilter": "ALLE",      # default "AKTIV" hides closed/awarded notices
    }


def _published(v: str | None) -> datetime | None:
    """'30-10-2026' (search rows) or '2026-10-30T13:55:29+01:00' (detail) -> local midnight of that day."""
    v = (v or "").strip()
    try:
        if len(v) == 10 and v[2] == "-":
            d = datetime.strptime(v, "%d-%m-%Y")
        else:
            d = isoparse(v).astimezone(CPH).replace(tzinfo=None)
    except ValueError:
        return None
    return d.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=CPH)


def _deadline(vs: list[str] | None) -> datetime | None:
    out = []
    for v in vs or []:
        try:
            d = isoparse(v)
        except ValueError:
            continue
        out.append(d if d.tzinfo else d.replace(tzinfo=CPH))
    return min(out) if out else None


def _cvr(x: dict[str, Any]) -> str | None:
    """Cleaned CVR ('ORG-16-28-71-80' -> '16287180'); None for non-Danish ids like 'COM'."""
    for v in (x.get("ordregiverIdDatavasket"), x.get("ordregiverId")):
        v = (v or "").upper().removeprefix("ORG").replace(" ", "").replace("-", "")
        if len(v) == 8 and v.isdigit():
            return v
    return None


def _legal_basis(label: str | None) -> str | None:
    label = (label or "").lower()
    return next((celex for key, celex in LEGAL_BASIS if key in label), None)


def _value(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


class UdbudDk(Connector):
    name = "udbud_dk"
    title = "udbud.dk — Danish national procurement notice portal"
    countries = ("DK",)
    homepage = "https://udbud.dk"
    kind = "portal"
    poll_interval = timedelta(hours=1)
    first_sync_lookback = timedelta(days=7)
    requests_per_second = 2.0

    async def _page(self, since: datetime, page: int) -> dict[str, Any]:
        return await self.http.post_json(API, json=_query(since, page), headers={"Accept": "application/json"})

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        now = self.ctx.now()
        first = await self._page(since, 1)
        total = int(first.get("totaltAntalResultater") or 0)
        pages = min(MAX_PAGES, math.ceil(total / PAGE_SIZE))
        if math.ceil(total / PAGE_SIZE) > MAX_PAGES:
            self.log.warning("udbud_dk: %d hits exceed the %d-page window; use a later --since", total, MAX_PAGES)

        seen: set[str] = set()
        tasks: dict[int, asyncio.Task[dict[str, Any]]] = {}
        try:
            data: dict[str, Any] | None = first
            for page in range(1, pages + 1):
                # keep up to PAGE_CONCURRENCY later pages in flight while this one is consumed
                for p in range(page + 1, min(pages, page + PAGE_CONCURRENCY) + 1):
                    if p not in tasks:
                        tasks[p] = asyncio.create_task(self._page(since, p))
                if data is None:
                    data = await tasks.pop(page)
                rows = data.get("resultatElementDtoList") or []
                for row in rows:
                    if row.get("noticeId") and row["noticeId"] not in seen:
                        seen.add(row["noticeId"])
                        yield self.to_notice(row, now)
                if not rows:
                    break
                data = None
        finally:
            for t in tasks.values():
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks.values(), return_exceptions=True)
        yield Checkpoint({"published_from": _query(since, 1)["filterDto"]["publikationDatoFra"], "total": total})

    def to_notice(self, row: dict[str, Any], now: datetime | None = None) -> Notice:
        nid, version = row["noticeId"], row.get("noticeVersion") or "01"
        pub_no = (row.get("noticePublicationNumber") or "").strip()
        da, en = row.get("dataDa") or {}, row.get("dataEn") or {}
        x = da or en
        form = x.get("formulartypeKode") or ""
        ntype = FORM_TYPES.get(form, NoticeType.OTHER)
        if x.get("erAendring") and ntype in (NoticeType.TENDER, NoticeType.PLAN, NoticeType.AWARD):
            ntype = NoticeType.MODIFICATION   # eForms change notice re-publishing the procedure
        deadline = _deadline(x.get("tidsfrister"))
        now = now or self.ctx.now()
        if ntype is NoticeType.AWARD:
            status = Status.AWARDED
        elif ntype is NoticeType.TENDER and deadline:
            status = Status.OPEN if deadline > now else Status.CLOSED
        else:
            status = Status.UNKNOWN
        cvr = _cvr(x)
        cpv = normalize_cpv(x.get("cpvKode") or "")
        refs = {"notice": nid}
        if pub_no:
            refs["ted"] = pub_no.lstrip("0") or pub_no   # '00753088-2026' -> '753088-2026'
        url = f"{DETAIL_PAGE}?{urlencode({'noticeId': nid, 'noticeVersion': version, 'noticePublicationNumber': pub_no})}"
        return Notice(
            source=self.name,
            source_id=nid,
            url=url,
            title=(x.get("titel") or "").strip() or nid,
            notice_type=ntype,
            status=status,
            description=(x.get("beskrivelse") or "").strip() or None,
            buyer_name=(x.get("ordregiver") or "").strip() or None,
            buyer_id=cvr,
            buyer_country="DK" if cvr else None,
            cpv_codes=[cpv] if cpv else [],
            legal_basis=_legal_basis(en.get("bkSubType")),
            published_at=_published(x.get("publiceringsdato")),
            deadline_at=deadline,
            value_amount=_value(x.get("anslaaetVaerdi")),
            value_currency=(x.get("anslaaetVaerdiValuta") or None) if _value(x.get("anslaaetVaerdi")) else None,
            refs=refs,
            raw={"noticeId": nid, "noticeVersion": version, "noticePublicationNumber": pub_no,
                 "dataDa": da, "dataEn": {k: en.get(k) for k in ("formulartype", "bkSubType", "cpvTitel")}},
        )
