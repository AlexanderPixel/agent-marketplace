"""Bekanntmachungsservice (oeffentlichevergabe.de) — Germany's national eForms notice data service.

Every notice published by a German buyer (federal, Länder, municipalities, sector entities, BAAINBw/
Bundeswehr) above and below the EU thresholds goes through this service. It offers an anonymous open-data
export: one zip per publication day with one eForms XML file per notice.

    GET https://oeffentlichevergabe.de/api/notice-exports?pubDay=YYYY-MM-DD&format=eforms.zip
        -> notices_YYYY-MM-DD_EFORMS.zip  ({noticeId}-{version}.xml, entry mtime = publication time, CET/CEST)
        -> 400 "must lie in the past" for today / future days; ETag + If-None-Match -> 304 supported
    GET https://oeffentlichevergabe.de/api/notices/{noticeId}?format=eforms[&noticeVersion=01]  (one notice)

Speed: the export days are downloaded concurrently (a small sliding window of in-flight days, still
behind the shared rate limiter) and each zip is parsed in a worker thread, so downloading day N+1,
parsing day N and storing day N-1 overlap. Notices are still yielded strictly in day order, so every
Checkpoint is a safe resume point. XPath expressions are compiled once (per thread) and reused.

The OCDS variant of the export (format=ocds.zip) has no tender deadlines, so we parse the eForms XML.
National below-threshold notices (UVgO/VOB/A, numeric ids, eforms-sdk-0.1) use the same UBL namespaces
but a reduced structure (buyer name inline in ContractingParty, often no organisation list).
"""

from __future__ import annotations

import asyncio
import io
import re
import threading
import zipfile
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from lxml import etree

from ..connector import Checkpoint, Connector, StoredRef
from ..countries import iso3_to_iso2, lang3_to_lang2
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status, absolute_url, normalize_cpv

BASE = "https://oeffentlichevergabe.de"
EXPORT = f"{BASE}/api/notice-exports"
NOTICE_API = f"{BASE}/api/notices"
BERLIN = ZoneInfo("Europe/Berlin")
ETAGS_KEPT = 14  # days of export ETags remembered in the checkpoint
WINDOW = 4       # export days downloaded/parsed ahead of the consumer (bounds memory and politeness)

NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}

NOTICE_TYPES = {  # cbc:NoticeTypeCode/@listName (eForms "form type")
    "planning": NoticeType.PLAN,
    "consultation": NoticeType.PLAN,
    "competition": NoticeType.TENDER,
    "result": NoticeType.AWARD,
    "dir-awa-pre": NoticeType.AWARD,   # voluntary ex-ante transparency (announced direct award)
    "change": NoticeType.MODIFICATION,
    "cont-modif": NoticeType.MODIFICATION,
}

# root-level eForms extension; fixed child steps instead of `//` scans (by local name: prefixes vary)
EFX = ("*[local-name()='UBLExtensions']/*[local-name()='UBLExtension']/*[local-name()='ExtensionContent']"
       "/*[local-name()='EformsExtension']")

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, remove_blank_text=True)


def notice_url(notice_id: str, version: str | None = None) -> str:
    """Human-readable notice page in the Bekanntmachungsservice UI."""
    url = f"{BASE}/ui/de/search/details?noticeId={notice_id}"
    return f"{url}&noticeVersion={version}" if version else url


# --- small XML helpers ---------------------------------------------------------------------------

def _clean(s: str | None) -> str | None:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s or None


_XPATHS = threading.local()  # compiled XPath objects, one cache per thread (parsing runs in workers)


def _x(el: Any, path: str, **variables: Any) -> list[Any]:
    if el is None:
        return []
    cache = _XPATHS.__dict__.setdefault("c", {})
    xp = cache.get(path)
    if xp is None:
        xp = cache[path] = etree.XPath(path, namespaces=NS)
    return xp(el, **variables)


def _t(el: Any, path: str) -> str | None:
    """First non-empty text at `path`."""
    for v in _x(el, path):
        s = _clean(v if isinstance(v, str) else v.text)
        if s:
            return s
    return None


def _unique(xs: Any) -> list[str]:
    return list(dict.fromkeys(x for x in xs if x))


