"""SEAP / SICAP (e-licitatie.ro) — Romania's national e-procurement system (all buyers, incl. MApN units).

The public Angular SPA calls anonymous JSON endpoints; we use the two list endpoints it uses:

* `POST /api-pub/NoticeCommon/GetCNoticeList/`  — contract notices (CN, simplified SCN, RFQ, ...)
* `POST /api-pub/NoticeCommon/GetCANoticeList/` — award / result notices (CAN, SCNA, PCA, ...)

Body: `{"pageIndex", "pageSize", "startPublicationDate", "endPublicationDate", "sysNoticeTypeIds": [], ...}`.
Quirks found while probing (Oct 2026):
* a non-empty `Referer` header is mandatory (403 "Referrer cannot be null" otherwise);
* the date filter is day-granular (time of day is ignored) and `total` is capped at 3000, so we
  query one Romanian calendar day at a time (~100 CNs and ~250 CANs per working day);
* `pageSize` up to at least 200 works; results come newest-first.

The list rows already carry title, buyer (fiscal code + name), main CPV, procedure type, value (RON),
publication time and tender deadline, so no per-notice detail requests are needed. Every (list, day)
window is fetched concurrently and the remaining pages of each window are fetched concurrently too;
the shared rate limiter keeps the request rate polite.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse

from ..connector import Checkpoint, Connector
from ..models import Notice, NoticeType, Status, normalize_cpv

BASE = "https://www.e-licitatie.ro"
CN_API = f"{BASE}/api-pub/NoticeCommon/GetCNoticeList/"
CAN_API = f"{BASE}/api-pub/NoticeCommon/GetCANoticeList/"
CN_REFERER = f"{BASE}/pub/notices/contract-notices/list/0/0"
CAN_REFERER = f"{BASE}/pub/notices/contract-award-notices/list/0/0"
PAGE_SIZE = 100
DAYS_PER_BATCH = 7          # day windows fetched concurrently before a checkpoint
RESULT_CAP = 3000           # the API never reports more than this many hits for one query
TZ = ZoneInfo("Europe/Bucharest")

# (list kind) -> (endpoint, referer, id field)
LISTS = {
    "cn": (CN_API, CN_REFERER, "cNoticeId"),
    "can": (CAN_API, CAN_REFERER, "caNoticeId"),
}

# Public view routes of the SPA, per sysNoticeTypeId: (v1 route, v2 route). `{id}` is cNoticeId / caNoticeId.
CN_ROUTES = {
    2: ("/pub/notices/c-notice/v1/view/{id}", "/pub/notices/c-notice/v2/view/{id}"),
    12: ("/pub/notices/rfq-invitation/v1/view/{id}", "/pub/notices/rfq-invitation/v2/view/{id}"),
    17: ("/pub/notices/simplified-notice/v1/view/{id}", "/pub/notices/simplified-notice/v2/view/{id}"),
    7: ("/pub/notices/pc-notice/v1/view/{id}", "/pub/notices/pc-notice/v2/view/{id}"),
    6: ("/pub/notices/dc-notice/v1/view/{id}", "/pub/notices/dc-notice/v2/view/{id}"),
    19: ("/pub/rfqInvitationSad/view/{id}", "/pub/rfqInvitationSad/view/{id}"),
}
CN_SAD_ROUTE = "/pub/sad/cnotice/view/{id}"   # contract notice inside a dynamic purchasing system
CAN_ROUTES = {
    3: ("/pub/notices/ca-notices/view-c/v1/{id}", "/pub/notices/ca-notices/view-c/{id}"),
    8: ("/pub/notices/ca-notices/view-dc/v1/{id}", "/pub/notices/ca-notices-dc/view/{id}"),
    16: ("/pub/notices/ca-notices/view-pc/v1/{id}", "/pub/notices/ca-notices-pc/view/{id}"),
    13: ("/pub/notices/ca-notices/view-rfq/v1/{id}", "/pub/notices/ca-notices/view-rfq/{id}"),
    18: ("/pub/notices/ca-notices/view-scn/v1/{id}", "/pub/notices/ca-notices/view-snc/{id}"),
}

# sysProcedureState ids: 2 in progress, 3 cancelled, 4 suspended (observed), 5 awarded
PROC_STATE_CANCELLED = 3
PROC_STATE_AWARDED = 5
PROC_STATE_IN_PROGRESS = 2

_BUYER = re.compile(r"^\s*((?:RO?)?\s*\d{2,10})\s*-\s*(.+)$", re.IGNORECASE)   # "RO 123", "R123", "r123"


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    d = isoparse(v)
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def _txt(v: Any) -> str | None:
    if isinstance(v, dict):
        v = v.get("text")
    return v.strip() if isinstance(v, str) and v.strip() else None


def _id(v: Any) -> int | None:
    return v.get("id") if isinstance(v, dict) else None


def split_buyer(s: str | None) -> tuple[str | None, str | None]:
    """'4270783 - Ministerul Apararii - UM 01434' -> ('4270783', 'Ministerul Apararii - UM 01434')."""
    s = (s or "").strip()
    if not s:
        return None, None
    m = _BUYER.match(s)
    if not m:
        return None, s
    return re.sub(r"\D", "", m.group(1)), m.group(2).strip()


def notice_url(kind: str, x: dict[str, Any]) -> str:
    type_id = x.get("sysNoticeTypeId")
    v1 = x.get("sysNoticeVersionId") == 1
    if kind == "cn":
        nid = x["cNoticeId"]
        if type_id == 2 and _id(x.get("sysContractAssigmentType")) == 2:
            route = CN_SAD_ROUTE
        else:
            route = CN_ROUTES.get(type_id, CN_ROUTES[2])[0 if v1 else 1]
    else:
        nid = x["caNoticeId"]
        route = CAN_ROUTES.get(type_id, CAN_ROUTES[3])[0 if v1 else 1]
    return BASE + route.format(id=nid)


def _days(since: datetime, now: datetime) -> list[date]:
    first, last = since.astimezone(TZ).date(), now.astimezone(TZ).date()
    if first > last:
        first = last
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


class SicapRo(Connector):
    name = "sicap_ro"
    title = "SEAP / SICAP — Romania e-licitatie.ro"
    countries = ("RO",)
    homepage = "https://www.e-licitatie.ro"
    kind = "portal"
    poll_interval = timedelta(hours=1)
    first_sync_lookback = timedelta(days=7)
    requests_per_second = 2.0

    async def _page(self, kind: str, day: date, page: int) -> dict[str, Any]:
        url, referer, _ = LISTS[kind]
        body = {
            "sysNoticeTypeIds": [],
            "sortProperties": [],
            "pageSize": PAGE_SIZE,
            "hasUnansweredQuestions": False,
            "startPublicationDate": f"{day.isoformat()}T00:00:00",
            "endPublicationDate": f"{day.isoformat()}T00:00:00",
            "pageIndex": page,
        }
        data = await self.http.post_json(url, json=body, headers={"Referer": referer, "Accept": "application/json"})
        return data if isinstance(data, dict) else {}

    async def _window(self, kind: str, day: date) -> list[dict[str, Any]]:
        """All rows of one list for one publication day: page 0, then the rest concurrently."""
        first = await self._page(kind, day, 0)
        items = list(first.get("items") or [])
        total = int(first.get("total") or 0)
        if total >= RESULT_CAP:
            self.log.warning("sicap_ro %s %s: %s hits (API cap) — results may be truncated", kind, day, total)
        pages = math.ceil(total / PAGE_SIZE)
        if items and pages > 1:
            rest = await asyncio.gather(*(self._page(kind, day, p) for p in range(1, pages)))
            for data in rest:
                items.extend(data.get("items") or [])
        return items

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        days = _days(since, self.ctx.now())
        for i in range(0, len(days), DAYS_PER_BATCH):
            batch = days[i:i + DAYS_PER_BATCH]
            jobs = [(kind, day) for day in batch for kind in LISTS]
            results = await asyncio.gather(*(self._window(kind, day) for kind, day in jobs))
            seen: set[str] = set()
            for (kind, _), items in zip(jobs, results, strict=True):
                for x in items:
                    if not x.get("noticeNo") or not x.get(LISTS[kind][2]) or x["noticeNo"] in seen:
                        continue
                    seen.add(x["noticeNo"])
                    yield self.to_notice(kind, x)
            yield Checkpoint({"last_day": batch[-1].isoformat()})

    def to_notice(self, kind: str, x: dict[str, Any]) -> Notice:
        buyer_id, buyer_name = split_buyer(x.get("contractingAuthorityNameAndFN"))
        proc_state = _id(x.get("sysProcedureState"))
        deadline = _dt(x.get("maxTenderReceiptDeadline") or x.get("minTenderReceiptDeadline"))
        cpv = normalize_cpv(x.get("cpvCodeAndName") or "")
        if kind == "cn":
            notice_type = NoticeType.TENDER
            value = x.get("estimatedValueRon")
            status = {PROC_STATE_CANCELLED: Status.CANCELLED, PROC_STATE_AWARDED: Status.AWARDED}.get(
                proc_state, Status.UNKNOWN)
            if proc_state == PROC_STATE_IN_PROGRESS:
                status = Status.CLOSED if deadline and deadline < self.ctx.now() else Status.OPEN
            currency = "RON"
        else:
            cancelled = proc_state == PROC_STATE_CANCELLED
            notice_type = NoticeType.CANCELLATION if cancelled else NoticeType.AWARD
            status = Status.CANCELLED if cancelled else Status.AWARDED
            value = x.get("ronContractValue")
            currency = x.get("currencyCode") or "RON"
        value = float(value) if isinstance(value, (int, float)) and value > 0 else None
        refs = {
            "notice": x["noticeNo"],
            "procedure": str(x["procedureId"]) if x.get("procedureId") else None,
            "sicap_notice_id": str(x["noticeId"]) if x.get("noticeId") else None,
        }
        return Notice(
            source=self.name,
            source_id=x["noticeNo"],
            url=notice_url(kind, x),
            title=_txt(x.get("contractTitle")) or x["noticeNo"],
            notice_type=notice_type,
            status=status,
            buyer_name=buyer_name,
            buyer_id=buyer_id,
            buyer_country="RO",
            cpv_codes=[cpv] if cpv else [],
            procedure_type=_txt(x.get("sysProcedureType")),
            published_at=_dt(x.get("noticeStateDate")),
            deadline_at=deadline if kind == "cn" else None,
            value_amount=value,
            value_currency=currency if value is not None else None,
            language="ro",
            refs={k: v for k, v in refs.items() if v},
            raw={k: v for k, v in x.items() if v is not None},
        )
