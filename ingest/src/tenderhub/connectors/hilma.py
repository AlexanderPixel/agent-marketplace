"""Hilma — Finland's national procurement notice service (hankintailmoitukset.fi).

Hilma's own search page queries an Azure Cognitive Search index through an anonymous proxy at
`https://www.hankintailmoitukset.fi/search/eformnotices` (OData-style params: `search`, `$filter`,
`$orderby`, `$top`, `$skip`, `$select`, `$count`). No key is needed for it, unlike the official
Open API on Azure API Management. The index holds eForms notices (`EF-<noticeId>`), legacy national
forms (`OLD-<noticeId>`) and procurement plans (`PLAN-<planId>`) — one document per published notice,
with flat fields (titleFi/Sv/En, organisation*, cpvCodes as a space-separated string, ISO timestamps in UTC).

We filter on `dateModified` (≥ datePublished) so notices updated after publication are re-read too,
and page with `$skip` ordered by `dateModified asc, id asc`. The first page carries `@odata.count`, so
all remaining pages of a window are known up front and fetched concurrently (the shared rate limiter still
spaces request starts); results are yielded in order with a checkpoint after each page. Windows deeper
than Azure's `$skip` limit restart from the last `dateModified` seen.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from dateutil.parser import isoparse

from ..connector import Checkpoint, Connector, StoredRef
from ..countries import iso3_to_iso2
from ..models import Document, Notice, NoticeType, Status, absolute_url, normalize_cpv

SEARCH = "https://www.hankintailmoitukset.fi/search/eformnotices"
WEB = "https://www.hankintailmoitukset.fi"
PAGE_SIZE = 250     # $top up to 1000 works; 250 rows (~1 MB) keep each response around a second
CONCURRENCY = 6     # pages of one window fetched in parallel
MAX_SKIP = 100_000  # Azure Cognitive Search hard limit for $skip
SELECT = ",".join([
    "id", "noticeId", "noticeNumber", "procedureId", "planId", "oldProcurementProjectId", "contractFolderId",
    "type", "mainType", "isEForms", "isPlan", "isCorrigendum", "isCancelled", "datePublished", "dateModified",
    "deadline", "expirationDate", "procedureType", "procurementTypeCode",
    "titleFi", "titleSv", "titleEn", "titleOther", "descriptionFi", "descriptionSv", "descriptionEn", "descriptionOther",
    "organisationNameFi", "organisationNameSv", "organisationNameEn", "organisationNameOther",
    "organisationNationalRegistrationNumber", "organisationAddress", "organisationDepartment", "nutsCodes", "cpvCodes",
    "estimatedValue", "currency", "noticeResultTotalAmount", "noticeResultTotalAmountCurrency", "winnerOrganisations",
    "tedPublicationId", "noticeOjsNumber", "linkedNotices", "procurementDocumentsUrl", "sendingSystem",
    "includesFrameworkAgreement", "includesDynamicPurcharingSystem", "isEuProcurement", "isNationalProcurement",
    "lots/id", "lots/titleFi", "lots/titleSv", "lots/titleEn", "lots/deadline",
])

MAIN_TYPES = {
    "ContractNotices": NoticeType.TENDER,
    "ContractAwardNotices": NoticeType.AWARD,
    "PriorInformationNotices": NoticeType.PLAN,
    "ProcurementPlan": NoticeType.PLAN,
    "Modification": NoticeType.MODIFICATION,
}
PIN_AS_CALL = {"7", "8", "9"}            # eForms PIN used as a call for competition
VEAT = {"25", "26", "27", "28"}          # voluntary ex-ante transparency: direct award announced
# Hilma uses the same language-suffixed fields everywhere; Fi first because it is nearly always filled.
LANGS = (("Fi", "fi"), ("Sv", "sv"), ("En", "en"), ("Other", None))
_ADDRESS = re.compile(r"\b\d{5}\s+(?P<city>.+?)(?:\s+(?P<country>[A-Z]{2,3}))?\s*$")


def _ml(x: dict[str, Any], prefix: str) -> tuple[str | None, str | None]:
    """First non-empty of <prefix>Fi/Sv/En/Other -> (text, ISO 639-1 language or None)."""
    for suffix, lang in LANGS:
        v = (x.get(prefix + suffix) or "").strip()
        if v:
            return v, lang
    return None, None


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    d = isoparse(v)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _amount(v: Any) -> float | None:
    """Hilma stores 0.0 / 1.0 as placeholders when no value was given."""
    return float(v) if isinstance(v, (int, float)) and v > 1 else None


def notice_url(x: dict[str, Any], lang: str = "fi") -> str:
    """Same rule as the Hilma frontend's search result links."""
    if x.get("isPlan") or str(x.get("id", "")).startswith("PLAN-"):
        return f"{WEB}/{lang}/public/plan/{x['planId']}"
    if x.get("isEForms"):
        return f"{WEB}/{lang}/public/procedure/{x['procedureId']}/enotice/{x['noticeId']}"
    return f"{WEB}/{lang}/public/procurement/{x['oldProcurementProjectId']}/notice/{x['noticeId']}/overview"


