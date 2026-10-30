"""EU Funding & Tenders Portal — EU institution tenders and European Defence Fund (EDF / EDIP / ASAP) calls.

The portal's own frontend reads everything from the public SEDIA search API (anonymous, the `apiKey=SEDIA`
query parameter is the portal's public key, not a secret):

    POST https://api.tech.ec.europa.eu/search-api/prod/rest/search?apiKey=SEDIA&text=***&pageSize=100&pageNumber=1
    multipart/form-data parts (each `application/json`):
      query         {"bool": {"must": [{"terms": {"type": ["0"]}},
                                       {"range": {"esDA_IngestDate": {"gte": "2026-10-27T00:00:00.000+0000"}}}]}}
      languages     ["en"]
      sort          {"field": "identifier", "order": "ASC"}       (unique -> stable paging)
      displayFields ["identifier", "title", ...]                   (limits the ~40 kB/record payload)

Every metadata value comes back as a list of strings; nested structures are JSON-encoded strings.
Record types: 0 = tender (EU institutions, agencies, delegations), 1 = call-for-proposals topic,
8 = cascade-funding (FSTP) call, 2 = external-action prospect, 6 = news item.
Status codes: 31094501 forthcoming, 31094502 open for submission, 31094503 closed.
`esDA_IngestDate` is the portal's last-change timestamp, so it drives incremental sync.

Speed: the search passes (open defence calls, changed defence calls, changed tenders) run concurrently,
and once page 1 reveals `totalResults` the remaining pages of a pass are fetched concurrently too
(bounded by MAX_IN_FLIGHT; the shared rate limiter still spaces request starts). Results are yielded in
pass order, with a checkpoint after each pass.

Scope: all tenders (type 0) plus calls of the defence programmes (EDF incl. EDIP / ASAP calls, and the
legacy EDIDP); the many civil grant programmes (Horizon, LIFE, Erasmus, ...) are not procurement and are left out.

Quirk: tender `deadlineDate` is the *local* wall-clock time in `cftTimezone` mislabelled as +0000
(verified against TED: '2026-12-01T15:00:59.000+0000' == TED '2026-12-01 15:00:59+01:00').
Call deadlines are usually date-only ('...T00:00:00.000+0000'); EU calls close at 17:00 Brussels time.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse
from selectolax.lexbor import LexborHTMLParser

from ..connector import Checkpoint, Connector, StoredRef
from ..models import Notice, NoticeType, Status, absolute_url, normalize_cpv

API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
PORTAL = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities"
PAGE_SIZE = 100          # API maximum used by the portal itself; same latency as 50
MAX_IN_FLIGHT = 6        # concurrent page requests per pass
BRUSSELS = ZoneInfo("Europe/Brussels")

TYPE_TENDER, TYPE_TOPIC, TYPE_CASCADE = "0", "1", "8"
FORTHCOMING, OPEN, CLOSED = "31094501", "31094502", "31094503"

# frameworkProgramme codes of the defence programmes (labels from the portal's facet endpoint)
DEFENCE_PROGRAMMES = {
    "44181033": ("EDF", "European Defence Fund (EDF)"),
    "31109727": ("EDIDP", "European Defence Industrial Development Programme (EDIDP - 2014-2020)"),
}

# tender procedureType codes -> label (portal facet endpoint); RELEX tenders use the symbolic codes
PROCEDURE_TYPES = {
    "47396202": "Call for Expression of Interest (CEI) Preselection of Candidates",
    "47396204": "Call for Expression of Interest (CEI) Vendor's List",
    "47396206": "Competitive dialogue",
    "47396208": "Competitive procedure with negotiation",
    "47396212": "Dynamic purchasing system",
    "47396214": "Planned negotiated procedure for middle/low value contract",
    "47396220": "Open procedure",
    "47396222": "Planned call for tenders with OJ S publication",
    "47396224": "Restricted procedure",
    "47396226": "Other procedure",
    "OPEN": "Open procedure",
    "RESTRICTED_PROCEDURE": "Restricted procedure",
    "NEGOTIATED_COMPETITIVE": "Competitive procedure with negotiation",
    "NEGOCIATED": "Negotiated procedure with contract notice",
    "COMPETITIVE_DIALOGUE": "Competitive dialogue",
    "INNOVATION_PARTNERSHIP": "Innovation partnership",
    "PRIOR_INFORMATION_NOTICE": "Planned call for tenders with OJ S publication",
    "EX_ANTE_PUBLICITY": "Planned negotiated procedure for middle/low value contract",
    "CALL_EXPRESSION_INTEREST_PC": "Call for Expression of Interest (CEI) Preselection of Candidates",
    "CALL_EXPRESSION_INTEREST_VL": "Call for Expression of Interest (CEI) Vendor's List",
}
PLAN_PROCEDURES = {"47396222", "PRIOR_INFORMATION_NOTICE"}
CONTRACT_TYPES = {"31095498": "services", "31095499": "supplies", "31095501": "works"}

FIELDS = [
    # common
    "identifier", "title", "type", "status", "startDate", "deadlineDate", "esDA_IngestDate",
    "esDA_FirstIngestDate", "url", "callIdentifier", "frameworkProgramme", "description", "ccm2Id",
    # tenders
    "cftId", "mainCpv", "additionalCpvs", "cftLeadContractingAuthorityCode", "caName", "cftContractNoticeLink",
    "cftEstimatedOverallContractAmount", "cftEstimatedOverallContractCurrency", "cftEstimatedTotalProcedureValue",
    "procedureType", "contractType", "cftTimezone", "cftEXARegistrationDeadline", "cftSubmissionMethodCode",
    "cftPartyLegalEntityId", "placesOfDeliveryOrPerformance", "cftPublicationDateEForm", "cftPlannedDate",
    # calls
    "callTitle", "typesOfAction", "deadlineModel", "descriptionByte", "budgetOverview", "budget",
    "projectAcronym", "projectName", "projectId",
]
RAW_DROP = {"description", "descriptionByte", "budgetOverview"}
MAX_DESCRIPTION = 4000


def _first(m: dict[str, Any], key: str) -> str | None:
    v = m.get(key)
    if isinstance(v, list):
        v = v[0] if v else None
    return v if v not in ("", None) else None


def _json(v: str | None) -> Any:
    try:
        return json.loads(v) if v else None
    except ValueError:
        return None


def _unique(xs: Any) -> list[str]:
    return list(dict.fromkeys(x for x in xs or [] if x))


def _text(v: str | None) -> str | None:
    """Plain text from a (possibly HTML) description, trimmed."""
    if not v:
        return None
    if "<" in v:
        v = LexborHTMLParser(v).text(separator=" ")
    v = re.sub(r"\s+", " ", v).strip()
    return v[:MAX_DESCRIPTION] or None


def _ts(v: str | None) -> datetime | None:
    """Real timestamps such as '2026-10-30T05:02:48.068+0100'."""
    if not v:
        return None
    try:
        return isoparse(v)
    except ValueError:
        return None


def _wall(v: str | None, tz: ZoneInfo) -> datetime | None:
    """'2026-12-01T15:00:59.000+0000' whose clock time is really local to `tz` -> aware datetime in `tz`."""
    t = _ts(v)
    return t.replace(tzinfo=None).replace(tzinfo=tz) if t else None


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name) if name else BRUSSELS
    except (KeyError, ValueError):
        return BRUSSELS


def _published(m: dict[str, Any]) -> datetime | None:
    """Earliest of the publication/opening date (a calendar date in Brussels) and the first ingest time."""
    candidates = []
    start = _ts(_first(m, "startDate"))
    if start:
        candidates.append(datetime.combine(start.date(), time(), BRUSSELS))
    first = _ts(_first(m, "esDA_FirstIngestDate"))
    if first:
        candidates.append(first)
    return min(candidates) if candidates else None


def _call_deadline(values: list[str] | None, kind: str, now: datetime) -> datetime | None:
    """Next upcoming cut-off of a call (else the last one). Date-only cut-offs close at 17:00 Brussels."""
    out = []
    for v in values or []:
        t = _wall(v, BRUSSELS)
        if t is None:
            continue
        if t.time() == time():
            t = t.replace(hour=17) if kind == TYPE_TOPIC else t.replace(hour=23, minute=59)
        out.append(t)
    if not out:
        return None
    out.sort()
    upcoming = [t for t in out if t >= now]
    return upcoming[0] if upcoming else out[-1]


def _status(code: str | None, deadline: datetime | None, now: datetime) -> Status:
    if code == CLOSED or (deadline and deadline < now):
        return Status.CLOSED  # the portal flips status lazily; a passed deadline is authoritative
    if code == OPEN:
        return Status.OPEN
    return Status.UNKNOWN  # forthcoming (announced, not yet accepting submissions) or unknown


def _topic_budget(m: dict[str, Any], identifier: str) -> float | None:
    """Sum of the yearly budget of this topic's own action rows (shared call budgets are not attributed)."""
    overview = _json(_first(m, "budgetOverview")) or {}
    total, found = 0.0, False
    for rows in (overview.get("budgetTopicActionMap") or {}).values():
        for row in rows or []:
            if (row.get("action") or "").split(" - ")[0].strip() != identifier:
                continue
            for amount in (row.get("budgetYearMap") or {}).values():
                try:
                    total += float(amount)
                    found = True
                except (TypeError, ValueError):
                    pass
    return total if found and total > 0 else None