_PLACEHOLDER = re.compile(r"^[Xx0\s./-]*$|^(?i:n/?a|keine|unbekannt|none)$")  # 'XXXX', '0000', 'n/a'
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(Z|[+-]\d{2}:\d{2})?$")
_TIME = re.compile(r"^(\d{2}:\d{2}(?::\d{2})?)(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$")


def _tz(offset: str | None) -> Any:
    if not offset:
        return None
    if offset == "Z":
        return UTC
    sign = 1 if offset[0] == "+" else -1
    h, m = offset[1:].split(":")
    return timezone(sign * timedelta(hours=int(h), minutes=int(m)))


def _datetime(d: str | None, t: str | None) -> datetime | None:
    """eForms date '2026-11-27+01:00' + time '08:00:00+01:00' -> aware datetime.

    A date without time means end of that day; naive values are taken as German local time."""
    dm = _DATE.match(_clean(d) or "")
    if not dm:
        return None
    day = date.fromisoformat(dm.group(1))
    tm = _TIME.match(_clean(t) or "")
    if tm:
        clock = time.fromisoformat(tm.group(1))
        tz = _tz(tm.group(2)) or _tz(dm.group(2)) or BERLIN
    else:
        clock = time(23, 59)
        tz = _tz(dm.group(2)) or BERLIN
    return datetime.combine(day, clock, tzinfo=tz)


def _amount(el: Any, path: str) -> tuple[float | None, str | None]:
    for v in _x(el, path):
        try:
            amount = float((v.text or "").strip())
        except ValueError:
            continue
        if amount > 0:
            return amount, v.get("currencyID")
    return None, None


# --- connector -----------------------------------------------------------------------------------

