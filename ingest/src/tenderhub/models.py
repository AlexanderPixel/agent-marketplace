"""Normalized data model every connector maps its source into."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class NoticeType(StrEnum):
    PLAN = "plan"                  # prior information notice, tender plan, market consultation
    TENDER = "tender"              # open call: suppliers can still bid
    AWARD = "award"                # contract award / procedure result
    MODIFICATION = "modification"  # corrigendum, amendment, contract modification
    CANCELLATION = "cancellation"
    OTHER = "other"


class Status(StrEnum):
    OPEN = "open"            # accepting bids
    CLOSED = "closed"        # deadline passed, no result yet
    AWARDED = "awarded"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class Document:
    url: str
    title: str | None = None
    kind: str | None = None  # e.g. "specification", "annex", "q&a"


@dataclass(slots=True)
class Notice:
    """One published procurement notice, normalized across sources.

    `source` + `source_id` is the identity: re-yielding the same pair updates the stored row.
    Datetimes must be timezone-aware. Country codes are ISO 3166-1 alpha-2 (PL, DE, ...).
    CPV codes are the 8-digit form without the check digit ("35300000", not "35300000-6").
    """

    source: str
    source_id: str
    url: str
    title: str
    notice_type: NoticeType = NoticeType.OTHER
    status: Status = Status.UNKNOWN
    description: str | None = None
    buyer_name: str | None = None
    buyer_id: str | None = None        # national registration id (NIP, SIREN, ...) when available
    buyer_country: str | None = None
    buyer_city: str | None = None
    cpv_codes: list[str] = field(default_factory=list)
    procedure_type: str | None = None
    legal_basis: str | None = None     # e.g. "32009L0081" for the defence directive
    published_at: datetime | None = None
    deadline_at: datetime | None = None
    value_amount: float | None = None
    value_currency: str | None = None
    platform_url: str | None = None    # where bids are submitted / documents live, if not `url`
    language: str | None = None        # ISO 639-1
    documents: list[Document] = field(default_factory=list)
    # identifiers that let us link the same procedure across sources/notices:
    # {"ocid": "...", "ted": "674227-2026", "bzp": "2026/BZP 00495214", "procedure": "..."}
    refs: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)  # trimmed source payload, JSON-serializable

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def content_hash(self) -> str:
        """Hash of the normalized fields (not `raw`) — a change here means the notice really changed."""
        d = self.to_dict()
        d.pop("raw")
        return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


_STR_FIELDS = ("source", "source_id", "url", "title", "description", "buyer_name", "buyer_id", "buyer_country",
               "buyer_city", "procedure_type", "legal_basis", "value_currency", "platform_url", "language")
_CPV = re.compile(r"^\d{8}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")


def normalize_cpv(code: str) -> str | None:
    """'35300000-6 (Broń...)' -> '35300000'. Returns None if no 8-digit code is present."""
    m = re.search(r"\d{8}", code or "")
    return m.group(0) if m else None


def absolute_url(u: str | None, base: str | None = None) -> str | None:
    """'www.x.pl/a' -> 'https://www.x.pl/a'; relative paths resolved against `base`; junk -> None."""
    from urllib.parse import urljoin, urlparse

    u = (u or "").strip()
    if not u or " " in u:
        return None
    if u.startswith("//"):
        u = "https:" + u
    elif base and not urlparse(u).scheme:
        u = urljoin(base, u)
    elif not urlparse(u).scheme:
        u = "https://" + u
    p = urlparse(u)
    return u if p.scheme in ("http", "https") and "." in (p.hostname or "") else None


def validate_notice(n: Notice) -> list[str]:
    """Contract checks every connector's output must pass. Returns a list of problems (empty = valid)."""
    problems = []
    for name in _STR_FIELDS:
        v = getattr(n, name)
        if v is not None and not isinstance(v, str):
            problems.append(f"{name} must be str or None, got {type(v).__name__}: {v!r:.80}")
    if n.value_amount is not None and not isinstance(n.value_amount, (int, float)):
        problems.append(f"value_amount must be a number: {n.value_amount!r}")
    if not all(isinstance(d, Document) and d.url.startswith(("http://", "https://")) for d in n.documents):
        problems.append("documents must be Document objects with absolute http(s) urls")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in n.refs.items()):
        problems.append("refs must map str -> str")
    if problems:
        return problems
    if not n.source_id:
        problems.append("source_id is empty")
    if not n.title or not n.title.strip():
        problems.append("title is empty")
    if not n.url.startswith(("http://", "https://")):
        problems.append(f"url is not absolute http(s): {n.url!r}")
    if n.buyer_country and not _COUNTRY.match(n.buyer_country):
        problems.append(f"buyer_country must be ISO alpha-2: {n.buyer_country!r}")
    for c in n.cpv_codes:
        if not _CPV.match(c):
            problems.append(f"cpv code must be 8 digits: {c!r}")
    for name in ("published_at", "deadline_at"):
        v = getattr(n, name)
        if v is not None and (not isinstance(v, datetime) or v.tzinfo is None):
            problems.append(f"{name} must be a timezone-aware datetime: {v!r}")
    if not isinstance(n.notice_type, NoticeType):
        problems.append(f"notice_type must be NoticeType: {n.notice_type!r}")
    if not isinstance(n.status, Status):
        problems.append(f"status must be Status: {n.status!r}")
    try:
        json.dumps(n.raw, default=str)
    except (TypeError, ValueError) as e:
        problems.append(f"raw is not JSON-serializable: {e}")
    return problems