def _range_since(since: datetime) -> dict[str, Any]:
    return {"range": {"esDA_IngestDate": {"gte": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000+0000")}}}


def tender_query(since: datetime) -> dict[str, Any]:
    return {"bool": {"must": [{"terms": {"type": [TYPE_TENDER]}}, _range_since(since)]}}


def calls_query(since: datetime) -> dict[str, Any]:
    return {"bool": {"must": [{"terms": {"type": [TYPE_TOPIC, TYPE_CASCADE]}},
                              {"terms": {"frameworkProgramme": list(DEFENCE_PROGRAMMES)}},
                              _range_since(since)]}}


def open_calls_query(now: datetime) -> dict[str, Any]:
    """Defence calls currently open or forthcoming (first run: they may not have changed recently)."""
    return {"bool": {"must": [{"terms": {"type": [TYPE_TOPIC, TYPE_CASCADE]}},
                              {"terms": {"frameworkProgramme": list(DEFENCE_PROGRAMMES)}},
                              {"terms": {"status": [FORTHCOMING, OPEN]}},
                              {"range": {"deadlineDate": {"gte": now.astimezone(UTC).strftime(
                                  "%Y-%m-%dT00:00:00.000+0000")}}}]}}


class EuFundingTenders(Connector):
    name = "eu_funding_tenders"
    title = "EU Funding & Tenders Portal — EU institution tenders and EDF / EDIP / ASAP calls"
    countries = ("EU",)
    homepage = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/home"
    kind = "aggregator"
    poll_interval = timedelta(hours=2)        # tenders land daily ~05:00 CET, amendments during the day
    first_sync_lookback = timedelta(days=7)
    requests_per_second = 2.0                 # official API; requests overlap, starts are spaced 0.5 s
    supports_refresh = True
    page_size = PAGE_SIZE

    async def _search(self, query: dict[str, Any], page: int, page_size: int | None = None) -> dict[str, Any]:
        files = {
            "query": ("blob", json.dumps(query), "application/json"),
            "languages": ("blob", json.dumps(["en"]), "application/json"),
            "sort": ("blob", json.dumps({"field": "identifier", "order": "ASC"}), "application/json"),
            "displayFields": ("blob", json.dumps(FIELDS), "application/json"),
        }
        params = {"apiKey": "SEDIA", "text": "***", "pageSize": page_size or self.page_size, "pageNumber": page}
        return await self.http.post_json(API, params=params, files=files)

    async def _collect(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        """All results of a query: page 1, then the remaining pages concurrently (sorted by identifier ASC,
        so pages are stable and disjoint)."""
        first = await self._search(query, 1)
        results = list(first.get("results") or [])
        total = int(first.get("totalResults") or 0)
        if not results or len(results) >= total:
            return results
        pages = range(2, math.ceil(total / self.page_size) + 1)
        sem = asyncio.Semaphore(MAX_IN_FLIGHT)

        async def fetch(page: int) -> list[dict[str, Any]]:
            async with sem:
                return (await self._search(query, page)).get("results") or []

        for chunk in await asyncio.gather(*(fetch(p) for p in pages)):
            results.extend(chunk)
        return results

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        now = self.ctx.now()
        bootstrapped = bool(state.get("bootstrapped"))
        passes = [] if bootstrapped else [("open_calls", open_calls_query(now))]
        passes += [("calls", calls_query(since)), ("tenders", tender_query(since))]
        # start every pass at once; consume them in order so checkpoints stay meaningful
        tasks = [(label, asyncio.ensure_future(self._collect(query))) for label, query in passes]
        try:
            for label, task in tasks:
                for r in await task:
                    if (n := self.to_notice(r, now)) is not None:
                        yield n
                bootstrapped = bootstrapped or label == "open_calls"
                yield Checkpoint({"bootstrapped": bootstrapped, "last_pass": label})
        finally:
            for _, task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*(t for _, t in tasks), return_exceptions=True)

    async def refresh(self, ref: StoredRef) -> Notice | None:
        kind = ref.raw.get("type")
        if kind == TYPE_CASCADE and ref.raw.get("ccm2Id"):
            must = [{"terms": {"ccm2Id": [ref.raw["ccm2Id"]]}}]
        else:
            must = [{"terms": {"identifier": [ref.raw.get("identifier") or ref.source_id]}}]
        must.append({"terms": {"type": [kind or TYPE_TENDER]}})
        data = await self._search({"bool": {"must": must}}, 1, page_size=10)
        now = self.ctx.now()
        notices = [n for r in data.get("results") or [] if (n := self.to_notice(r, now)) and n.source_id == ref.source_id]
        notices.sort(key=lambda n: n.raw.get("esDA_IngestDate") or "")
        return notices[-1] if notices else None

    # ------------------------------------------------------------------ mapping

    def to_notice(self, r: dict[str, Any], now: datetime | None = None) -> Notice | None:
        now = now or datetime.now(UTC)
        m = r.get("metadata") or {}
        kind = _first(m, "type")
        if kind == TYPE_TENDER:
            return self._tender(r, m, now)
        if kind in (TYPE_TOPIC, TYPE_CASCADE):
            return self._call(r, m, kind, now)
        return None

    def _raw(self, r: dict[str, Any], m: dict[str, Any]) -> dict[str, Any]:
        raw = {k: (v[0] if isinstance(v, list) and len(v) == 1 else v) for k, v in m.items()
               if k not in RAW_DROP and v not in ([], None, "")}
        raw["reference"] = r.get("reference")
        return raw

    def _tender(self, r: dict[str, Any], m: dict[str, Any], now: datetime) -> Notice | None:
        identifier = _first(m, "identifier") or _first(m, "cftId") or r.get("reference")
        title = _first(m, "title") or r.get("summary")
        if not identifier or not title:
            return None
        url = _first(m, "url") or r.get("url") or ""
        if not url.startswith("http") or url.endswith(".json"):
            url = f"{PORTAL}/tender-details/{identifier}"
        tz = _zone(_first(m, "cftTimezone"))
        suffix = identifier.rsplit("-", 1)[-1]
        proc = _first(m, "procedureType")
        if suffix == "PIN" or proc in PLAN_PROCEDURES:
            notice_type = NoticeType.PLAN
        elif suffix in ("CN", "EXA") or proc:
            notice_type = NoticeType.TENDER
        else:
            notice_type = NoticeType.OTHER

        deadline = _wall(_first(m, "deadlineDate"), tz)
        if deadline is None and (exa := _first(m, "cftEXARegistrationDeadline")):
            deadline = _ts(exa.split(" ")[0])       # '2026-10-26T23:59:59+02:00 Europe/Vilnius'

        authorities = _json(_first(m, "cftLeadContractingAuthorityCode")) or []
        lead = next((a for a in authorities if a.get("isLeadAuthority")), authorities[0] if authorities else {})
        buyer = (lead.get("name") or "").strip() or None
        if not buyer and (ca := _first(m, "caName")) and ca != title:
            buyer = ca                              # older external-action tenders: 'DEVCO', ...

        cpvs = list(m.get("mainCpv") or [])
        for extra in _json(_first(m, "additionalCpvs")) or []:
            cpvs.append(extra.get("mainCode") if isinstance(extra, dict) else extra)
        amount, currency = _first(m, "cftEstimatedOverallContractAmount"), _first(m, "cftEstimatedOverallContractCurrency")
        if not amount and (total := _first(m, "cftEstimatedTotalProcedureValue")):
            parts = total.split()
            amount, currency = parts[0], parts[1] if len(parts) > 1 else currency
        try:
            value = float(amount) if amount else None
        except ValueError:
            value = None

        link = _json(_first(m, "cftContractNoticeLink")) or []
        ted = link[0] if link and isinstance(link[0], str) and re.fullmatch(r"\d+-\d{4}", link[0]) else None
        refs = {"sedia": r.get("reference") or identifier, "procedure": _first(m, "callIdentifier"), "ted": ted,
                "oj": link[1] if len(link) > 1 and isinstance(link[1], str) else None,
                "party_id": _first(m, "cftPartyLegalEntityId")}
        proc_label = PROCEDURE_TYPES.get(proc or "", proc)
        return Notice(
            source=self.name,
            source_id=identifier,
            url=url,
            title=title.strip(),
            notice_type=notice_type,
            status=_status(_first(m, "status"), deadline, now) if notice_type is not NoticeType.PLAN else Status.UNKNOWN,
            description=_text(_first(m, "description")),
            buyer_name=buyer,
            cpv_codes=_unique(normalize_cpv(c) for c in cpvs if c),
            procedure_type=proc_label,
            published_at=_published(m),
            deadline_at=deadline,
            value_amount=value,
            value_currency=currency if value is not None else None,
            platform_url=absolute_url(lead.get("link")) if lead.get("link") else None,
            refs={k: v for k, v in refs.items() if v},
            raw=self._raw(r, m),
        )

    def _call(self, r: dict[str, Any], m: dict[str, Any], kind: str, now: datetime) -> Notice | None:
        identifier = _first(m, "identifier")
        title = _first(m, "title") or r.get("summary")
        if not identifier or not title:
            return None
        code = _first(m, "frameworkProgramme")
        short, programme = DEFENCE_PROGRAMMES.get(code or "", (code, None))
        instrument = next((p for p in ("EDIP", "ASAP") if identifier.startswith(f"EDF-{p}-")), short)
        deadline = _call_deadline(m.get("deadlineDate"), kind, now)
        refs = {"programme": short, "instrument": instrument, "call": _first(m, "callIdentifier"),
                "sedia": r.get("reference")}
        if kind == TYPE_TOPIC:
            source_id = identifier
            url = f"{PORTAL}/topic-details/{identifier}"
            buyer = f"European Commission — {programme}" if programme else "European Commission"
            actions = _first(m, "typesOfAction")
            procedure = f"Call for proposals: {actions}" if actions else "Call for proposals"
            description = _text(_first(m, "descriptionByte") or _first(m, "description"))
            value = _topic_budget(m, identifier)
            refs["topic"] = identifier
        else:
            ccm2 = _first(m, "ccm2Id") or r.get("reference")
            source_id = f"cascade-{ccm2}"
            url = _first(m, "url") or r.get("url") or f"{PORTAL}/competitive-calls-cs/{ccm2}"
            acronym, project = _first(m, "projectAcronym"), _first(m, "projectId")
            funded_by = f"{programme} project {project}" if programme and project else programme or project
            buyer = f"{acronym} consortium ({funded_by})" if acronym and funded_by else acronym
            procedure = "Cascade funding call (financial support to third parties)"
            description = _text(_first(m, "description"))
            try:
                value = float(_first(m, "budget") or "") or None
            except ValueError:
                value = None
            refs.update({"topic": identifier, "project": project})
        return Notice(
            source=self.name,
            source_id=source_id,
            url=url,
            title=title.strip(),
            notice_type=NoticeType.TENDER,
            status=_status(_first(m, "status"), deadline, now),
            description=description,
            buyer_name=buyer,
            procedure_type=procedure,
            published_at=_published(m),
            deadline_at=deadline,
            value_amount=value,
            value_currency="EUR" if value is not None else None,
            language="en",
            refs={k: v for k, v in refs.items() if v},
            raw=self._raw(r, m),
        )