def _odata_dt(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Hilma(Connector):
    name = "hilma"
    title = "Hilma — Finnish public procurement notices"
    countries = ("FI",)
    homepage = "https://www.hankintailmoitukset.fi"
    kind = "portal"
    poll_interval = timedelta(hours=1)
    first_sync_lookback = timedelta(days=7)
    requests_per_second = 2.0
    supports_refresh = True

    async def _search(self, flt: str, skip: int = 0, top: int | None = None) -> dict[str, Any]:
        params = {
            "search": "*",
            "queryType": "full",
            "searchMode": "all",
            "$count": "true",
            "$top": str(top or PAGE_SIZE),
            "$skip": str(skip),
            "$orderby": "dateModified asc, id asc",
            "$filter": flt,
            "$select": SELECT,
        }
        return await self.http.get_json(SEARCH, params=params)

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        start = since
        while True:
            flt = f"dateModified ge {_odata_dt(start)}"
            first = await self._search(flt)
            items = first.get("value") or []
            total = first.get("@odata.count")
            for x in items:
                yield self.to_notice(x)
            if items:
                yield Checkpoint({"modified_through": items[-1].get("dateModified")})
            if len(items) < PAGE_SIZE:
                return
            last = items[-1]

            # Count known: every remaining page of the window is fetched concurrently, CONCURRENCY at a time.
            # Without a count, walk one page at a time until a short page.
            planned = list(range(PAGE_SIZE, min(total, MAX_SKIP + 1), PAGE_SIZE)) if total is not None else None
            skip, exhausted = PAGE_SIZE, False
            while not exhausted and skip <= MAX_SKIP:
                if planned is not None:
                    batch, planned = planned[:CONCURRENCY], planned[CONCURRENCY:]
                    if not batch:
                        break
                else:
                    batch = [skip]
                pages = await asyncio.gather(*(self._search(flt, skip=s) for s in batch))
                for page in pages:
                    rows = page.get("value") or []
                    for x in rows:
                        yield self.to_notice(x)
                    if rows:
                        last = rows[-1]
                        yield Checkpoint({"modified_through": last.get("dateModified")})
                    if planned is None and len(rows) < PAGE_SIZE:
                        exhausted = True
                skip = batch[-1] + PAGE_SIZE
            if exhausted or (total is not None and total <= MAX_SKIP + PAGE_SIZE):
                return
            # deeper than $skip allows: restart the window from the last modification time
            # (re-reads a few rows; storage dedups them)
            nxt = _dt(last.get("dateModified"))
            if nxt is None or nxt <= start:
                return
            start = nxt

    async def refresh(self, ref: StoredRef) -> Notice | None:
        sid = ref.source_id.replace("'", "''")
        data = await self._search(f"id eq '{sid}'", top=1)
        items = data.get("value") or []
        return self.to_notice(items[0]) if items else None

    def to_notice(self, x: dict[str, Any]) -> Notice:
        sid = x["id"]
        lots = x.get("lots") or []
        title, lang = _ml(x, "title")
        if not title:
            title = next((t for lot in lots for t in (lot.get("titleFi"), lot.get("titleSv"), lot.get("titleEn"))
                          if t and t.strip()), None)
        description, _ = _ml(x, "description")
        buyer, _ = _ml(x, "organisationName")

        city = country = None
        m = _ADDRESS.search(x.get("organisationAddress") or "")
        if m:
            city = m.group("city").strip() or None
            country = iso3_to_iso2(m.group("country"))
        if country is None and (x.get("organisationAddress") or x.get("organisationNationalRegistrationNumber")):
            country = "FI"  # Hilma buyers are Finnish contracting authorities

        main = x.get("mainType") or ""
        typ = str(x.get("type") or "")
        ntype = MAIN_TYPES.get(main, NoticeType.OTHER)
        if main == "PriorInformationNotices" and typ in PIN_AS_CALL:
            ntype = NoticeType.TENDER
        elif main == "PriorInformationNotices" and typ in VEAT:
            ntype = NoticeType.AWARD
        if x.get("isCorrigendum"):
            ntype = NoticeType.MODIFICATION
        if x.get("isCancelled"):
            ntype = NoticeType.CANCELLATION

        deadline = None
        if ntype not in (NoticeType.AWARD, NoticeType.CANCELLATION) and main not in ("ContractAwardNotices",
                                                                                      "Modification"):
            deadline = _dt(x.get("deadline"))
            if deadline is None:
                lot_deadlines = [d for d in (_dt(lot.get("deadline")) for lot in lots) if d]
                deadline = min(lot_deadlines) if lot_deadlines else None

        if x.get("isCancelled"):
            status = Status.CANCELLED
        elif ntype is NoticeType.AWARD:
            status = Status.AWARDED
        elif deadline is not None:
            status = Status.OPEN if deadline > datetime.now(UTC) else Status.CLOSED
        else:
            status = Status.UNKNOWN

        value = _amount(x.get("estimatedValue"))
        currency = x.get("currency") if value is not None else None
        if value is None:
            value = _amount(x.get("noticeResultTotalAmount"))
            currency = x.get("noticeResultTotalAmountCurrency") if value is not None else None

        url = notice_url(x)
        platform = absolute_url(x.get("procurementDocumentsUrl"))
        if platform == url:
            platform = None
        docs = [Document(platform, kind="procurement_documents")] if platform else []

        refs = {
            "hilma": x.get("noticeNumber"),
            "ted": x.get("tedPublicationId"),
            "ojs": x.get("noticeOjsNumber"),
            "procedure": x.get("contractFolderId"),
            "hilma_procedure": str(x["procedureId"]) if x.get("procedureId") else None,
            "hilma_project": str(x["oldProcurementProjectId"]) if x.get("oldProcurementProjectId") else None,
            "hilma_plan": str(x["planId"]) if x.get("planId") else None,
        }

        raw = {k: v for k, v in x.items() if not k.startswith("@")}
        return Notice(
            source=self.name,
            source_id=sid,
            url=url,
            title=title or f"Hilma {x.get('noticeNumber') or sid}",
            notice_type=ntype,
            status=status,
            description=description,
            buyer_name=buyer,
            buyer_id=(x.get("organisationNationalRegistrationNumber") or "").strip() or None,
            buyer_country=country,
            buyer_city=city,
            cpv_codes=list(dict.fromkeys(c for c in (normalize_cpv(s) for s in (x.get("cpvCodes") or "").split())
                                         if c)),
            procedure_type=x.get("procedureType") or None,
            published_at=_dt(x.get("datePublished")),
            deadline_at=deadline,
            value_amount=value,
            value_currency=currency or None,
            platform_url=platform,
            language=lang,
            documents=docs,
            refs={k: str(v) for k, v in refs.items() if v},
            raw=raw,
        )
