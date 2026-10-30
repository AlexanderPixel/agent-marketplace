"""platformazakupowa.pl (Open Nexus) — Poland's largest e-procurement platform, thousands of buyers incl. military units.

The platform's own pages (https://platformazakupowa.pl/transakcja/<id>) redirect anonymous HTTP clients to an
SSO login, but Open Nexus runs a public tender search on top of it (platformaofertowa.pl) whose JSON API is
anonymous. Filtering that API with `provider=pz` returns exactly the platformazakupowa.pl proceedings:

  POST https://api.platformaofertowa.pl/tenders/search/best-match      search (max 50 per page)
  GET  https://api.platformaofertowa.pl/tenders/tender/<uuid>          one tender (same record shape)
  GET  https://api.platformaofertowa.pl/tenders/tender/<uuid>/attachments   attachment list
  GET  https://api.platformaofertowa.pl/tenders/attachment/download/<attachment id>   public file download

Offset paging over the search is unstable (deep pages repeat/skip items), so `sync` uses keyset paging on the
offer deadline instead: sort by deadline ascending, filter `deadline >= last seen deadline`. A run of more than one
page of tenders sharing the same deadline is paged by offset inside that single instant (which is stable).
The deadline axis is cut into independent segments that are walked concurrently (all requests still go through
the shared rate limiter). `publication_date` is a ">= day" filter; each record's `createdAt` is the time the
search index last (re)ingested it, so it is used to yield only records new or changed since `since`.

Attachments need one request per tender, and the API answers 429 above ~4 requests/second, so they are only
fetched by `refresh` (open tenders), not during `sync`.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..connector import Checkpoint, Connector, StoredRef
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status, absolute_url, normalize_cpv

API = "https://api.platformaofertowa.pl"
SEARCH = f"{API}/tenders/search/best-match"
TENDER = f"{API}/tenders/tender/{{id}}"
ATTACHMENTS = f"{API}/tenders/tender/{{id}}/attachments"
DOWNLOAD = f"{API}/tenders/attachment/download/{{id}}"
TRANSACTION = "https://platformazakupowa.pl/transakcja/{id}"

PROVIDER = "pz"
PAGE_SIZE = 50                       # the API caps `limit` at 50
SORT = "submitting_offers_deadline_asc"
# Active tenders are walked in deadline segments (days from now) concurrently; most deadlines fall within 3 weeks.
SEGMENT_DAYS: tuple[int, ...] = (2, 4, 7, 11, 16, 30)
MIN_DEADLINE = datetime(2000, 1, 1, tzinfo=UTC)
MAX_DEADLINE = datetime(2100, 1, 1, tzinfo=UTC)
MAX_PAGES_PER_SEGMENT = 1000
WARSAW = ZoneInfo("Europe/Warsaw")
SECOND = timedelta(seconds=1)
INGEST_LAG = timedelta(hours=6)      # observed createdAt - publicationDate on first ingest: under 2 hours

STATUS = {1: Status.OPEN, 2: Status.CLOSED}
PROCEDURE = {  # `procedure` field: which regime the proceeding falls under
    "union": "above EU thresholds",
    "domestic": "national (BZP)",
    "internal": "below PZP threshold (internal rules)",
}
_BZP = re.compile(r"\b(20\d\d)\s*/?\s*BZP\s*[_ ]?(\d{8})")
_TED = re.compile(r"\b(20\d\d)/S\s*\d{3}-0*(\d{5,7})\b|\b(?:TED|Dz\.\s*U(?:rz)?\.\s*UE)[^0-9]{0,40}(\d{5,7}-20\d\d)\b")
# market-price estimations / market research (no contract follows directly) -> PLAN; "rozeznanie cenowe" is a real RFQ
_ESTIMATE = re.compile(r"szacowani\w* wartości|\bszacowanie\b|rozeznani\w* rynku|dialog\w* techniczn", re.IGNORECASE)
_WS = re.compile(r"\s+")
DESCRIPTION_MAX = 2000


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = isoparse(v)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=WARSAW)


def _iso(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _floor(d: datetime) -> datetime:
    return d.astimezone(UTC).replace(microsecond=0)


def _html_text(v: str | None) -> str | None:
    if not v:
        return None
    text = _WS.sub(" ", HTMLParser(v).text(separator=" ")).strip()
    if len(text) > DESCRIPTION_MAX:
        text = text[:DESCRIPTION_MAX].rstrip() + " …"
    return text or None


def find_refs(*texts: str | None) -> dict[str, str]:
    """BZP / TED notice numbers mentioned in titles, descriptions, case numbers or attachment names."""
    refs: dict[str, str] = {}
    for t in texts:
        if not t:
            continue
        if "bzp" not in refs and (m := _BZP.search(t)):
            refs["bzp"] = f"{m.group(1)}/BZP {m.group(2)}"
        if "ted" not in refs and (m := _TED.search(t)):
            refs["ted"] = f"{m.group(2)}-{m.group(1)}" if m.group(1) else m.group(3)
    return refs


def changed_at(x: dict[str, Any]) -> datetime | None:
    """When the record last entered the index (createdAt is bumped on re-ingest, e.g. status changes)."""
    ds = [d for d in (_dt(x.get("createdAt")), _dt(x.get("versionDate")), _dt(x.get("publicationDate"))) if d]
    return max(ds) if ds else None


class Platformazakupowa(Connector):
    name = "platformazakupowa"
    title = "platformazakupowa.pl (Open Nexus)"
    countries = ("PL",)
    homepage = "https://platformazakupowa.pl"
    kind = "platform"
    poll_interval = timedelta(hours=1)
    overlap = timedelta(hours=3)              # records carry their own ingest timestamp; a short overlap suffices
    first_sync_lookback = timedelta(days=3)
    requests_per_second = 2.0                 # the API starts answering 429 at ~4 req/s
    supports_refresh = True

    # ------------------------------------------------------------------ sync

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        # publication_date filters ">= that day"; INGEST_LAG covers records indexed a while after publication
        pub_from = (since.astimezone(WARSAW) - INGEST_LAG).date().isoformat()
        now = _floor(self.ctx.now())
        cuts = [now + timedelta(days=d) for d in SEGMENT_DAYS]
        bounds = [MIN_DEADLINE, *cuts, MAX_DEADLINE]
        segments = [(1, lo, hi - SECOND) for lo, hi in zip(bounds, bounds[1:], strict=False)]
        segments[-1] = (1, segments[-1][1], MAX_DEADLINE)
        segments.append((0, MIN_DEADLINE, MAX_DEADLINE))   # inactive (deadline passed) proceedings

        queue: asyncio.Queue[list[dict[str, Any]] | Exception | None] = asyncio.Queue()

        async def run(active: int, lo: datetime, hi: datetime) -> None:
            try:
                async for batch in self._walk(pub_from, active, lo, hi):
                    await queue.put(batch)
                await queue.put(None)
            except Exception as e:  # noqa: BLE001 — re-raised by the consumer
                await queue.put(e)

        tasks = [asyncio.create_task(run(*s)) for s in segments]
        seen: set[str] = set()
        try:
            pending = len(tasks)
            while pending:
                item = await queue.get()
                if item is None:
                    pending -= 1
                    continue
                if isinstance(item, Exception):
                    raise item
                for x in item:
                    tid = x.get("id")
                    if not tid or tid in seen or x.get("provider", PROVIDER) != PROVIDER:
                        continue
                    seen.add(tid)
                    changed = changed_at(x)
                    if changed is not None and changed < since:
                        continue
                    yield self.to_notice(x)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        yield Checkpoint({"publication_from": pub_from})

    async def _search(self, pub_from: str, active: int, lo: datetime, hi: datetime, offset: int = 0) -> dict[str, Any]:
        body = {
            "query": "",
            "filters": {
                "provider": PROVIDER,
                "publication_date": pub_from,
                "submitting_offers_deadline_from": _iso(lo),
                "submitting_offers_deadline_to": _iso(hi),
            },
            "limit": PAGE_SIZE,
            "offset": offset,
            "active_status": active,
            "sort_by": SORT,
        }
        return await self.http.post_json(SEARCH, json=body)

    async def _walk(self, pub_from: str, active: int, lo: datetime, hi: datetime) -> AsyncIterator[list[dict]]:
        """Keyset-page one deadline segment [lo, hi] (both inclusive, whole seconds)."""
        cur = lo
        for _ in range(MAX_PAGES_PER_SEGMENT):
            if cur > hi:
                return
            data = await self._search(pub_from, active, cur, hi)
            tenders = data.get("tenders") or []
            total = (data.get("pagination") or {}).get("total_count") or 0
            yield tenders
            if not tenders or len(tenders) >= total or len(tenders) < PAGE_SIZE:
                return
            deadlines = [d for d in (_dt(t.get("submittingOffersDeadline")) for t in tenders) if d]
            last = _floor(max(deadlines)) if deadlines else cur
            if last > cur:
                cur = last          # inclusive: tenders at `last` not yet returned come back on the next page
                continue
            # a full page all due at the same instant: page that instant by offset, then move past it
            offset = 0
            while True:
                page = await self._search(pub_from, active, cur, cur, offset)
                batch = page.get("tenders") or []
                yield batch
                offset += len(batch)
                if not batch or offset >= ((page.get("pagination") or {}).get("total_count") or 0):
                    break
            cur = cur + SECOND
        self.log.warning("segment %s..%s (active=%s) not exhausted after %d pages", lo, hi, active,
                         MAX_PAGES_PER_SEGMENT)

    # --------------------------------------------------------------- refresh

    async def refresh(self, ref: StoredRef) -> Notice | None:
        uuid = (ref.raw or {}).get("id")
        if not uuid:
            return None
        try:
            x = await self.http.get_json(TENDER.format(id=uuid))
        except HttpError as e:
            if e.status in (404, 410):
                return None
            raise
        if not isinstance(x, dict) or not x.get("id"):
            return None
        attachments = await self.http.get_json(ATTACHMENTS.format(id=uuid))
        return self.to_notice(x, attachments if isinstance(attachments, list) else [])

    # ---------------------------------------------------------------- mapping

    def to_notice(self, x: dict[str, Any], attachments: list[dict[str, Any]] | None = None) -> Notice:
        extra = x.get("sourceExtraData") or {}
        external_id = extra.get("externalId")
        source_id = str(external_id) if external_id else x["id"]
        url = absolute_url(x.get("websiteUrl")) or (TRANSACTION.format(id=external_id) if external_id else None) \
            or TENDER.format(id=x["id"])
        title = _WS.sub(" ", x.get("title") or "").strip() or x.get("referenceNumber") or source_id
        description = _html_text(x.get("description"))

        issuer = (x.get("issuers") or [{}])[0] or {}
        address = issuer.get("address") or {}
        tax_id = issuer.get("taxId") or ""
        country = tax_id[:2] if re.match(r"^[A-Z]{2}\d", tax_id) else "PL"

        status = STATUS.get(x.get("status"), Status.UNKNOWN)
        if x.get("hasResult") or x.get("tenderResult"):
            status = Status.AWARDED
        head = f"{title} {(description or '')[:600]}"
        notice_type = NoticeType.PLAN if _ESTIMATE.search(head) else NoticeType.TENDER

        documents: list[Document] = []
        names: list[str] = []
        for a in attachments or []:
            if not a.get("id") or a.get("fileExists") is False:
                continue
            names.append(a.get("originalFileName") or "")
            documents.append(Document(DOWNLOAD.format(id=a["id"]), title=a.get("originalFileName"),
                                      kind="attachment"))

        reference = (x.get("referenceNumber") or "").strip()
        refs = find_refs(reference, title, x.get("description"), *names)
        if reference:
            refs["procedure"] = reference

        raw = {k: v for k, v in x.items() if k not in ("description", "highlight", "score", "accuracy",
                                                       "isReferenceMatch", "results")}
        if attachments is not None:
            raw["attachments"] = [{k: a.get(k) for k in ("id", "originalFileName", "mimeType", "fileSize",
                                                         "publishedAt")} for a in attachments]
        return Notice(
            source=self.name,
            source_id=source_id,
            url=url,
            title=title,
            notice_type=notice_type,
            status=status,
            description=description,
            buyer_name=(issuer.get("title") or "").strip() or None,
            buyer_id=issuer.get("nip") or re.sub(r"^[A-Z]{2}", "", tax_id) or None,
            buyer_country=country,
            buyer_city=(address.get("city") or "").strip() or None,
            cpv_codes=list(dict.fromkeys(c for c in (normalize_cpv(str(v)) for v in x.get("cpvCodes") or []) if c)),
            procedure_type=PROCEDURE.get(x.get("procedure") or "", x.get("procedure")),
            published_at=_dt(x.get("publicationDate")),
            deadline_at=_dt(x.get("submittingOffersDeadline")),
            language="pl",
            documents=documents,
            refs=refs,
            raw=raw,
        )