class Oeffentlichevergabe(Connector):
    name = "oeffentlichevergabe"
    title = "Bekanntmachungsservice (oeffentlichevergabe.de)"
    countries = ("DE",)
    homepage = "https://oeffentlichevergabe.de"
    kind = "portal"
    poll_interval = timedelta(hours=6)        # one export per publication day, finalised after midnight
    first_sync_lookback = timedelta(days=3)
    requests_per_second = 2.0             # a few large static zips per run, served with ETags
    supports_refresh = True

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        etags: dict[str, str] = dict(state.get("etags") or {})
        start = since.astimezone(BERLIN).date()
        last = self.ctx.now().astimezone(BERLIN).date() - timedelta(days=1)  # today's export does not exist yet
        days = [start + timedelta(days=i) for i in range((last - start).days + 1)]
        if not days:
            yield Checkpoint({"etags": etags, **({"last_day": state["last_day"]} if "last_day" in state else {})})
            return
        known = dict(etags)  # If-None-Match values, read-only for the workers

        # sliding window: up to WINDOW days in flight (download + threaded parse), consumed in day order
        pending = deque(days)
        inflight: deque[tuple[date, asyncio.Task]] = deque()

        def launch() -> None:
            while pending and len(inflight) < WINDOW:
                d = pending.popleft()
                inflight.append((d, asyncio.create_task(self._fetch_day(d, known.get(d.isoformat())))))

        try:
            launch()
            while inflight:
                day, task = inflight.popleft()
                result = await task
                if result is None:  # day not closed yet (clock skew around midnight): stop here
                    break
                notices, etag = result
                launch()  # keep the window full while the runner stores this day
                for n in notices:
                    yield n
                if etag:
                    etags[day.isoformat()] = etag
                etags = dict(sorted(etags.items())[-ETAGS_KEPT:])
                yield Checkpoint({"last_day": day.isoformat(), "etags": etags})
        finally:
            for _, t in inflight:
                t.cancel()
            await asyncio.gather(*(t for _, t in inflight), return_exceptions=True)

    async def _fetch_day(self, day: date, etag: str | None) -> tuple[list[Notice], str | None] | None:
        """Download and parse one daily export. Returns ([], None) for 304/404/empty, None if not available yet."""
        headers = {"If-None-Match": etag} if etag else {}
        try:
            r = await self.http.get(EXPORT, params={"pubDay": day.isoformat(), "format": "eforms.zip"},
                                    headers=headers)
        except HttpError as e:
            if e.status == 400 and "past" in e.body:
                return None
            if e.status == 404:
                return [], None
            raise
        if r.status_code == 304 or not r.content:
            return [], None
        notices = await asyncio.to_thread(self.parse_export, r.content, day)
        return notices, r.headers.get("etag")

    def parse_export(self, content: bytes, pub_day: date) -> list[Notice]:
        """All notices of one daily export zip, in publication order (later versions win on dedup)."""
        day_start = datetime.combine(pub_day, time(0, 0), tzinfo=BERLIN)
        out = []
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            entries = sorted((i for i in zf.infolist() if i.filename.endswith(".xml")), key=lambda i: i.date_time)
            for info in entries:
                published = max(datetime(*info.date_time, tzinfo=BERLIN), day_start)
                try:
                    root = etree.fromstring(zf.read(info), _PARSER)
                except etree.XMLSyntaxError as e:
                    self.log.warning("skipping unparsable %s (%s)", info.filename, e)
                    continue
                n = self.to_notice(root, published=published, pub_day=pub_day.isoformat(), entry=info.filename)
                if n is not None:
                    out.append(n)
        return out

    async def refresh(self, ref: StoredRef) -> Notice | None:
        """Latest version of one notice (picks up re-published versions of the same notice id)."""
        notice_id = ref.refs.get("notice") or ref.source_id
        try:
            r = await self.http.get(f"{NOTICE_API}/{notice_id}", params={"format": "eforms"})
        except HttpError as e:
            if e.status in (404, 410):
                return None
            raise
        root = etree.fromstring(r.content, _PARSER)
        published = None
        if p := (ref.raw or {}).get("published_at"):
            published = datetime.fromisoformat(p)
        return self.to_notice(root, published=published, pub_day=(ref.raw or {}).get("pub_day"),
                              entry=(ref.raw or {}).get("entry"))

    def to_notice(self, root: Any, *, published: datetime | None, pub_day: str | None = None,
                  entry: str | None = None) -> Notice | None:
        notice_id = _t(root, "cbc:ID")
        if not notice_id:
            return None
        version = _t(root, "cbc:VersionID")
        procedure_id = _t(root, "cbc:ContractFolderID")
        type_el = next(iter(_x(root, "cbc:NoticeTypeCode")), None)
        form_type = type_el.get("listName") if type_el is not None else None
        notice_code = _clean(type_el.text) if type_el is not None else None
        notice_type = NOTICE_TYPES.get(form_type or "", NoticeType.OTHER)
        subtype = _t(root, f"{EFX}/*[local-name()='NoticeSubType']/*[local-name()='SubTypeCode']")
        regulatory = _t(root, "cbc:RegulatoryDomain")
        lang3 = _t(root, "cbc:NoticeLanguageCode")

        project = next(iter(_x(root, "cac:ProcurementProject")), None)
        lots = _x(root, "cac:ProcurementProjectLot")
        lot_projects = [p for lot in lots for p in _x(lot, "cac:ProcurementProject")]
        title = _t(project, "cbc:Name") or next((t for p in lot_projects if (t := _t(p, "cbc:Name"))), None)
        description = _t(project, "cbc:Description")

        # buyer: ContractingParty -> organisation id -> efac:Organization/efac:Company (or inline party)
        party = next(iter(_x(root, "cac:ContractingParty/cac:Party")), None)
        org_id = _t(party, "cac:PartyIdentification/cbc:ID")
        company = None
        if org_id:
            company = next(iter(_x(
                root, f"{EFX}/*[local-name()='Organizations']/*[local-name()='Organization']/*[local-name()='Company']"
                "[cac:PartyIdentification/cbc:ID=$oid]", oid=org_id)), None)
        buyer_el = company if company is not None else party
        buyer_name = _t(buyer_el, "cac:PartyName/cbc:Name")
        buyer_id = _t(buyer_el, "cac:PartyLegalEntity/cbc:CompanyID")
        if buyer_id and _PLACEHOLDER.match(buyer_id):
            buyer_id = None
        buyer_city = _t(buyer_el, "cac:PostalAddress/cbc:CityName")
        country3 = (_t(buyer_el, "cac:PostalAddress/cac:Country/cbc:IdentificationCode")
                    or _t(project, "cac:RealizedLocation/cac:Address/cac:Country/cbc:IdentificationCode"))
        buyer_country = iso3_to_iso2(country3) or "DE"

        cpv_path = ("cac:MainCommodityClassification/cbc:ItemClassificationCode"
                    " | cac:AdditionalCommodityClassification/cbc:ItemClassificationCode")
        cpvs = _unique(normalize_cpv(c.text or "") for p in [project, *lot_projects] for c in _x(p, cpv_path)
                       if (c.get("listName") or "cpv") == "cpv")

        deadlines = [d for lot in lots for d in [
            _datetime(_t(lot, "cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod/cbc:EndDate"),
                      _t(lot, "cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod/cbc:EndTime"))
            or _datetime(_t(lot, "cac:TenderingProcess/cac:ParticipationRequestReceptionPeriod/cbc:EndDate"),
                         _t(lot, "cac:TenderingProcess/cac:ParticipationRequestReceptionPeriod/cbc:EndTime"))]
            if d]
        deadline = min(deadlines) if deadlines else None

        value, currency = _amount(project, "cac:RequestedTenderTotal/cbc:EstimatedOverallContractAmount")
        if value is None:
            value, currency = _amount(root, f"{EFX}/*[local-name()='NoticeResult']/cbc:TotalAmount")
        if value is None:
            lot_values = [_amount(p, "cac:RequestedTenderTotal/cbc:EstimatedOverallContractAmount")
                          for p in lot_projects]
            lot_values = [v for v in lot_values if v[0] is not None]
            if lot_values and len({c for _, c in lot_values}) == 1:
                value, currency = sum(v for v, _ in lot_values), lot_values[0][1]

        docs = _unique(absolute_url(_clean(u.text)) for lot in lots for u in _x(
            lot, "cac:TenderingTerms/cac:CallForTendersDocumentReference/cac:Attachment/cac:ExternalReference/cbc:URI"))
        submit = _unique(absolute_url(_clean(u.text)) for lot in lots
                         for u in _x(lot, "cac:TenderingTerms/cac:TenderRecipientParty/cbc:EndpointID"))

        if notice_type is NoticeType.AWARD:
            status = Status.AWARDED
        elif notice_type is NoticeType.TENDER and deadline is not None:
            status = Status.OPEN if deadline > self.ctx.now() else Status.CLOSED
        else:
            status = Status.UNKNOWN

        changed = _t(root, f"{EFX}/*[local-name()='Changes']/*[local-name()='ChangedNoticeIdentifier']")
        ted = _t(root, f"{EFX}/*[local-name()='Publication']/*[local-name()='NoticePublicationID']")
        if ted and ted.startswith("00000000"):
            ted = None
        refs = {
            "notice": notice_id,
            "notice_version": version,
            "procedure": procedure_id,
            "ocid": f"ocds-mnwr74-{procedure_id or notice_id}",
            "ted": ted,
            "changed_notice": changed,
        }
        raw = {
            "notice_id": notice_id,
            "version": version,
            "procedure_id": procedure_id,
            "form_type": form_type,
            "notice_type_code": notice_code,
            "notice_subtype": subtype,
            "regulatory_domain": regulatory,
            "customization": _t(root, "cbc:CustomizationID"),
            "issue_date": _t(root, "cbc:IssueDate"),
            "procedure_code": _t(root, "cac:TenderingProcess/cbc:ProcedureCode"),
            "contract_nature": _t(project, "cbc:ProcurementTypeCode"),
            "lots": [_t(p, "cbc:Name") for p in lot_projects][:20],
            "published_at": published.isoformat() if published else None,
            "pub_day": pub_day,
            "entry": entry,
        }
        return Notice(
            source=self.name,
            source_id=notice_id,
            url=notice_url(notice_id, version),
            title=title or notice_id,
            notice_type=notice_type,
            status=status,
            description=description,
            buyer_name=buyer_name,
            buyer_id=buyer_id,
            buyer_country=buyer_country,
            buyer_city=buyer_city,
            cpv_codes=cpvs,
            procedure_type=_t(root, "cac:TenderingProcess/cbc:ProcedureCode"),
            legal_basis=regulatory,
            published_at=published,
            deadline_at=deadline,
            value_amount=value,
            value_currency=currency,
            platform_url=next(iter(submit), None) or next(iter(docs), None),
            language=lang3_to_lang2(lang3),
            documents=[Document(u, kind="procurement_documents") for u in docs],
            refs={k: v for k, v in refs.items() if v},
            raw={k: v for k, v in raw.items() if v},
        )
