"""UK Contracts Finder — below-threshold (and some above-threshold) UK public contracts, published as OCDS.

Public, anonymous OCDS search API (no key):
  GET https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search
      ?publishedFrom=<ISO datetime>&limit=<1..100>      (publishedTo defaults to "now")
Releases come newest-first; the next page is `links.next` (an opaque `cursor`), absent on the last page.
Each release id is "<notice guid>-<release number>"; the human page is /Notice/<notice guid> and the latest
release of one notice is at /Published/Notice/releases/<notice guid>.json (used by `refresh`).

The service rate-limits hard (HTTP 429 with `Retry-After: 120` after ~15 quick requests), so we go slowly.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from dateutil.parser import isoparse

from ..connector import Checkpoint, Connector, StoredRef
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status, absolute_url, normalize_cpv

BASE = "https://www.contractsfinder.service.gov.uk"
SEARCH = f"{BASE}/Published/Notices/OCDS/Search"
RELEASE = BASE + "/Published/Notice/releases/{guid}.json"
NOTICE_URL = BASE + "/Notice/{guid}"
PAGE_SIZE = 100  # API maximum

# OCDS release tags -> notice type. Updates/amendments are new releases of the *same* notice (same guid, so the
# same source_id): they keep that notice's type, the stored row is simply updated (e.g. a deadline extension).
TAGS: dict[str, NoticeType] = {
    "planning": NoticeType.PLAN,
    "planningUpdate": NoticeType.PLAN,
    "tender": NoticeType.TENDER,
    "tenderUpdate": NoticeType.TENDER,
    "tenderAmendment": NoticeType.TENDER,
    "tenderCancellation": NoticeType.CANCELLATION,
    "award": NoticeType.AWARD,
    "awardUpdate": NoticeType.AWARD,
    "awardCancellation": NoticeType.CANCELLATION,
    "contract": NoticeType.AWARD,
    "contractUpdate": NoticeType.AWARD,
    "contractAmendment": NoticeType.AWARD,
    "contractTermination": NoticeType.CANCELLATION,
}

_RELEASE_ID = re.compile(r"^(?P<guid>[0-9a-fA-F-]{36})-(?P<num>\d+)$")


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = isoparse(v)
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def notice_guid(release_id: str) -> str:
    """'fe5875f1-...-aaf615b6f5f4-919751' -> 'fe5875f1-...-aaf615b6f5f4'."""
    m = _RELEASE_ID.match(release_id or "")
    return m.group("guid") if m else release_id


def _unique(xs: Any) -> list[Any]:
    return list(dict.fromkeys(x for x in xs if x))


def _cpvs(tender: dict[str, Any]) -> list[str]:
    codes: list[str | None] = []

    def add(c: Any) -> None:
        if isinstance(c, dict) and (c.get("scheme") or "CPV").upper() == "CPV":
            codes.append(normalize_cpv(str(c.get("id") or "")))

    add(tender.get("classification"))
    for c in tender.get("additionalClassifications") or []:
        add(c)
    for item in tender.get("items") or []:
        add(item.get("classification"))
        for c in item.get("additionalClassifications") or []:
            add(c)
    return _unique(codes)


def _value(tender: dict[str, Any], awards: list[dict[str, Any]], prefer_awards: bool) -> dict[str, Any]:
    """Estimated value of the tender, or (for award notices) the total awarded value."""
    vals = [a.get("value") or {} for a in awards]
    amounts = [v["amount"] for v in vals if isinstance(v.get("amount"), (int, float))]
    awarded = {"amount": sum(amounts), "currency": next((v.get("currency") for v in vals if v.get("currency")), None)}
    estimated = tender.get("value") or tender.get("minValue") or {}
    if prefer_awards and amounts:
        return awarded
    if isinstance(estimated.get("amount"), (int, float)):
        return estimated
    return awarded if amounts else {}


def _city(v: str | None) -> str | None:
    v = (v or "").strip()
    return (v.title() if v.isupper() else v) or None


def _procedure(tender: dict[str, Any]) -> str | None:
    """'Open procedure (below threshold)'; placeholders like 'Not specified' / 'Other - ' fall back to the method."""
    details = (tender.get("procurementMethodDetails") or "").strip().rstrip("- ").strip()
    if details and details.lower() not in ("not specified", "other", "not applicable"):
        return details
    return tender.get("procurementMethod") or None


def _status(tag: str, tender: dict[str, Any], awards: list[dict[str, Any]]) -> Status:
    s = (tender.get("status") or "").lower()
    if s == "cancelled" or "Cancellation" in tag:
        return Status.CANCELLED
    if awards and any((a.get("status") or "active") in ("active", "pending") for a in awards):
        return Status.AWARDED
    if s == "active":
        return Status.OPEN
    if s in ("complete", "unsuccessful", "withdrawn"):
        return Status.CLOSED
    return Status.UNKNOWN


class ContractsFinder(Connector):
    name = "contracts_finder"
    title = "Contracts Finder (UK)"
    countries = ("GB",)
    homepage = "https://www.contractsfinder.service.gov.uk"
    kind = "portal"
    poll_interval = timedelta(hours=1)
    first_sync_lookback = timedelta(days=3)
    requests_per_second = 0.2          # 429 + Retry-After: 120 after ~15 requests in a minute
    supports_refresh = True

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        published_from = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        url: str | None = SEARCH
        params: dict[str, Any] | None = {"publishedFrom": published_from, "limit": PAGE_SIZE}
        pages = 0
        seen: set[str] = set()   # newest-first: an older release of a notice already yielded must not overwrite it
        while url:
            data = await self.http.get_json(url, params=params)
            pages += 1
            releases = data.get("releases") or []
            for r in releases:
                n = self.to_notice(r)
                if n is not None and n.source_id not in seen:
                    seen.add(n.source_id)
                    yield n
            nxt = (data.get("links") or {}).get("next")
            if not nxt or not releases or nxt == url:
                break
            url, params = nxt, None   # `next` already carries every query parameter + cursor
        yield Checkpoint({"published_from": published_from, "pages": pages})

    async def refresh(self, ref: StoredRef) -> Notice | None:
        try:
            data = await self.http.get_json(RELEASE.format(guid=ref.source_id))
        except HttpError as e:
            if e.status == 404:
                return None
            raise
        releases = (data.get("releases") or []) if isinstance(data, dict) else []
        return self.to_notice(releases[-1]) if releases else None

    def to_notice(self, r: dict[str, Any]) -> Notice | None:
        rid = r.get("id") or ""
        guid = notice_guid(rid)
        tender = r.get("tender") or {}
        title = (tender.get("title") or "").strip()
        if not guid or not title:
            return None
        tags = r.get("tag") or []
        tag = tags[0] if tags else ""
        awards = r.get("awards") or []
        parties = r.get("parties") or []
        buyer_ref = r.get("buyer") or {}
        buyer = next((p for p in parties if p.get("id") and p.get("id") == buyer_ref.get("id")), None) \
            or next((p for p in parties if "buyer" in (p.get("roles") or [])), None) or {}
        address = buyer.get("address") or {}

        notice_type = TAGS.get(tag, NoticeType.OTHER)
        value = _value(tender, awards, prefer_awards=notice_type is NoticeType.AWARD)
        amount = value.get("amount")

        self_url = NOTICE_URL.format(guid=guid)
        docs: list[Document] = []
        raw_docs = ((r.get("planning") or {}).get("documents") or []) + (tender.get("documents") or []) \
            + [d for a in awards for d in a.get("documents") or []]
        for d in raw_docs:
            u = absolute_url(d.get("url"))
            if u and u.rstrip("/") != self_url and u not in {x.url for x in docs}:
                docs.append(Document(u, title=d.get("title") or d.get("description"), kind=d.get("documentType")))
        # where suppliers actually respond: an external e-tendering portal linked from the notice, if any
        platform = next((d.url for d in docs if not d.url.startswith(BASE)), None)

        published = (
            _dt(tender.get("datePublished"))
            or _dt(next((a.get("datePublished") for a in awards if a.get("datePublished")), None))
            or _dt(r.get("date"))
        )
        refs = {k: v for k, v in {
            "ocid": r.get("ocid"),
            "release": rid,
            "procedure": tender.get("id"),
        }.items() if isinstance(v, str) and v}

        return Notice(
            source=self.name,
            source_id=guid,
            url=self_url,
            title=title,
            notice_type=notice_type,
            status=_status(tag, tender, awards),
            description=(tender.get("description") or "").replace("\r\n", "\n").strip() or None,
            buyer_name=(buyer_ref.get("name") or buyer.get("name") or "").strip() or None,
            buyer_id=buyer.get("id") or buyer_ref.get("id") or None,
            buyer_country="GB",
            buyer_city=_city(address.get("locality")),
            cpv_codes=_cpvs(tender),
            procedure_type=_procedure(tender),
            published_at=published,
            deadline_at=_dt((tender.get("tenderPeriod") or {}).get("endDate")),
            value_amount=float(amount) if isinstance(amount, (int, float)) and amount > 0 else None,
            value_currency=value.get("currency") if isinstance(amount, (int, float)) and amount > 0 else None,
            platform_url=platform,
            language=(r.get("language") or "en")[:2].lower(),
            documents=docs,
            refs=refs,
            raw=r,
        )
