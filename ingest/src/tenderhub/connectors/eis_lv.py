"""EIS — Latvia's Electronic Procurement System (eis.gov.lv), e-tenders module ("EKEIS").

Every Latvian contracting authority publishes its procurements here, including the defence sector
(VALIC — Valsts aizsardzības loģistikas un iepirkumu centrs, VAMOIC, the National Armed Forces units)
under the Defence and Security Procurement Law (ADJIL).

Access: no API key, no login. The public supplier search page (https://www.eis.gov.lv/EKEIS/Supplier/)
calls a JSON endpoint its own frontend uses:

    POST /EKEIS/supplier/searchprocurements      (application/json)
    body  {"FormCode": "SUP.S01", "Paging": true, "From": <offset>,
           "SearchParameters": {"PublicationDateFrom": "YYYY-MM-DDT00:00:00", "From": <offset>, ...}}
    reply {"Success": true, "EventData": {"Hits": <total>, "Items": [... 20 procurements, newest first ...]}}

The POST needs the ASP.NET anti-forgery pair (cookie + `__RequestVerificationToken` header, both from a
GET of the search page) and an Origin/Referer on www.eis.gov.lv — otherwise it answers
`{"Success": false, "RedirectUrl": "/EKEIS/Error.html?..."}`.

The list rows carry id, identification number, title, buyer + registration number, announcement time,
submission deadline and status, but no CPV / procedure / legal act. Those come from the public HTML
detail page `/EKEIS/Supplier/Procurement/<id>` (labels keyed by `for=` attributes, ids in hidden inputs,
lots and additional CPVs in embedded `var X_items = [...]` JSON), fetched once per notice.
All times are naive Europe/Riga.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..connector import Checkpoint, Connector, StoredRef
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status, normalize_cpv

BASE = "https://www.eis.gov.lv"
SEARCH_PAGE = f"{BASE}/EKEIS/Supplier/"
SEARCH_API = f"{BASE}/EKEIS/supplier/searchprocurements"
DETAIL = f"{BASE}/EKEIS/Supplier/Procurement/{{id}}"
DETAIL_PREFIX = f"{BASE}/EKEIS/Supplier/Procurement/"
TZ = ZoneInfo("Europe/Riga")
DEFENCE_DIRECTIVE = "32009L0081"
DETAIL_CONCURRENCY = 8

# Procurement status: list rows give StatusCode, detail pages a hidden StatusId.
STATUS_IDS = {2: "ISL", 3: "NOS", 5: "IZB", 6: "PRT", 7: "PAT", 8: "LPN", 10: "LNS"}
STATUS = {
    "ISL": Status.OPEN,       # Izsludināts — announced, open for bids (closed once the deadline passed)
    "PAT": Status.CLOSED,     # Piedāvājumi atvērti — bids opened
    "NOS": Status.CLOSED,     # Noslēgts — procedure completed
    "LPN": Status.AWARDED,    # Lēmums pieņemts — award decision taken
    "LNS": Status.AWARDED,    # Līgums noslēgts — contract concluded
    "IZB": Status.CANCELLED,  # Izbeigts — terminated
    "PRT": Status.CANCELLED,  # Pārtraukts — discontinued
}
# RegulatoryActId (search classifier "ActId")
ACTS = {
    1: "PIL (spēka līdz 28.02.2017)",
    2: "SPSIL",   # Sabiedrisko pakalpojumu sniedzēju iepirkumu likums (utilities)
    3: "ADJIL",   # Aizsardzības un drošības jomas iepirkumu likums (defence & security, transposes 2009/81/EC)
    4: "PPPL",    # Publiskās un privātās partnerības likums
    5: "PIL",     # Publisko iepirkumu likums
    30: "AKS",    # Par ārkārtējo situāciju un izņēmuma stāvokli
}
CONSULTATION = re.compile(r"apspriede ar pieg|consultation with suppl", re.I)
_TOKEN = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
_REG_NO = re.compile(r",\s*re[gģ]\.\s*(?:numurs|number|nr\.?)\s*:\s*(\S+)\s*$", re.I)
_DMY = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")
_ITEMS = re.compile(r"var (\w+)_items = (\[.*?\]);\s*$", re.M)
_TED = re.compile(r"(\d{1,8}-20\d\d)")
_IUB = re.compile(r"/show/([0-9a-f-]{36})")
_MONEY = re.compile(r"(\d[\d\s]*(?:[.,]\d+)?)\s*([A-Z]{3})?")

PERSONAL = {"ContactList"}  # contact person names / e-mails: not needed, keep them out of `raw`
LIST_KEYS = (
    "ProcurementIdentifier", "ProcurementId", "ProcurementIdentificationNumber", "ProcurementTitle", "CustomerTitle",
    "CustomerRegistrationNumber", "ProcurementAnnouncementDate", "LastStagePlannedProposalSubmissionTime",
    "LastStageHistoricalProposalSubmissionTime", "LastAmendmentsDate", "StatusCode", "StatusTitle",
    "ProcurementStatusId", "IsPoster",
)


def _search_body(since_local: str, offset: int) -> dict[str, Any]:
    params = {
        "Mode": 1, "SearchEventCode": "SUP.810", "AdvancedSearch": False, "Keywords": None,
        "PublicationDateFrom": since_local, "PublicationDateUntil": None, "SearchStatusId": None,
        "ActId": None, "CpvIdList": [], "CpvTable": [], "From": offset,
    }
    return {"ListMode": 0, "Paging": True, "IsAllPublishedProcurements": False, "FormCode": "SUP.S01",
            "SearchParameters": params, "Results": [], "From": offset}


def _iso_local(v: str | None) -> datetime | None:
    """'2026-10-30T14:44:26' (naive Riga time) -> aware datetime."""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def _dmy(v: str | None, end_of_day: bool = False) -> datetime | None:
    """'16.11.2026 11:00 (plānots)' / '30.10.2026' -> aware Riga datetime."""
    m = _DMY.search(v or "")
    if not m:
        return None
    d, mo, y, h, mi = m.groups()
    if h is None:
        h, mi = (23, 59) if end_of_day else (0, 0)
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), tzinfo=TZ)
    except ValueError:
        return None


def _clean(v: str | None) -> str | None:
    v = re.sub(r"\s+", " ", v or "").strip()
    return v or None


def parse_detail(html: str) -> dict[str, Any] | None:
    """Detail page -> {"fields": {for-id: text}, "hidden": {id: value}, "<name>_items": [...]}.
    Returns None for the error page (unknown / removed procurement)."""
    tree = HTMLParser(html)
    fields: dict[str, str] = {}
    for g in tree.css("div.form-group"):
        lab = g.css_first("label")
        blk = g.css_first(".field-block")
        if not lab or not blk or len(g.css("div.form-group")) > 1:
            continue
        key = lab.attributes.get("for") or _clean(lab.text()) or ""
        texts = [n.text() for n in blk.css(".field-text")]
        own = blk.text(deep=False)  # e.g. the bare "490000.00" before the currency span
        val = _clean(" ".join([own, *texts]) if own and own.strip() else " ".join(texts))
        links = [a.attributes.get("href") for a in blk.css("a") if (a.attributes.get("href") or "").startswith("http")]
        if links and not val:
            val = links[0]
        if val and key not in fields:
            fields[key] = val
    hidden = {}
    for i in tree.css("input[type=hidden]"):
        k, v = i.attributes.get("id"), i.attributes.get("value")
        if k and v and k not in hidden:
            hidden[k] = v
    if not fields.get("IdentificationNumber") and not hidden.get("ProcurementIdentifier"):
        return None
    out: dict[str, Any] = {"fields": fields, "hidden": hidden}
    for name, payload in _ITEMS.findall(html):
        if name in ("SubjectDescription_CpvAdditionals", "Parts", "ActualDocuments"):
            try:
                out[name] = json.loads(payload)
            except ValueError:
                continue
    return out


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _value(v: str | None) -> tuple[float | None, str | None]:
    m = _MONEY.search(v or "")
    if not m:
        return None, None
    try:
        amount = float(re.sub(r"\s", "", m.group(1)).replace(",", "."))
    except ValueError:
        return None, None
    return (amount, m.group(2) or "EUR") if amount > 0 else (None, None)


class EisLv(Connector):
    name = "eis_lv"
    title = "EIS — Elektronisko iepirkumu sistēma (Latvia)"
    countries = ("LV",)
    homepage = "https://www.eis.gov.lv/EKEIS/Supplier/"
    kind = "portal"
    poll_interval = timedelta(hours=1)
    first_sync_lookback = timedelta(days=3)
    requests_per_second = 4.0
    supports_refresh = True
    _csrf: str | None = None

    async def _token(self) -> str:
        # The GET sets the session + anti-forgery cookies in the shared jar; the POST must echo the
        # matching token. Detail GETs below reuse that session (no explicit Cookie header), so the
        # server does not rotate it and one token serves the whole sync.
        self.http.client.cookies.set("Eis.Language", "LAT", domain="www.eis.gov.lv", path="/")
        html = await self.http.get_text(SEARCH_PAGE)
        m = _TOKEN.search(html)
        if not m:
            raise RuntimeError("EIS search page has no __RequestVerificationToken (layout changed?)")
        return m.group(1)

    async def _detail(self, pid: Any) -> dict[str, Any] | None:
        r = await self.http.get(DETAIL.format(id=pid))
        if "/Error" in r.url.path:
            return None
        return parse_detail(r.text)

    async def _search(self, day: str, offset: int) -> tuple[list[dict[str, Any]], int]:
        for attempt in range(2):
            if self._csrf is None:
                self._csrf = await self._token()
            headers = {"__RequestVerificationToken": self._csrf, "X-Requested-With": "XMLHttpRequest",
                       "Origin": BASE, "Referer": SEARCH_PAGE}
            data = await self.http.post_json(SEARCH_API, json=_search_body(day, offset), headers=headers)
            if data.get("Success"):
                ev = data.get("EventData") or {}
                return list(ev.get("Items") or []), int(ev.get("Hits") or 0)
            self._csrf = None  # expired / rotated session: fetch a fresh token once and retry
        raise RuntimeError(f"EIS search failed: {data.get('Message') or data.get('RedirectUrl')}")

    async def _notice(self, x: dict[str, Any], sem: asyncio.Semaphore) -> Notice:
        pid = x.get("ProcurementIdentifier") or x.get("ProcurementId")
        async with sem:
            try:
                detail = await self._detail(pid)
            except HttpError as e:  # one broken detail page should not lose the whole list row
                self.log.warning("EIS detail %s failed: %s", pid, e)
                detail = None
        return self.to_notice(x, detail)

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        since_local = since.astimezone(TZ)
        day = f"{since_local:%Y-%m-%d}T00:00:00"
        self._csrf = None
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)  # the shared rate limiter still caps req/s
        offset = 0
        page = asyncio.ensure_future(self._search(day, 0))
        try:
            while True:
                items, hits = await page
                fresh = [x for x in items if not ((p := _iso_local(x.get("ProcurementAnnouncementDate")))
                                                  and p < since)]
                offset += len(items)
                # newest first: once a page reaches rows older than `since`, the rest are older too
                done = not items or len(fresh) < len(items) or offset >= hits
                # pipeline: page N+1 of the list loads while page N's detail pages are fetched
                page = None if done else asyncio.ensure_future(self._search(day, offset))
                for n in await asyncio.gather(*(self._notice(x, sem) for x in fresh)):
                    yield n
                yield Checkpoint({"since": day, "offset": offset})
                if page is None:
                    break
        finally:
            if page is not None and not page.done():
                page.cancel()

    async def refresh(self, ref: StoredRef) -> Notice | None:
        detail = await self._detail(ref.source_id)
        if detail is None:
            return None
        return self.to_notice(ref.raw.get("list") or {"ProcurementIdentifier": ref.source_id}, detail)

    def to_notice(self, x: dict[str, Any], detail: dict[str, Any] | None = None) -> Notice:
        d = detail or {}
        f: dict[str, str] = d.get("fields") or {}
        h: dict[str, str] = d.get("hidden") or {}
        # ProcurementIdentifier is the stable id the site links with; ProcurementId is a version id
        # that changes whenever the procurement is amended
        pid = str(x.get("ProcurementIdentifier") or h.get("ProcurementIdentifier") or x.get("ProcurementId"))
        number = _clean(x.get("ProcurementIdentificationNumber")) or f.get("IdentificationNumber")
        title = _clean(x.get("ProcurementTitle")) or f.get("Name") or number or pid

        buyer, buyer_id = _clean(x.get("CustomerTitle")), _clean(x.get("CustomerRegistrationNumber"))
        org = f.get("OrganizerId")
        if org and (not buyer or not buyer_id):
            m = _REG_NO.search(org)
            buyer = buyer or _clean(org[: m.start()] if m else org)
            buyer_id = buyer_id or (m.group(1) if m else None)

        published = _iso_local(x.get("ProcurementAnnouncementDate")) or _dmy(f.get("FirstAnnouncementDate"))
        deadline = (_iso_local(x.get("LastStagePlannedProposalSubmissionTime"))
                    or _iso_local(x.get("LastStageHistoricalProposalSubmissionTime"))
                    or _dmy(f.get("stage_ProposalSubmissionDate"), end_of_day=True)
                    or _dmy(f.get("stage_SuppliersGathering"), end_of_day=True))

        # list rows are a snapshot from sync time; the detail page is fresher (used by refresh)
        code = STATUS_IDS.get(_int(h.get("StatusId")) or -1) or x.get("StatusCode") \
            or STATUS_IDS.get(_int(x.get("ProcurementStatusId")) or -1)
        status = STATUS.get(code or "", Status.UNKNOWN)
        if status is Status.OPEN and deadline and deadline < self.ctx.now():
            status = Status.CLOSED

        procedure = f.get("ProcurementRegulationId")
        act_id = _int(h.get("RegulatoryActId"))
        notice_type = NoticeType.PLAN if CONSULTATION.search(f"{procedure or ''} {number or ''}") else NoticeType.TENDER

        cpvs = [normalize_cpv(f.get("SubjectDescription_CpvMainId") or "")]
        cpvs += [normalize_cpv(c.get("Title") or "") for c in d.get("SubjectDescription_CpvAdditionals") or []]
        amount, currency = _value(f.get("SubjectDescription_EstimatedContractValue"))

        parts = [p.get("Title") for p in d.get("Parts") or [] if p.get("Title")]
        description = f.get("SubjectDescription_Description")
        if parts:
            lots = "; ".join(f"{i}. {t}" for i, t in enumerate(parts, 1))
            description = f"{description}\n\nDaļas: {lots}" if description else f"Daļas: {lots}"

        iub_url, eu_url = f.get("IubPublicationUrl"), f.get("EuPublicationUrl")
        iub_url = iub_url if (iub_url or "").startswith("http") else None
        eu_url = eu_url if (eu_url or "").startswith("http") else None
        docs = [Document(u, title=t, kind="notice") for u, t in ((iub_url, "IUB publikācija"),
                                                                    (eu_url, "ES OV publikācija")) if u]
        refs = {"eis": pid, "procedure": number}
        if x.get("ProcurementId") and str(x["ProcurementId"]) != pid:
            refs["eis_version"] = str(x["ProcurementId"])
        if iub_url and (m := _IUB.search(iub_url)):
            refs["iub"] = m.group(1)
        if eu_url and (m := _TED.search(eu_url)):
            refs["ted"] = m.group(1)

        raw_list = {k: x[k] for k in LIST_KEYS if k in x}
        if isinstance(x.get("TypeIcon"), dict):
            raw_list["Type"] = x["TypeIcon"].get("Title")
        raw_detail = {"fields": {k: v for k, v in f.items() if k not in PERSONAL},
                      "status_id": h.get("StatusId"), "act_id": h.get("RegulatoryActId"),
                      "object_type_id": h.get("SubjectDescription_ProcurementObjectTypeId"),
                      "regulation_id": h.get("ProcurementRegulationId"), "lots": parts,
                      "documents": [doc.get("Title") for doc in d.get("ActualDocuments") or [] if doc.get("Title")]}
        return Notice(
            source=self.name,
            source_id=pid,
            url=DETAIL.format(id=pid),
            title=title,
            notice_type=notice_type,
            status=status,
            description=description,
            buyer_name=buyer,
            buyer_id=buyer_id,
            buyer_country="LV",
            cpv_codes=list(dict.fromkeys(c for c in cpvs if c)),
            procedure_type=procedure,
            legal_basis=DEFENCE_DIRECTIVE if act_id == 3 else ACTS.get(act_id or -1),
            published_at=published,
            deadline_at=deadline,
            value_amount=amount,
            value_currency=currency,
            language="lv",
            documents=docs,
            refs={k: v for k, v in refs.items() if v},
            raw={"list": raw_list, "detail": raw_detail if detail else None},
        )
