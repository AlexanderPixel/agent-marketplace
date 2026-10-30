"""eB2B — Polish e-procurement platform (platforma.eb2b.com.pl, buyers on <buyer>.eb2b.com.pl).

eB2B is one multi-tenant ExtJS application: every buyer subdomain (wim.eb2b.com.pl, gddkia.eb2b.com.pl, ...)
is served by the same backend, and the "Lista postępowań otwartych" grid on the *global* host
(https://platforma.eb2b.com.pl/open-auctions.html) loads its rows from a public JSON endpoint that spans all
tenants (verified: rows of wim, jurasza, spsk4lublin, nio, gddkia, szpital-lublin, ... all appear there). So
instead of rotating through the 115 known tenants, the connector reads that one list:

1. `GET /auction/auction/list?page=&start=&limit=&sort=[publication_date DESC, id DESC]` — ExtJS store JSON
   `{"success", "total", "data": [...]}`. There is no date filter, so the walk goes newest-first and stops at
   the first row published before `since`. `filter=[{"property": "id", "value": <id>}]` returns one row, which
   is how `refresh` re-reads a notice.
2. `GET /auction/auction/list-attachment?id=<id>` — the public attachment list; files download anonymously
   from `/auction/auction/download-attachment/id/<attachment id>`.

The public notice page (`/open-preview-auction.html/<id>`) embeds richer JSON (buyer NIP/address, description,
CPV) but the platform caps how many notices one client may view ("Przekroczono dozwolony limit wyświetlania
postępowań w krótkim czasie", ~1 h lockout after a few dozen views), so the connector does not fetch it.

Speed: a list page (~2 s server time) is prefetched while the previous page's attachment lists (~0.2 s each)
are fetched concurrently; the shared rate limiter caps the request rate, the concurrency only overlaps latency.

`state["seen"]` keeps `{id: updated_at}` for the rows of the current window, so unchanged rows do not cost an
attachment request on the next incremental run; changes to open notices are picked up by `refresh`.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..connector import Checkpoint, Connector, StoredRef
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status

BASE = "https://platforma.eb2b.com.pl"
LIST_API = f"{BASE}/auction/auction/list"
ATTACHMENTS_API = f"{BASE}/auction/auction/list-attachment"
DETAIL = f"{BASE}/open-preview-auction.html"
DOWNLOAD = f"{BASE}/auction/auction/download-attachment/id"
WARSAW = ZoneInfo("Europe/Warsaw")
PAGE_SIZE = 100  # one list call costs ~2 s server-side whatever the size; fewer, larger pages
SORT = json.dumps([{"property": "publication_date", "direction": "DESC"}, {"property": "id", "direction": "DESC"}])
AJAX = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}

# App.model.auction.Auction.PUBLIC_ORDER_* (0 = not under the Public Procurement Law)
PROCEDURES = {
    1: "Przetarg nieograniczony", 2: "Przetarg ograniczony", 3: "Negocjacje z ogłoszeniem",
    4: "Dialog konkurencyjny", 5: "Negocjacje bez ogłoszenia", 6: "Zamówienie z wolnej ręki",
    7: "Zapytanie o cenę", 8: "Licytacja elektroniczna", 9: "Dialog techniczny", 10: "RFI",
    11: "Partnerstwo innowacyjne", 12: "Realizacja umów ramowych",
    13: "Zamówienia na usługi społeczne i inne szczególne usługi", 14: "Konkurs",
    15: "Tryb podstawowy – bez negocjacji", 16: "Tryb podstawowy – negocjacje fakultatywne",
    17: "Tryb podstawowy – negocjacje obligatoryjne", 18: "Rozeznanie rynku",
    19: "Dynamiczny system zakupów", 20: "System kwalifikowania Wykonawców",
}
NON_PZP = "Postępowanie poza ustawą Pzp"
PLAN_PROCEDURES = {9, 10}  # technical dialogue, RFI: market consultation, not a call for bids
ORDER_TYPES = {1: "supplies", 2: "works", 3: "services"}  # PUBLIC_ORDER_TYPE_*

# App.model.auction.Auction.STATUS_* / AUCTION_STATE_*
STATUS_FINISHED, STATUS_CANCELED, STATUS_RESOLVED = 3, 9, 10
STATE_CANCELED = 4

_DT = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")
_BZP = re.compile(r"\d{4}/BZP \d{8}(?:/\d{2})?")
_ROW_KEYS = (
    "id", "evidence_number", "auction_number", "name", "slug", "publication_date", "created_at", "updated_at",
    "company_name", "region_name", "assortment_group", "auction_type", "auction_state", "public_order_mode",
    "public_order_type", "is_public", "is_value_above_limit", "is_open", "enable_offers_without_login",
    "status_code", "status_name", "current_stage_type", "current_stage_begin_time", "current_stage_end_time",
    "participation_requests_deadline", "has_winning_offers", "bzp_links",
)


def _dt(v: str | None) -> datetime | None:
    """'2026-11-06 09:00:00' (Warsaw local time) -> aware datetime."""
    m = _DT.match(v or "")
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    return datetime(int(y), int(mo), int(d), int(h or 23), int(mi or 59), int(s or 0), tzinfo=WARSAW)


def _clean(s: Any) -> str | None:
    s = re.sub(r"\s+", " ", str(s or "").replace("​", "")).strip()
    return s or None


def notice_url(row: dict[str, Any]) -> str:
    return f"{DETAIL}/{row['id']}/{row['slug']}" if row.get("slug") else f"{DETAIL}/{row['id']}"


def _deadline(row: dict[str, Any]) -> datetime | None:
    """End of the current/last stage (offers, or applications for RFI/restricted stages).

    `participation_requests_deadline` is only a fallback: buyers often leave it weeks after the offer deadline.
    """
    stage = row.get("stage") if isinstance(row.get("stage"), dict) else {}
    for v in (stage.get("end_date"), row.get("current_stage_end_time"), row.get("participation_requests_deadline")):
        if d := _dt(v):
            return d
    return None


def _status(row: dict[str, Any]) -> Status:
    code = row.get("status_code")
    if code == STATUS_CANCELED or row.get("auction_state") == STATE_CANCELED:
        return Status.CANCELLED
    if code == STATUS_RESOLVED or (code == STATUS_FINISHED and row.get("has_winning_offers")):
        return Status.AWARDED
    if code == STATUS_FINISHED:
        return Status.CLOSED
    return Status.UNKNOWN  # during / between stages: the runner derives open/closed from the deadline


def _bzp(links: Any) -> str | None:
    for link in links or []:
        number = _clean(link.get("bzp_number")) if isinstance(link, dict) else None
        if number:
            m = _BZP.search(number)
            return m.group(0) if m else number
    return None


class Eb2b(Connector):
    name = "eb2b"
    title = "eB2B — platforma zakupowa"
    countries = ("PL",)
    homepage = "https://platforma.eb2b.com.pl/open-auctions.html"
    kind = "platform"
    poll_interval = timedelta(hours=1)
    first_sync_lookback = timedelta(days=3)
    requests_per_second = 4.0  # small JSON calls; attachment lists of a page are fetched concurrently
    supports_refresh = True

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        previous: dict[str, str] = dict(state.get("seen") or {})
        seen: dict[str, str] = {}
        page = 1
        pending: asyncio.Task[Any] | None = asyncio.ensure_future(self._page(page))
        try:
            while pending is not None:
                data, pending = await pending, None
                rows = data.get("data") or []
                done = not rows
                fresh: list[dict[str, Any]] = []
                for row in rows:
                    published = _dt(row.get("publication_date"))
                    if published is None or published < since:
                        done = True
                        break
                    if row.get("is_test") or row.get("is_hidden"):
                        continue
                    key, stamp = str(row["id"]), str(row.get("updated_at") or "")
                    if previous.get(key) == stamp:
                        seen[key] = stamp  # unchanged since the last run
                    else:
                        fresh.append(row)
                done = done or page * PAGE_SIZE >= int(data.get("total") or 0)
                if not done:  # fetch the next page while this page's attachment lists load
                    pending = asyncio.ensure_future(self._page(page + 1))
                lists = await asyncio.gather(*(self.attachments(r["id"]) for r in fresh))
                for row, attachments in zip(fresh, lists, strict=True):
                    yield self.to_notice(row, attachments or [])
                    if attachments is not None:  # retry the attachment list next run if it failed
                        seen[str(row["id"])] = str(row.get("updated_at") or "")
                yield Checkpoint({"seen": seen if done else {**previous, **seen}})
                page += 1
        finally:
            if pending is not None and not pending.done():
                pending.cancel()

    async def _page(self, page: int) -> dict[str, Any]:
        data = await self.http.get_json(LIST_API, headers=AJAX, params={
            "page": page, "start": (page - 1) * PAGE_SIZE, "limit": PAGE_SIZE, "sort": SORT})
        return data if isinstance(data, dict) else {}

    async def attachments(self, auction_id: Any) -> list[dict[str, Any]] | None:
        try:
            data = await self.http.get_json(ATTACHMENTS_API, headers=AJAX, params={"id": auction_id})
        except (HttpError, ValueError) as e:
            self.log.warning("eb2b: attachment list of %s unavailable: %s", auction_id, e)
            return None
        if not isinstance(data, dict) or not data.get("success"):
            return None
        return [x for x in data.get("data") or [] if isinstance(x, dict)]

    async def refresh(self, ref: StoredRef) -> Notice | None:
        data, attachments = await asyncio.gather(  # both requests in flight at once
            self.http.get_json(LIST_API, headers=AJAX, params={
                "page": 1, "start": 0, "limit": 1,
                "filter": json.dumps([{"property": "id", "value": int(ref.source_id)}])}),
            self.attachments(ref.source_id))
        rows = [r for r in data.get("data") or [] if str(r.get("id")) == ref.source_id]
        if not rows:
            return None
        if attachments is None:  # keep the stored documents rather than recording a version without them
            attachments = (ref.raw or {}).get("attachments") or []
        return self.to_notice(rows[0], attachments)

    def to_notice(self, row: dict[str, Any], attachments: list[dict[str, Any]]) -> Notice:
        mode = row.get("public_order_mode") or 0
        procedure = PROCEDURES.get(mode, NON_PZP)
        order_type = ORDER_TYPES.get(row.get("public_order_type") or 0)
        number = _clean(row.get("auction_number"))
        bzp = _bzp(row.get("bzp_links"))
        docs = [Document(f"{DOWNLOAD}/{x['id']}", title=_clean(x.get("name")), kind=_clean(x.get("type_name")))
                for x in attachments if x.get("id") and x.get("is_public", True) and x.get("is_active", True)]
        departments = [_clean(d.get("title")) for d in row.get("CompanyDepartments") or [] if isinstance(d, dict)]
        raw = {k: row.get(k) for k in _ROW_KEYS if k in row}
        raw["stage"] = {k: v for k, v in (row.get("stage") or {}).items() if k != "ExtraTimes"}
        raw["departments"] = [d for d in departments if d]
        raw["attachments"] = [{k: x.get(k) for k in ("id", "name", "extension", "size", "type_name", "is_public",
                                                     "is_active", "created_at")} for x in attachments[:50]]
        url = notice_url(row)
        return Notice(
            source=self.name,
            source_id=str(row["id"]),
            url=url,
            title=_clean(row.get("name")) or number or str(row["id"]),
            notice_type=NoticeType.PLAN if mode in PLAN_PROCEDURES else NoticeType.TENDER,
            status=_status(row),
            buyer_name=_clean(row.get("company_name")),
            buyer_country="PL",
            procedure_type=f"{procedure} ({order_type})" if order_type else procedure,
            published_at=_dt(row.get("publication_date")),
            deadline_at=_deadline(row),
            platform_url=url,
            language="pl",
            documents=docs,
            refs={k: v for k, v in {"procedure": number, "evidence": _clean(row.get("evidence_number")),
                                    "bzp": bzp}.items() if v},
            raw=raw,
        )
